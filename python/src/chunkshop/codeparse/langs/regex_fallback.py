"""Pure-regex symbol + call-site extraction.

This module is the safety net: if tree-sitter or the per-language grammar
package isn't installed, every language still produces a ``ParseResult``
with the most common top-level declarations. Coverage is "best common
case", not "complete language" — class declarations, function / method
declarations, interface declarations, and bare call sites. It's enough to
keep search-by-symbol useful when the heavy tree-sitter wheels are absent.

The patterns lift from yonk-full-stack-mig-srv ``indexer/parser.py`` with
the same posture: anchor to ``^\\s*`` to avoid matching inside strings or
comments in practice; accept the rare miss / over-match over hauling in a
full lexer just for the fallback.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_REGEX_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "python": [
        ("class", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"^def\s+(\w+)", re.MULTILINE)),
        ("method", re.compile(r"^[ \t]+def\s+(\w+)", re.MULTILINE)),
    ],
    "java": [
        (
            "class",
            re.compile(
                r"^\s*(?:public|private|protected)?\s*(?:abstract|final|static)?\s*class\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "interface",
            re.compile(
                r"^\s*(?:public|private|protected)?\s*interface\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "method",
            re.compile(
                r"^\s+(?:public|private|protected)\s+(?:static\s+)?(?:[\w<>\[\],\s]+?)\s+(\w+)\s*\([^)]*\)\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "javascript": [
        (
            "class",
            re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
        ),
        (
            "function",
            re.compile(
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "method",
            re.compile(
                r"^[ \t]+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "typescript": [
        (
            "class",
            re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
        ),
        (
            "interface",
            re.compile(
                r"^\s*(?:export\s+)?interface\s+(\w+)", re.MULTILINE
            ),
        ),
        (
            "function",
            re.compile(
                r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "method",
            re.compile(
                r"^[ \t]+(?:public\s+|private\s+|protected\s+)?(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]| ]+)?\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE)),
        ("method", re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(", re.MULTILINE)),
        ("class", re.compile(r"^type\s+(\w+)\s+struct\b", re.MULTILINE)),
    ],
    # The five below are best-effort fallbacks only — the [code] extra ships
    # real tree-sitter grammars for each. Anchored to common top-level forms.
    "rust": [
        (
            "class",
            re.compile(r"^\s*(?:pub\s+)?(?:struct|enum)\s+(\w+)", re.MULTILINE),
        ),
        ("interface", re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.MULTILINE)),
        (
            "function",
            re.compile(
                r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE
            ),
        ),
    ],
    "c": [
        ("class", re.compile(r"^\s*struct\s+(\w+)", re.MULTILINE)),
        (
            "function",
            re.compile(
                r"^[A-Za-z_][\w\s\*]*?\b(\w+)\s*\([^;{]*\)\s*\{", re.MULTILINE
            ),
        ),
    ],
    "cpp": [
        ("class", re.compile(r"^\s*(?:class|struct)\s+(\w+)", re.MULTILINE)),
        (
            "function",
            re.compile(
                r"^[A-Za-z_][\w\s\*:<>,]*?\b(\w+)\s*\([^;{]*\)\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "csharp": [
        (
            "class",
            re.compile(
                r"^\s*(?:public|private|protected|internal)?\s*(?:abstract|sealed|static)?\s*class\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "interface",
            re.compile(
                r"^\s*(?:public|private|protected|internal)?\s*interface\s+(\w+)",
                re.MULTILINE,
            ),
        ),
        (
            "method",
            re.compile(
                r"^\s+(?:public|private|protected|internal)\s+(?:static\s+|virtual\s+|override\s+|async\s+)*[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "ruby": [
        ("class", re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)),
        ("interface", re.compile(r"^\s*module\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"^\s*def\s+(\w+)", re.MULTILINE)),
    ],
}

_IMPORT_REGEX: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"^\s*(?:from\s+([\w.]+)\s+)?import\s+([\w., ]+)", re.MULTILINE
    ),
    "java": re.compile(r"^\s*import\s+([\w.*]+)\s*;", re.MULTILINE),
    "javascript": re.compile(
        r"""^\s*import\s+.*?from\s+['"]([^'"]+)['"]""", re.MULTILINE
    ),
    "typescript": re.compile(
        r"""^\s*import\s+.*?from\s+['"]([^'"]+)['"]""", re.MULTILINE
    ),
    "go": re.compile(r'^\s*import\s+(?:\([^)]+\)|"[^"]+")', re.MULTILINE | re.DOTALL),
}

# Generic call-site pattern. Greedy enough to fire on ``foo()``,
# ``self.foo()`` (captures ``foo``), or ``obj.method()``. Strict enough to
# skip ``foo =``-style assignments and keyword statements (``if (``,
# ``while (``, ``for (``, ``return (``, ``switch (``, ``catch (``).
_CALL_REGEX = re.compile(
    r"(?<![\w.])(?P<name>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_KEYWORD_SKIPLIST = frozenset(
    {
        "if", "while", "for", "switch", "return", "catch", "throw",
        "elif", "and", "or", "not", "in", "is", "lambda",
        "function", "def", "class", "interface", "struct", "type",
        "func", "import", "from", "package", "var", "let", "const",
        "new", "await", "yield", "raise", "del", "global", "nonlocal",
        "with", "try", "except", "finally", "case", "default",
        "public", "private", "protected", "static", "async",
        "true", "false", "null", "None", "True", "False",
    }
)


def extract_with_regex(
    *,
    text: str,
    language: str,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Best-effort extraction. Always returns a ParseResult (possibly empty).

    Single public entry point so the tree-sitter wrapper can fall through
    here without knowing the language-specific pattern tables.
    """
    symbols = _extract_symbols(text=text, language=language, file_path=file_path)
    call_sites = _extract_call_sites(
        text=text,
        language=language,
        file_path=file_path,
        project_id=project_id,
        symbols=symbols,
    )
    imports = _extract_imports(text=text, language=language)
    return ParseResult(
        symbols=symbols,
        call_sites=call_sites,
        imports=imports,
        language=language,
        parser="regex",
    )


def _extract_symbols(*, text: str, language: str, file_path: str) -> list[Symbol]:
    patterns = _REGEX_PATTERNS.get(language, [])
    symbols: list[Symbol] = []
    # Track method-of-class via simple indentation heuristic for Python; for
    # everything else, regex 'method' patterns already encode "indented body
    # of a class" via leading whitespace, so the parent name is unknown at
    # this layer. The cross-file resolver can still match by bare name.
    parent_stack: list[tuple[int, str]] = []  # (indent, class_name) only for python
    if language == "python":
        for line_num, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            # Blank / whitespace-only lines and comment lines do NOT affect
            # the parent stack — they have indent 0 by definition and would
            # otherwise pop a legitimate enclosing class.
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            # Pop stack on real dedent (a non-blank line at the parent
            # indent or shallower).
            while parent_stack and indent <= parent_stack[-1][0]:
                parent_stack.pop()
            m_class = re.match(r"class\s+(\w+)", stripped)
            if m_class:
                name = m_class.group(1)
                parent = parent_stack[-1][1] if parent_stack else None
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type="class",
                        line_start=line_num,
                        line_end=line_num,
                        parent_name=parent,
                    )
                )
                parent_stack.append((indent, name))
                continue
            m_def = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
            if m_def:
                name = m_def.group(1)
                parent = parent_stack[-1][1] if parent_stack else None
                sym_type = "method" if parent else "function"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type=sym_type,
                        line_start=line_num,
                        line_end=line_num,
                        parent_name=parent,
                    )
                )
        return symbols

    # Non-Python: bracket-aware scan to set parent_name on methods.
    # We track the most-recently-opened class/interface by counting braces.
    class_stack: list[tuple[int, str]] = []  # (brace_depth_when_opened, class_name)
    brace_depth = 0
    for line_num, line in enumerate(text.splitlines(), start=1):
        # Sweep class / interface / struct declarations first.
        for sym_type, pattern in patterns:
            m = pattern.match(line)
            if m is None:
                continue
            name = m.group(1)
            if sym_type in ("class", "interface"):
                parent = class_stack[-1][1] if class_stack else None
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type=sym_type,
                        line_start=line_num,
                        line_end=line_num,
                        parent_name=parent,
                    )
                )
                # We assume the class body opens on the same line or the next.
                # The brace push happens during the brace sweep below.
                class_stack.append((brace_depth, name))
                break
            if sym_type == "method":
                parent = class_stack[-1][1] if class_stack else None
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type="method" if parent else "function",
                        line_start=line_num,
                        line_end=line_num,
                        parent_name=parent,
                    )
                )
                break
            if sym_type == "function":
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type="function",
                        line_start=line_num,
                        line_end=line_num,
                        parent_name=None,
                    )
                )
                break
        # Brace sweep — pop class_stack when its enclosing scope closes.
        for ch in line:
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                while class_stack and brace_depth <= class_stack[-1][0]:
                    class_stack.pop()
    return symbols


def _extract_imports(*, text: str, language: str) -> list[str]:
    pat = _IMPORT_REGEX.get(language)
    if pat is None:
        return []
    return [m.group(0).strip() for m in pat.finditer(text)]


def _extract_call_sites(
    *,
    text: str,
    language: str,
    file_path: str,
    project_id: str,
    symbols: list[Symbol],
) -> list[CallSite]:
    """Emit one CallSite per ``name(`` occurrence, scoped to the nearest
    enclosing top-level symbol when one can be inferred.

    The "enclosing symbol" lookup is purely line-based: we walk *symbols*,
    pick the symbol whose ``line_start <= call_line <= line_end``, and use
    its FQN to mint ``caller_node_id``. For the regex fallback, ``line_end``
    is typically equal to ``line_start`` (we can't compute end lines without
    a real AST), so the heuristic is "nearest symbol declared above the
    call." This matches the v1 posture from yonk-full-stack-mig-srv: a
    best-effort identifier; cross-file resolution corrects unresolved sites
    later.
    """
    lines = text.splitlines()
    # Sort symbols by line_start ascending so we can pick the nearest above.
    sorted_syms = sorted(symbols, key=lambda s: s.line_start)

    def _enclosing(line_num: int) -> Optional[Symbol]:
        nearest: Optional[Symbol] = None
        for s in sorted_syms:
            if s.line_start > line_num:
                break
            if s.symbol_type in ("function", "method"):
                nearest = s
        return nearest

    call_sites: list[CallSite] = []
    intra_file_names = {s.name for s in symbols}
    for line_num, line in enumerate(lines, start=1):
        for m in _CALL_REGEX.finditer(line):
            name = m.group("name")
            if name in _KEYWORD_SKIPLIST:
                continue
            enclosing = _enclosing(line_num)
            if enclosing is None:
                continue
            caller_id = code_symbol_node_id(
                project_id, language, file_path, enclosing.fqn
            )
            call_sites.append(
                CallSite(
                    caller_node_id=caller_id,
                    callee_name=name,
                    line=line_num,
                    snippet=line.strip()[:240],
                    parser="regex",
                    resolved_intra_file=name in intra_file_names and name != enclosing.name,
                )
            )
    return call_sites


def detect_language(path: Path) -> Optional[str]:
    """Map a file extension to the chunkshop language tag.

    Centralised so tests and the tree-sitter wrapper agree on what counts
    as ``.py`` etc. Unknown extensions return None — the caller decides
    whether that's an error or a skip.
    """
    suffix = path.suffix.lower()
    return _EXT_TO_LANG.get(suffix)


# --- content-based detection (last resort) ---------------------------------
# When a document arrives without a usable path or explicit language hint (a
# caller passing a synthetic id / stele:// URI — see chunkshop#69), guessing
# the language from the source body keeps symbol extraction working instead of
# silently falling back. Covers all ten codeparse languages.
#
# Each language has a set of (pattern, weight) markers chosen to be *distinctive*
# — markers shared across languages (``class``, ``import``, bare ``def``) carry
# low weight or are omitted, so the score reflects evidence FOR a specific
# language rather than generic code-ness. The highest-scoring language wins, but
# only if it clears ``_CONTENT_MIN_SCORE`` AND beats the runner-up by
# ``_CONTENT_MARGIN`` — a near-tie is "ambiguous" and returns None. Conservative
# by design: a doc that doesn't clearly read as one language falls back exactly
# as before, so a wrong guess can never do worse than today's behaviour.
_CONTENT_MIN_SCORE = 3
_CONTENT_MARGIN = 2

_CONTENT_MARKERS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    lang: [(re.compile(p), w) for p, w in markers]
    for lang, markers in {
        "python": [
            # def ...(): — tolerates an async prefix and a -> return annotation
            (r"(?m)^[ \t]*(?:async[ \t]+)?def[ \t]+\w+\s*\([^)]*\)\s*(?:->[^:\n]+)?:", 3),
            (r"(?m)^[ \t]*from[ \t]+[\w.]+[ \t]+import\b", 3),
            (r"(?m)^[ \t]*elif\b", 3),
            (r"(?m)^[ \t]*class[ \t]+\w+[^\n]*:\s*$", 2),     # class ...:
            (r"(?m)\b(?:__name__|__init__|self)\b", 1),
            (r"(?m)^[ \t]*@\w+[ \t]*$", 1),                   # decorator line
            (r"(?m)^[ \t]*def[ \t]+\w+", 1),                  # shared w/ ruby
        ],
        "ruby": [
            (r"(?m)^[ \t]*end[ \t]*$", 3),                    # block terminator
            (r"(?m)^[ \t]*elsif\b", 3),
            (r"(?m)\battr_(?:accessor|reader|writer)\b", 3),
            (r"(?m)\bputs\b", 2),
            (r"(?m)^[ \t]*require(?:_relative)?[ \t]+['\"]", 2),
            (r"(?m)\bdo[ \t]*\|", 2),                         # do |x|
            (r"(?m)^[ \t]*module[ \t]+\w+", 1),
            (r"(?m)^[ \t]*def[ \t]+\w+", 1),                  # shared w/ python
        ],
        "go": [
            (r"(?m)^package[ \t]+\w+[ \t]*$", 3),             # no semicolon
            (r"(?m)\bfunc[ \t]+(?:\([^)]*\)[ \t]*)?\w+[ \t]*\(", 3),
            (r"(?m):=", 2),
            (r"(?m)^[ \t]*type[ \t]+\w+[ \t]+struct\b", 2),
            (r"(?m)\b(?:fmt|errors|strings)\.\w+", 1),
        ],
        "rust": [
            (r"(?m)^[ \t]*(?:pub[ \t]+)?fn[ \t]+\w+", 3),
            (r"(?m)^[ \t]*impl\b", 3),
            (r"(?m)\blet[ \t]+mut\b", 2),
            (r"(?m)^[ \t]*use[ \t]+[\w:]+::", 2),
            (r"(?m)^[ \t]*(?:pub[ \t]+)?(?:struct|enum|trait)[ \t]+\w+", 1),
            (r"(?m)\b\w+!\s*[\(\[]", 1),                      # macro!(
            (r"(?m)&(?:self|mut)\b", 1),
        ],
        "c": [
            (r"(?m)^[ \t]*#include\b", 2),
            (r"(?m)\b(?:printf|malloc|free|sizeof)\b", 1),
            (r"(?m)\btypedef\b", 1),
            (r"(?m)^[A-Za-z_][\w \t\*]*\b\w+\s*\([^;{]*\)\s*\{", 1),
            (r"(?m)^[ \t]*struct[ \t]+\w+", 1),
        ],
        "cpp": [
            (r"(?m)\bstd::", 3),
            (r"(?m)\btemplate[ \t]*<", 3),
            (r"(?m)\busing[ \t]+namespace\b", 2),
            (r"(?m)\b(?:cout|cin|endl)\b", 2),
            (r"(?m)^[ \t]*(?:public|private|protected):", 2),  # access labels
            (r"(?m)^[ \t]*namespace[ \t]+\w+", 2),
            (r"(?m)^[ \t]*#include\b", 1),                    # shared w/ c
        ],
        "csharp": [
            (r"(?m)^[ \t]*using[ \t]+System", 3),
            (r"(?m)\bConsole[ \t]*\.", 3),
            (r"(?m)\bget;[ \t]*set;", 2),
            (r"(?m)^[ \t]*namespace[ \t]+[\w.]+\s*$", 2),     # Allman namespace
            (r"(?m)\bstring\[\]", 1),
            (r"(?m)\b(?:public|private|protected|internal)[ \t]+(?:class|interface|struct|enum)\b", 1),
            (r"(?m)\b(?:public|private|protected|internal)[ \t]+\w+[ \t]+\w+\s*\(", 1),
        ],
        "java": [
            (r"(?m)^[ \t]*package[ \t]+[\w.]+[ \t]*;", 3),    # semicolon (vs go)
            (r"(?m)\bSystem[ \t]*\.\s*(?:out|err|in)\b", 3),
            (r"(?m)\bjava\.\w+", 2),
            (r"(?m)^[ \t]*import[ \t]+[\w.]+[ \t]*;", 2),
            (r"(?m)@Override\b", 2),
            (r"(?m)\b(?:public|private|protected)[ \t]+(?:static[ \t]+)?(?:void|int|boolean|long|double|float|char|String)\b", 1),
            (r"(?m)\b(?:public|private|protected)[ \t]+(?:class|interface)\b", 1),
        ],
    }.items()
}

# JS/TS share a base; ``_TS_MARKERS`` are TypeScript-only and decide the split.
# A file scoring on either becomes the "javascript family" candidate, resolved
# to typescript when any TS-only marker fires, else javascript.
_JS_FAMILY_MARKERS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(p), w)
    for p, w in [
        (r"(?m)\bfunction[ \t*(]", 2),
        (r"(?m)=>", 1),
        (r"(?m)^[ \t]*(?:export|const|let)[ \t]", 1),
        (r"(?m)\b(?:console\.log|require\(|module\.exports)\b", 2),
        (r"(?m);[ \t]*$", 1),
    ]
]
_TS_MARKERS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(p), w)
    for p, w in [
        (r"(?m)^[ \t]*import[ \t].*[ \t]from[ \t]['\"]", 3),  # ES import
        (r"(?m):[ \t]*(?:string|number|boolean|void|any|unknown|never)\b", 2),
        (r"(?m)^[ \t]*type[ \t]+\w+[ \t]*=", 2),
        (r"(?m)\benum[ \t]+\w+", 2),
        (r"(?m)\breadonly\b", 2),
        (r"(?m)\binterface[ \t]+\w+", 1),  # weak: C#/Java have it too
        (r"(?m)\bimplements\b", 1),
    ]
]

# Shebang interpreters that pin a language outright.
_SHEBANG_LANGS = (("python", "python"), ("node", "javascript"), ("ruby", "ruby"))

# A line longer than this reads as machine-emitted (minified/bundled). Normal
# hand-written source almost never exceeds it; a single huge line is the
# signature of a minified blob. Conservative so a long string/array literal in
# real code doesn't trip it.
_GENERATED_MAX_LINE_LEN = 2000


def _looks_generated_or_minified(head: str) -> bool:
    """Heuristic: does ``head`` look machine-generated or minified?

    Path-less detection (chunkshop#69) started classifying generated/minified
    files as code that earlier was skipped; embedding the resulting flood of
    chunks OOM'd downstream consumers (chunkshop#71). When we're *guessing* from
    content alone, treat these as not-worth-symbol-parsing and skip them (the
    caller falls back to sentence_aware, which is bounded by size). Explicit
    signals — cfg.language / metadata['language'] / a real path — bypass this
    guard, so a caller can still force such a file through if they mean to.
    """
    # Explicit machine-generated markers.
    if "@generated" in head or "sourceMappingURL" in head:
        return True
    # Minified: at least one absurdly long line.
    return any(len(line) > _GENERATED_MAX_LINE_LEN for line in head.splitlines())


def detect_language_from_content(text: str) -> Optional[str]:
    """Best-effort language guess from source content alone.

    Returns a chunkshop language tag only on a clear, unambiguous signal across
    the ten supported languages; prose, config, a near-tie, or a file that looks
    generated/minified returns ``None`` so the caller falls back rather than
    mislabelling or over-parsing. Scans a bounded prefix so very large files stay
    cheap.
    """
    if not text:
        return None
    head = "\n".join(text.splitlines()[:400])

    if _looks_generated_or_minified(head):
        return None

    stripped = head.lstrip()
    if stripped.startswith("#!"):
        shebang = stripped.splitlines()[0]
        for needle, lang in _SHEBANG_LANGS:
            if needle in shebang:
                return lang

    scores: dict[str, int] = {
        lang: sum(w for pat, w in markers if pat.search(head))
        for lang, markers in _CONTENT_MARKERS.items()
    }
    js = sum(w for pat, w in _JS_FAMILY_MARKERS if pat.search(head))
    ts = sum(w for pat, w in _TS_MARKERS if pat.search(head))
    if js or ts:
        scores["typescript" if ts else "javascript"] = js + ts

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score >= _CONTENT_MIN_SCORE and best_score - runner_up >= _CONTENT_MARGIN:
        return best
    return None


def normalize_language_tag(value: str) -> Optional[str]:
    """Coerce a caller-supplied language hint to a canonical tag, else None.

    Accepts an exact tag (``"typescript"``), or an extension-ish alias
    (``"ts"``, ``".tsx"``, ``"py"``) which is matched through
    :func:`detect_language`. Unknown values return ``None`` so a junk hint is
    ignored rather than forcing a wrong language.
    """
    if not value:
        return None
    v = value.strip().lower()
    if v in KNOWN_LANGUAGES:
        return v
    ext = v if v.startswith(".") else "." + v
    return detect_language(Path("x" + ext))


_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
}


# Canonical set of codeparse language tags — the values of ``_EXT_TO_LANG``.
# Exported so callers can validate an explicit ``language`` hint without
# re-deriving the list (which would drift).
KNOWN_LANGUAGES: frozenset[str] = frozenset(_EXT_TO_LANG.values())


__all__ = [
    "extract_with_regex",
    "detect_language",
    "detect_language_from_content",
    "normalize_language_tag",
    "KNOWN_LANGUAGES",
]
