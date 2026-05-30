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


__all__ = ["extract_with_regex", "detect_language"]
