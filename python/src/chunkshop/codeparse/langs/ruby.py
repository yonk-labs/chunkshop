"""Ruby symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.python` in shape. Ruby nests methods
directly inside a ``class`` or ``module`` body (no separate impl wrapper like
Rust), so ``parent_name`` is threaded down the walk: a ``method`` whose
enclosing type is ``Calculator`` becomes a ``method`` with that parent; a
top-level ``method`` is a ``function``.

``class`` lands as ``class``; ``module`` also lands as ``class`` — it's the
nearest concept SP-A's schema models (there is no ``module`` symbol_type in
the frozen v1 set used here). Both expose their name via the ``name`` field,
which is a ``constant`` node.

Calls are best-effort: only ``call`` nodes with a ``method`` field (an
``identifier``) are captured — i.e. invocations written with parentheses or
an explicit receiver. Bare identifiers (Ruby's paren-less dynamic dispatch)
are NOT ``call`` nodes and are deliberately ignored: under-capture beats
over-capture for a name-heuristic resolver downstream.

Scope is one level deep: a ``method`` nested inside another ``method`` (rare
in Ruby) is NOT emitted, and a call inside it attributes to the OUTERMOST
enclosing ``method`` (the emitted symbol). On any import/parse failure the
caller drops through to the regex fallback; this module signals failure by
raising.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-ruby"

# A `call` node's `method` field is the invoked method name. Bare identifiers
# (no parens, no receiver) don't parse as `call` — we intentionally skip them.
_CALL_QUERY = "(call method: (identifier) @callee_name) @call"

# `class` and `module` both map to symbol_type "class".
_TYPE_SYMBOLS = {"class", "module"}


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse Ruby source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_ruby  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_ruby.language())
    parser = Parser(language)
    tree = parser.parse(source)
    root = tree.root_node

    symbols = _walk_symbols(root, file_path, source)
    call_sites = _extract_call_sites(
        root=root,
        language=language,
        symbols=symbols,
        file_path=file_path,
        project_id=project_id,
        source=source,
    )
    imports = _extract_imports(root, source)
    return ParseResult(
        symbols=symbols,
        call_sites=call_sites,
        imports=imports,
        language="ruby",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    """Collect classes / modules / functions / methods from the tree.

    ``class`` and ``module`` emit a ``class`` symbol and descend with their
    name as the parent. A ``method`` whose parent is a type is a ``method``;
    a top-level ``method`` is a ``function``. Nested methods are NOT emitted
    (one level deep, mirroring the Python extractor).
    """
    symbols: list[Symbol] = []

    def visit(node: Any, parent: Optional[str]) -> None:
        ntype = node.type
        if ntype in _TYPE_SYMBOLS:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type="class",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=None,
                    )
                )
                for child in node.children:
                    visit(child, name)
            return
        if ntype == "method":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
                sym_type = "method" if parent is not None else "function"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type=sym_type,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=parent,
                    )
                )
            # Don't descend — one level deep (nested methods not emitted).
            return

        for child in node.children:
            visit(child, parent)

    visit(root, None)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing ``method`` as (name, parent_type).

    Mirrors :func:`_walk_symbols`: a call inside a nested method rolls up to
    the outermost emitted method, and the parent is the nearest enclosing
    ``class``/``module`` name (None for a free function). Returns None for a
    module-level call with no enclosing method.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "method":
            outermost = cur  # keep climbing; the highest one wins
        cur = cur.parent
    if outermost is None:
        return None
    name_node = outermost.child_by_field_name("name")
    if name_node is None:
        return None
    func_name = _text(name_node, source)
    parent: Optional[str] = None
    anc = outermost.parent
    while anc is not None:
        if anc.type in _TYPE_SYMBOLS:
            tname = anc.child_by_field_name("name")
            if tname is not None:
                parent = _text(tname, source)
            break
        anc = anc.parent
    return (func_name, parent)


def _extract_call_sites(
    *,
    root: Any,
    language: Any,
    symbols: list[Symbol],
    file_path: str,
    project_id: str,
    source: bytes,
) -> list[CallSite]:
    try:
        from tree_sitter import Query, QueryCursor  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — pinned in [code] extra
        return []
    try:
        q = Query(language, _CALL_QUERY)
    except Exception:  # pragma: no cover — query string is constant
        return []

    intra_file_names = {s.name for s in symbols}
    call_sites: list[CallSite] = []
    cursor = QueryCursor(q)
    captures = cursor.captures(root)
    callee_nodes = captures.get("callee_name", [])

    for callee_node in callee_nodes:
        callee_name = _text(callee_node, source)
        enclosing = _enclosing_function(callee_node, source)
        if enclosing is None:
            continue
        func_name, parent = enclosing
        caller_fqn = build_fqn(file_path, func_name, parent)
        caller_id = code_symbol_node_id(project_id, "ruby", file_path, caller_fqn)
        line = callee_node.start_point[0] + 1
        snippet = (
            source[
                callee_node.start_byte : min(
                    callee_node.end_byte + 60, len(source)
                )
            ]
            .decode(errors="replace")
            .splitlines()[0]
        )
        call_sites.append(
            CallSite(
                caller_node_id=caller_id,
                callee_name=callee_name,
                line=line,
                snippet=snippet[:240],
                parser=_PARSER_NAME,
                resolved_intra_file=(
                    callee_name in intra_file_names and callee_name != func_name
                ),
            )
        )
    return call_sites


def _extract_imports(root: Any, source: bytes) -> list[str]:
    """Capture top-level ``require`` / ``require_relative`` lines, best-effort.

    These parse as ``call`` nodes; we keep only the bare source line so the
    downstream surface stays simple (no module-path resolution here).
    """
    out: list[str] = []
    for child in root.children:
        if child.type == "call":
            method = child.child_by_field_name("method")
            if method is not None and _text(method, source) in (
                "require",
                "require_relative",
            ):
                out.append(_text(child, source).strip())
    return out


__all__ = ["parse"]
