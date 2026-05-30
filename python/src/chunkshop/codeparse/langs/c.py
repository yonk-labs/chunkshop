"""C symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.rust` in shape, swapping the grammar
package and node names. C has no methods — every ``function_definition`` is a
top-level ``function`` with ``parent_name=None``. ``struct_specifier`` with a
``name`` field lands as ``class`` (anonymous structs are skipped). A function's
name lives on the ``declarator`` field: a ``function_declarator`` whose own
``declarator`` child is the ``identifier``. Pointer-returning functions wrap the
``function_declarator`` in one or more ``pointer_declarator`` nodes, so we
descend ``declarator`` fields until we hit the ``function_declarator``.

Scope is flat — C has no nested functions — but ``_enclosing_function`` climbs
to the OUTERMOST enclosing ``function_definition`` anyway for safety. On any
import or parse failure the caller drops through to the regex fallback; this
module signals failure by raising, never by returning a bogus ParseResult.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-c"

_CALL_QUERY = (
    "(call_expression function: (identifier) @callee_name) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse C source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_c  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_c.language())
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
        language="c",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _function_name(node: Any, source: bytes) -> Optional[str]:
    """Return a ``function_definition``'s name, or None.

    Descends the ``declarator`` field through any ``pointer_declarator`` wrappers
    (pointer-returning functions) until it finds the ``function_declarator``,
    then returns the text of that declarator's own ``declarator`` identifier.
    """
    cur = node.child_by_field_name("declarator")
    while cur is not None and cur.type != "function_declarator":
        cur = cur.child_by_field_name("declarator")
    if cur is None:
        return None
    name_node = cur.child_by_field_name("declarator")
    if name_node is None or name_node.type != "identifier":
        return None
    return _text(name_node, source)


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Any) -> None:
        ntype = node.type
        if ntype == "struct_specifier":
            name_node = node.child_by_field_name("name")
            if name_node is not None:  # skip anonymous structs
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
            return
        if ntype == "function_definition":
            name = _function_name(node, source)
            if name is not None:
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type="function",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=None,
                    )
                )
            # Don't descend — C has no nested functions.
            return

        for child in node.children:
            visit(child)

    visit(root)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing ``function_definition`` as (name, None).

    C has no methods, so ``parent_name`` is always None. C also has no nested
    functions, so the outermost enclosing function is the only one — but we
    climb to the highest anyway for safety. Returns None for a top-level call.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "function_definition":
            outermost = cur  # keep climbing; the last (highest) wins
        cur = cur.parent
    if outermost is None:
        return None
    func_name = _function_name(outermost, source)
    if func_name is None:
        return None
    return (func_name, None)


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
        caller_id = code_symbol_node_id(project_id, "c", file_path, caller_fqn)
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
    out: list[str] = []
    for child in root.children:
        if child.type == "preproc_include":
            out.append(_text(child, source).strip())
    return out


__all__ = ["parse"]
