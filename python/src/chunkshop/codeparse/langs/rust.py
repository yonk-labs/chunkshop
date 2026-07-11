"""Rust symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.go` in shape. Rust groups methods
under an ``impl_item`` (``impl Calculator { fn add(...) }``): the impl's
``type`` is the method's ``parent_name``, the same way Go's receiver type
groups methods. ``struct_item`` / ``enum_item`` / ``union_item`` land as
``class``; ``trait_item`` lands as ``interface``. Bare method signatures
(``function_signature_item``) are not emitted — interface marker only — but a
default method (``function_item``, i.e. it has a body) IS emitted as a
``method`` parented to the trait, so the calls in its body attribute to a real
symbol instead of becoming orphan edges.

Scope is one level deep: a ``fn`` nested inside another ``fn`` is NOT emitted,
and a call inside it attributes to the OUTERMOST enclosing ``function_item``
(the emitted symbol) — never the nested one. On any import/parse failure the
caller drops through to the regex fallback; this module signals failure by
raising.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-rust"

_CALL_QUERY = (
    "(call_expression function: (identifier) @callee_name) @call\n"
    "(call_expression function: (scoped_identifier"
    " name: (identifier) @callee_name)) @call\n"
    "(call_expression function: (field_expression"
    " field: (field_identifier) @callee_name)) @call"
)

_TYPE_SYMBOLS = {"struct_item", "enum_item", "union_item"}


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse Rust source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_rust  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_rust.language())
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
        language="rust",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _impl_type_name(node: Any, source: bytes) -> Optional[str]:
    """The type an ``impl_item`` is for: ``impl Calculator`` / ``impl T for C``.

    The ``type`` field is the implementing type in both forms. It may be a
    ``type_identifier`` or a ``generic_type`` whose first ``type_identifier``
    descendant is the name.
    """
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return _text(type_node, source)
    stack = [type_node]
    while stack:
        cur = stack.pop()
        if cur.type == "type_identifier":
            return _text(cur, source)
        stack.extend(cur.children)
    return None


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
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
            return
        if ntype == "trait_item":
            name_node = node.child_by_field_name("name")
            trait_name: Optional[str] = None
            if name_node is not None:
                trait_name = _text(name_node, source)
                symbols.append(
                    Symbol(
                        name=trait_name,
                        fqn=build_fqn(file_path, trait_name, None),
                        symbol_type="interface",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=None,
                    )
                )
            # Bare method signatures (function_signature_item) are NOT emitted —
            # interface marker only. But a DEFAULT method (function_item, i.e. it
            # has a body) is real code with real calls, so descend and emit it
            # parented to the trait — keeping call attribution symmetric with
            # _enclosing_function so its calls don't become orphan edges.
            for child in node.children:
                visit(child, trait_name)
            return
        if ntype == "impl_item":
            impl_type = _impl_type_name(node, source)
            for child in node.children:
                visit(child, impl_type)
            return
        if ntype == "function_item":
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
            # Don't descend — one level deep (nested fns not emitted).
            return

        for child in node.children:
            visit(child, parent)

    visit(root, None)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing ``function_item`` as (name, parent_type).

    Mirrors :func:`_walk_symbols`: a call inside a nested fn rolls up to the
    outermost emitted fn, and the parent is the nearest enclosing ``impl_item``
    type (None for a free function). Returns None for a module-level call.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "function_item":
            outermost = cur  # keep climbing; the last (highest) wins
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
        if anc.type == "impl_item":
            parent = _impl_type_name(anc, source)
            break
        if anc.type == "trait_item":
            # Symmetric with _walk_symbols: a default method's parent is its
            # trait, so its calls resolve to the emitted method symbol.
            name_node = anc.child_by_field_name("name")
            parent = _text(name_node, source) if name_node is not None else None
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
        caller_id = code_symbol_node_id(project_id, "rust", file_path, caller_fqn)
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
        if child.type == "use_declaration":
            out.append(_text(child, source).strip())
    return out


__all__ = ["parse"]
