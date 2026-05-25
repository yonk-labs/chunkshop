"""Per-language comment + docstring extraction.

Surfaces inline rationale that lives inside function bodies — exactly the
content ``symbol_aware`` embeds *alongside* the code, which is where the
benchmark loss for "rationale-in-comments" retrieval comes from. Lifting
comments out into their own KB lets a query like
"why did we pick batch_size=64?" hit the comment that explains it, not the
function that uses the value.

Public surface::

    from chunkshop.codeparse.comments import extract_comments, CommentBlock

    blocks = extract_comments(path=Path("foo.py"), language="python")
    for b in blocks:
        print(b.kind, b.start_line, b.text[:60])

Per CLAUDE.md "lazy imports": the Python path uses stdlib ``tokenize`` +
``ast`` only. Other languages use regex with a simple state machine that
tracks string-literal context so ``//`` inside a string doesn't get picked
up as a comment.

Per-language coverage:

    python      stdlib tokenize + ast.get_docstring (module/class/function)
    java        regex: // line, /* */ block; string-aware
    javascript  regex: // line, /* */ block; string-aware
    typescript  regex: // line, /* */ block; string-aware
    go          regex: // line, /* */ block; string-aware
    c / cpp     regex: // line, /* */ block; string-aware
    rust        regex: // line, //! //? doc, /* */ block; string-aware
    sql         regex: -- line, /* */ block; string-aware
    shell       regex: # line (no block form)
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional


CommentKind = Literal["line", "block", "docstring"]


@dataclass(frozen=True)
class CommentBlock:
    """One comment region from a source file.

    ``text`` is the comment content with delimiters stripped (``# ``,
    ``// ``, ``/* */``, triple-quote). ``kind="docstring"`` is reserved
    for the Python path where ``ast.get_docstring`` confirms a
    module/class/function docstring — regex-only languages always emit
    ``"line"`` or ``"block"``.

    Multi-line line-comment regions (consecutive ``#`` / ``//`` lines)
    are returned as ONE ``CommentBlock`` with ``kind="line"``, not as
    N blocks. That's the natural unit for "comments-as-docs" — readers
    write a paragraph as a stack of ``# `` lines and expect it to land
    in the KB as one document, not N.
    """
    text: str
    start_line: int
    end_line: int
    kind: CommentKind
    language: str
    # Optional context (set by the Python path when the comment is a
    # docstring attached to a function/class — None for the module
    # docstring, the symbol's qualified name otherwise).
    symbol: Optional[str] = None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
}


def detect_language(path: Path) -> Optional[str]:
    """Map a file extension to a language tag this module knows about.

    Returns ``None`` for unknown extensions — the caller decides whether
    that's a skip or an error (the comment_extracts source treats it as
    a skip).
    """
    return _EXT_TO_LANG.get(path.suffix.lower())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_comments(
    *,
    path: Path,
    language: Optional[str] = None,
    text: Optional[str] = None,
    skip_pragmas: bool = True,
) -> list[CommentBlock]:
    """Extract comment blocks from one source file.

    Parameters
    ----------
    path :
        File to parse. Used for extension-based language detection when
        ``language`` is None; also used to keep error messages traceable.
    language :
        Override language tag. Pass when the extension lies (``.txt``
        containing Python, etc.). Unknown languages return ``[]``.
    text :
        Optional pre-read source text. When ``None`` the function reads
        ``path`` itself. Pass the text when the caller already has it in
        hand to avoid a second filesystem hit.
    skip_pragmas :
        Drop pragma-style lines (shebangs, encoding declarations,
        ``# type:`` hints, ``# noqa`` / ``# pylint:`` / ``# pragma:``,
        Rust ``#![...]`` inner attrs and JS ``// @ts-ignore``-style
        directives). These are tooling chatter, not prose-for-retrieval.
    """
    lang = language or detect_language(path)
    if lang is None:
        return []
    if text is None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

    if lang == "python":
        return _extract_python(text=text, skip_pragmas=skip_pragmas)
    if lang in ("java", "javascript", "typescript", "go", "c", "cpp", "rust"):
        return _extract_c_style(text=text, language=lang, skip_pragmas=skip_pragmas)
    if lang == "sql":
        return _extract_sql(text=text, skip_pragmas=skip_pragmas)
    if lang == "shell":
        return _extract_shell(text=text, skip_pragmas=skip_pragmas)
    return []


# ---------------------------------------------------------------------------
# Python: stdlib tokenize + ast
# ---------------------------------------------------------------------------


# Patterns that mark a comment as "pragma / tooling chatter" — dropped
# when skip_pragmas=True. Kept conservative: a comment that *contains*
# ``noqa`` mid-sentence is not a pragma; only comments whose *first
# non-space token* matches one of these is.
_PYTHON_PRAGMA_PREFIXES = (
    "noqa",
    "type:",
    "pylint:",
    "pyright:",
    "mypy:",
    "ruff:",
    "pragma:",
    "fmt:",
    "isort:",
    "coding:",
    "encoding:",
    "-*-",  # legacy ``# -*- coding: utf-8 -*-``
)


def _is_python_pragma_comment(text: str) -> bool:
    """A bare ``# noqa`` / ``# type: ignore`` / ``# pragma: no cover`` etc.

    The comment text passed in here is delimiter-stripped (no leading
    ``#``). We split on whitespace and look at the first token.
    """
    s = text.strip()
    if not s:
        return False
    first = s.split(None, 1)[0].lower()
    return any(first.startswith(p) for p in _PYTHON_PRAGMA_PREFIXES)


def _extract_python(*, text: str, skip_pragmas: bool) -> list[CommentBlock]:
    """Pull ``#`` comments (grouped) + module/class/function docstrings.

    Two passes:

    1. ``tokenize.generate_tokens`` for ``COMMENT`` tokens. Adjacent
       comments on consecutive lines (no code in between) get merged
       into one CommentBlock with ``kind="line"`` — that's the natural
       unit when someone writes a paragraph of ``#`` lines.
    2. ``ast.parse`` + ``ast.get_docstring`` for module / class /
       function docstrings, emitted with ``kind="docstring"`` and
       ``symbol`` set to the qualified name (None for module).
    """
    blocks: list[CommentBlock] = []

    # --- Pass 1: line comments via tokenize ---
    # tokenize raises TokenizeError / IndentationError on malformed
    # source; we want to degrade gracefully ("best effort") rather than
    # crash the whole ingest because one .py file is broken.
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []

    # Skip shebangs (``#!...``). A shebang lives at col 0 on a line whose
    # only non-whitespace content is the shebang itself — we don't restrict
    # to line 1 because the file's first physical line may be blank and the
    # shebang sits on line 2.
    comment_tokens = [t for t in toks if t.type == tokenize.COMMENT]
    if skip_pragmas:
        comment_tokens = [
            t for t in comment_tokens
            if not (t.start[1] == 0 and t.string.startswith("#!"))
        ]

    # Group consecutive ``#`` comments — same column, no blank line and
    # no other code token between them. We approximate "no code between"
    # by checking the line numbers are strictly contiguous.
    grouped: list[list[tokenize.TokenInfo]] = []
    for t in comment_tokens:
        # delimiter-stripped text for the pragma check
        body = t.string.lstrip("#").lstrip(" \t")
        if skip_pragmas and _is_python_pragma_comment(body):
            continue
        if grouped and grouped[-1][-1].start[0] + 1 == t.start[0]:
            grouped[-1].append(t)
        else:
            grouped.append([t])

    for group in grouped:
        lines = [tok.string.lstrip("#").rstrip("\n") for tok in group]
        # Strip one leading space when present so ``# foo`` -> ``foo``
        # but ``#foo`` -> ``foo`` and ``#  foo`` -> ``` foo``.
        lines = [ln[1:] if ln.startswith(" ") else ln for ln in lines]
        body = "\n".join(lines).strip()
        if not body:
            continue
        blocks.append(
            CommentBlock(
                text=body,
                start_line=group[0].start[0],
                end_line=group[-1].end[0],
                kind="line",
                language="python",
            )
        )

    # --- Pass 2: docstrings via ast ---
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None

    if tree is not None:
        # Module docstring
        mod_doc = ast.get_docstring(tree, clean=False)
        if mod_doc is not None:
            first_stmt = tree.body[0] if tree.body else None
            if isinstance(first_stmt, ast.Expr) and isinstance(
                first_stmt.value, ast.Constant
            ):
                start_line = first_stmt.lineno
                end_line = getattr(first_stmt, "end_lineno", start_line)
                blocks.append(
                    CommentBlock(
                        text=mod_doc.strip(),
                        start_line=start_line,
                        end_line=end_line,
                        kind="docstring",
                        language="python",
                        symbol=None,
                    )
                )

        # Class / function / method docstrings
        for node, qname in _walk_python_definitions(tree):
            doc = ast.get_docstring(node, clean=False)
            if not doc:
                continue
            first = node.body[0]
            start_line = first.lineno
            end_line = getattr(first, "end_lineno", start_line)
            blocks.append(
                CommentBlock(
                    text=doc.strip(),
                    start_line=start_line,
                    end_line=end_line,
                    kind="docstring",
                    language="python",
                    symbol=qname,
                )
            )

    blocks.sort(key=lambda b: (b.start_line, b.end_line))
    return blocks


def _walk_python_definitions(tree: ast.AST) -> Iterable[tuple[ast.AST, str]]:
    """Yield (node, qualified_name) for every class/function/method.

    Walks one level into classes so methods get ``Class.method`` qnames.
    Doesn't descend into function bodies — nested functions are out of
    scope for v1, matching ``codeparse.langs.python``'s SP-A posture.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, node.name
        elif isinstance(node, ast.ClassDef):
            yield node, node.name
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, f"{node.name}.{child.name}"


# ---------------------------------------------------------------------------
# C-style languages: regex + string-aware state machine
# ---------------------------------------------------------------------------


# Per-language quirks. ``line_prefix`` is the line-comment marker
# (``//`` for C-family, ``#`` would belong here for Python but Python
# uses tokenize). ``has_block`` toggles ``/* ... */`` handling.
_C_STYLE_QUIRKS: dict[str, dict] = {
    "java":       {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "javascript": {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "typescript": {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "go":         {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "c":          {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "cpp":        {"line_prefix": "//", "has_block": True,  "doc_prefixes": ()},
    "rust":       {"line_prefix": "//", "has_block": True,  "doc_prefixes": ("///", "//!")},
}


# Comments inside string / char literals are not comments. We use a
# tiny state machine: scan char-by-char tracking whether we're inside a
# ``"..."`` / ``'...'`` / template-literal `` `...` `` (JS/TS). Escapes
# (``\"``) are honored. This is intentionally simple — Rust raw strings
# (``r#"..."#``) and Go raw strings (`` `...` ``) need extra care, but
# the v1 cost/benefit favors "ship the common case, accept rare
# misses" per the regex_fallback.py posture in this repo.
def _strip_strings(text: str, *, language: str) -> str:
    """Replace string-literal *contents* with spaces, preserving lines.

    The replacement keeps every newline and the quote characters
    themselves so character offsets / line numbers in the output match
    the input. After this, a plain regex for ``//`` / ``/* */`` won't
    match inside what used to be a string.

    Template literals (`` `...` ``) are stripped for JS / TS only;
    in other C-family languages backticks are not string delimiters.
    """
    out = []
    i = 0
    n = len(text)
    in_str: Optional[str] = None  # the active opening quote, or None
    in_block_comment = False
    in_line_comment = False
    template_capable = language in ("javascript", "typescript")

    while i < n:
        ch = text[i]

        # --- comment context dominates: don't enter strings from inside a comment ---
        if in_line_comment:
            out.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            out.append(ch)
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                out.append("/")
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # --- string context ---
        if in_str is not None:
            if ch == "\\" and i + 1 < n:
                # Replace the escaped char with a space; preserve the
                # backslash so line counts stay aligned.
                out.append(ch)
                out.append(" " if text[i + 1] != "\n" else "\n")
                i += 2
                continue
            if ch == in_str:
                out.append(ch)
                in_str = None
                i += 1
                continue
            # Inside a string: keep newlines (multi-line strings are
            # valid for ``"""`` in Python, template literals in JS/TS,
            # backtick raw strings in Go) — for everything else this is
            # still safe because a line break inside ``"..."`` is a
            # syntax error and we degrade rather than crash.
            out.append(ch if ch == "\n" else " ")
            i += 1
            continue

        # --- not in any context: detect openers ---
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            in_line_comment = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            in_block_comment = True
            out.append(ch)
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        if template_capable and ch == "`":
            in_str = "`"
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out)


# Patterns matched against the *string-stripped* text. We use re.MULTILINE
# for line comments and re.DOTALL for block comments.
_LINE_COMMENT_RE = re.compile(r"//(?P<body>[^\n]*)")
_BLOCK_COMMENT_RE = re.compile(r"/\*(?P<body>.*?)\*/", re.DOTALL)
# Same pattern set, SQL-flavored:
_SQL_LINE_RE = re.compile(r"--(?P<body>[^\n]*)")
_SHELL_LINE_RE = re.compile(r"(?:^|[\t ;])#(?P<body>[^\n]*)")


# Pragma-style prefixes we strip when skip_pragmas=True. Conservative —
# the first non-space token of the comment body must match.
_C_STYLE_PRAGMA_PREFIXES = (
    "@ts-ignore",
    "@ts-nocheck",
    "@ts-expect-error",
    "eslint-disable",
    "eslint-enable",
    "prettier-ignore",
    "biome-ignore",
    "deno-lint-ignore",
    "nolint",
    "noinspection",
    "go:build",
    "go:generate",
    "go:embed",
    "go:linkname",
    "#![",  # Rust inner attribute on its own line — handled separately
)


def _is_c_style_pragma(body: str, *, language: str) -> bool:
    s = body.strip()
    if not s:
        return False
    # Strip the leading "!" of `///`/`//!` doc-comment markers if any —
    # those are doc comments, NOT pragmas, so let them through.
    first = s.split(None, 1)[0].lower()
    return any(first.startswith(p.lower()) for p in _C_STYLE_PRAGMA_PREFIXES)


def _is_c_style_pragma_line(src_line: str) -> bool:
    """Inspect the ORIGINAL source line (with the ``//`` prefix intact).

    Catches the ``//go:build`` / ``//go:generate`` form where no space
    follows the ``//``. We also detect ``// @ts-...`` / ``// eslint-...``
    by stripping the ``//`` + optional whitespace and testing the
    pragma-prefix list.
    """
    stripped = src_line.lstrip()
    if not stripped.startswith("//"):
        return False
    after = stripped[2:].lstrip()
    if not after:
        return False
    first = after.split(None, 1)[0].lower()
    return any(first.startswith(p.lower()) for p in _C_STYLE_PRAGMA_PREFIXES)


def _extract_c_style(
    *, text: str, language: str, skip_pragmas: bool
) -> list[CommentBlock]:
    """Regex-based extractor for C-family languages.

    Two stages:

    1. Mask string-literal *contents* so ``//`` inside a string is
       invisible to the regex.
    2. Scan for ``/* ... */`` first (multi-char, take precedence), then
       for ``// ...`` line comments. Group consecutive single-line
       comments at the same column into one ``CommentBlock``.
    """
    stripped = _strip_strings(text, language=language)
    blocks: list[CommentBlock] = []

    # --- Block comments ---
    occupied: list[tuple[int, int]] = []  # (start_offset, end_offset)
    for m in _BLOCK_COMMENT_RE.finditer(stripped):
        body = m.group("body")
        cleaned = _clean_block_body(body)
        if not cleaned:
            continue
        start_line = stripped.count("\n", 0, m.start()) + 1
        end_line = stripped.count("\n", 0, m.end() - 1) + 1
        blocks.append(
            CommentBlock(
                text=cleaned,
                start_line=start_line,
                end_line=end_line,
                kind="block",
                language=language,
            )
        )
        occupied.append((m.start(), m.end()))

    # --- Line comments (// or /// or //!) ---
    # Group consecutive lines that are *only* a line comment (ignoring
    # leading whitespace). A code-trailing comment (``foo(); // hi``)
    # is a single-line block of its own.
    line_groups: list[list[tuple[int, str, str]]] = []
    # Each element: (line_num_1based, comment_body_stripped, mode)
    # mode in {"standalone", "trailing"} — only "standalone" groups can
    # extend.
    lines = stripped.split("\n")
    src_lines = text.split("\n")

    for idx, line in enumerate(lines):
        # Find the first // on this line, accounting for the fact that
        # _strip_strings already removed string contents.
        pos = line.find("//")
        if pos < 0:
            # Boundary line — the next "standalone" group must NOT merge
            # backwards. Insert a sentinel only if the last entry is a
            # real (non-sentinel) group, so we don't stack sentinels.
            if line_groups and line_groups[-1]:
                line_groups.append([])
            continue
        # Is this position inside a block-comment-occupied range? Skip if so.
        # (Block comments may legitimately contain "//" as part of prose.)
        offset = sum(len(line_) + 1 for line_ in lines[:idx]) + pos
        if any(s <= offset < e for s, e in occupied):
            continue
        before = line[:pos].strip()
        mode = "standalone" if not before else "trailing"
        # Original-text body (so we preserve whatever spacing the
        # author used; the stripped pass replaced string content with
        # spaces but our offsets line up).
        body = src_lines[idx][pos + 2:]
        # Drop one leading space for readability.
        if body.startswith(" "):
            body = body[1:]
        # Drop pragma-style lines up front so they don't poison a
        # later merge ("``// @ts-ignore`` then real comment" should
        # NOT become one grouped block). The original-text line is
        # used for the prefix check so we catch ``//go:build...``
        # (no space between ``//`` and ``go:build``).
        if skip_pragmas and _is_c_style_pragma_line(src_lines[idx]):
            # Insert a sentinel so subsequent standalone comments
            # don't reverse-merge through the pragma.
            if line_groups and line_groups[-1]:
                line_groups.append([])
            continue
        line_groups.append([(idx + 1, body, mode)])

        # Try to merge into a previous standalone group. Skip past any
        # empty sentinel entries (left as boundary markers above).
        if mode == "standalone" and len(line_groups) >= 2:
            j = len(line_groups) - 2
            while j >= 0 and not line_groups[j]:
                j -= 1
            if (
                j >= 0
                and line_groups[j][-1][2] == "standalone"
                and line_groups[j][-1][0] + 1 == idx + 1
            ):
                # Pop everything from j onwards, merge group j with the
                # current one, discard the sentinels.
                curr = line_groups.pop()
                # Drop any sentinels between j and end.
                while len(line_groups) > j + 1:
                    line_groups.pop()
                prev = line_groups.pop()
                prev.extend(curr)
                line_groups.append(prev)

    # Flatten empty entries
    line_groups = [g for g in line_groups if g]

    quirks = _C_STYLE_QUIRKS[language]
    doc_prefixes = quirks["doc_prefixes"]

    for group in line_groups:
        # Doc-comment detection (Rust). When the SOURCE line begins with
        # ``///`` or ``//!`` the line-comment regex captured one extra
        # ``/`` or ``!`` at the head of the body — strip that so the
        # extracted text reads as prose.
        kind: CommentKind = "line"
        body_lines = []
        if doc_prefixes:
            for line_num, body, _mode in group:
                src_line = src_lines[line_num - 1].lstrip()
                trimmed = body
                for dp in doc_prefixes:
                    if src_line.startswith(dp):
                        # body still starts with the trailing char of dp
                        # past the initial "//" (e.g. "/", "!").
                        extra = dp[2:]
                        if trimmed.startswith(extra):
                            trimmed = trimmed[len(extra):]
                            if trimmed.startswith(" "):
                                trimmed = trimmed[1:]
                        kind = "block"
                        break
                body_lines.append(trimmed)
        else:
            for _ln, body, _mode in group:
                body_lines.append(body)
        joined = "\n".join(body_lines).strip()
        if not joined:
            continue
        blocks.append(
            CommentBlock(
                text=joined,
                start_line=group[0][0],
                end_line=group[-1][0],
                kind=kind,
                language=language,
            )
        )

    blocks.sort(key=lambda b: (b.start_line, b.end_line))
    return blocks


def _clean_block_body(body: str) -> str:
    """Strip one leading ``*`` per line (JavaDoc style) and trim."""
    out_lines = []
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("*"):
            stripped = stripped[1:]
            if stripped.startswith(" "):
                stripped = stripped[1:]
            out_lines.append(stripped.rstrip())
        else:
            out_lines.append(line.strip())
    return "\n".join(out_lines).strip()


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


def _extract_sql(*, text: str, skip_pragmas: bool) -> list[CommentBlock]:
    """SQL has ``-- line`` and ``/* block */`` comments.

    Same string-aware masking as C-style; SQL strings are ``'...'``
    with ``''`` as the escape (not ``\\'``), but for comment-detection
    purposes the simple " inside-a-string" mask is sufficient — false
    negatives (missing a comment inside a string) are vanishingly rare
    in real SQL.
    """
    blocks: list[CommentBlock] = []
    # Strip single-quoted string contents — we hand-roll because SQL
    # doesn't use backslash escapes.
    out = []
    i = 0
    in_str = False
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                # SQL escape — keep both quotes, skip
                out.append("'")
                out.append("'")
                i += 2
                continue
            if ch == "'":
                out.append("'")
                in_str = False
                i += 1
                continue
            out.append(ch if ch == "\n" else " ")
            i += 1
            continue
        if ch == "'":
            in_str = True
            out.append("'")
            i += 1
            continue
        out.append(ch)
        i += 1
    stripped = "".join(out)

    # Block comments
    occupied: list[tuple[int, int]] = []
    for m in _BLOCK_COMMENT_RE.finditer(stripped):
        body = m.group("body")
        cleaned = _clean_block_body(body)
        if not cleaned:
            continue
        start_line = stripped.count("\n", 0, m.start()) + 1
        end_line = stripped.count("\n", 0, m.end() - 1) + 1
        blocks.append(
            CommentBlock(
                text=cleaned,
                start_line=start_line,
                end_line=end_line,
                kind="block",
                language="sql",
            )
        )
        occupied.append((m.start(), m.end()))

    # Line comments
    lines = stripped.split("\n")
    src_lines = text.split("\n")
    groups: list[list[tuple[int, str, str]]] = []
    for idx, line in enumerate(lines):
        pos = line.find("--")
        if pos < 0:
            if groups and groups[-1]:
                groups.append([])
            continue
        offset = sum(len(l_) + 1 for l_ in lines[:idx]) + pos
        if any(s <= offset < e for s, e in occupied):
            continue
        before = line[:pos].strip()
        mode = "standalone" if not before else "trailing"
        body = src_lines[idx][pos + 2:]
        if body.startswith(" "):
            body = body[1:]
        groups.append([(idx + 1, body, mode)])
        if mode == "standalone" and len(groups) >= 2:
            j = len(groups) - 2
            while j >= 0 and not groups[j]:
                j -= 1
            if (
                j >= 0
                and groups[j][-1][2] == "standalone"
                and groups[j][-1][0] + 1 == idx + 1
            ):
                curr = groups.pop()
                while len(groups) > j + 1:
                    groups.pop()
                prev = groups.pop()
                prev.extend(curr)
                groups.append(prev)

    groups = [g for g in groups if g]
    for group in groups:
        joined = "\n".join(b for _, b, _ in group).strip()
        if not joined:
            continue
        blocks.append(
            CommentBlock(
                text=joined,
                start_line=group[0][0],
                end_line=group[-1][0],
                kind="line",
                language="sql",
            )
        )

    blocks.sort(key=lambda b: (b.start_line, b.end_line))
    return blocks


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


def _extract_shell(*, text: str, skip_pragmas: bool) -> list[CommentBlock]:
    """Shell only has ``# line`` comments.

    Trailing-comment detection is heuristic — we treat any ``#`` not at
    the start of a line as trailing iff the preceding char is whitespace
    or a semicolon. ``#`` inside a single-quoted string is preserved
    verbatim by quoting semantics, but we don't try to mask strings
    here — shell quoting is too irregular to handle in v1 (heredocs,
    ANSI-C $'', etc.). The cost of a false-positive ``#`` inside a
    string is one bogus document — acceptable.
    """
    blocks: list[CommentBlock] = []
    lines = text.split("\n")
    groups: list[list[tuple[int, str, str]]] = []
    for idx, line in enumerate(lines):
        if not line:
            if groups and groups[-1]:
                groups.append([])
            continue
        stripped = line.lstrip()
        if stripped.startswith("#!") and idx == 0 and skip_pragmas:
            continue
        if stripped.startswith("#"):
            body = stripped[1:]
            if body.startswith(" "):
                body = body[1:]
            mode = "standalone"
            groups.append([(idx + 1, body, mode)])
            if len(groups) >= 2:
                j = len(groups) - 2
                while j >= 0 and not groups[j]:
                    j -= 1
                if (
                    j >= 0
                    and groups[j][-1][2] == "standalone"
                    and groups[j][-1][0] + 1 == idx + 1
                ):
                    curr = groups.pop()
                    while len(groups) > j + 1:
                        groups.pop()
                    prev = groups.pop()
                    prev.extend(curr)
                    groups.append(prev)
            continue
        # Trailing comment?
        pos = line.find("#")
        if pos > 0 and line[pos - 1] in (" ", "\t", ";"):
            body = line[pos + 1:]
            if body.startswith(" "):
                body = body[1:]
            groups.append([(idx + 1, body, "trailing")])

    groups = [g for g in groups if g]
    for group in groups:
        joined = "\n".join(b for _, b, _ in group).strip()
        if not joined:
            continue
        blocks.append(
            CommentBlock(
                text=joined,
                start_line=group[0][0],
                end_line=group[-1][0],
                kind="line",
                language="shell",
            )
        )

    blocks.sort(key=lambda b: (b.start_line, b.end_line))
    return blocks


__all__ = ["CommentBlock", "extract_comments", "detect_language"]
