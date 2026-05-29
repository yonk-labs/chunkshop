"""Go symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.python` in shape, swapping the
grammar package and node names. Go has no class-nested methods — a method is
a ``method_declaration`` with a *receiver* (``func (c *Calculator) Add(...)``).
We resolve the receiver's type name and use it as ``parent_name`` so methods
group under their type, the same way Python/Java methods group under a class.
Struct and interface type declarations land as ``class`` / ``interface``.

On any import or query failure the caller (``tree_sitter_wrapper``) drops
through to the regex fallback — this module signals failure by raising,
never by returning a bogus ParseResult.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-go"

_CALL_QUERY = (
    "(call_expression function: (identifier) @callee_name) @call\n"
    "(call_expression function: (selector_expression"
    " field: (field_identifier) @callee_name)) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse Go source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_go  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_go.language())
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
        language="go",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _receiver_type(node: Any, source: bytes) -> Optional[str]:
    """Pull the type name out of a method_declaration's receiver.

    ``func (c *Calculator) Add(...)`` → ``Calculator``. The receiver type may
    be a bare ``type_identifier`` or wrapped in a ``pointer_type``; in both
    cases the first descendant ``type_identifier`` is the type name.
    """
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    stack = [receiver]
    while stack:
        cur = stack.pop()
        if cur.type == "type_identifier":
            return _text(cur, source)
        stack.extend(cur.children)
    return None


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Any) -> None:
        ntype = node.type
        if ntype == "type_declaration":
            for spec in node.children:
                if spec.type != "type_spec":
                    continue
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node is None:
                    continue
                name = _text(name_node, source)
                kind = type_node.type if type_node is not None else ""
                sym_type = "interface" if kind == "interface_type" else "class"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type=sym_type,
                        line_start=spec.start_point[0] + 1,
                        line_end=spec.end_point[0] + 1,
                        parent_name=None,
                    )
                )
            return
        if ntype == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
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
            return
        if ntype == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
                parent = _receiver_type(node, source)
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent),
                        symbol_type="method",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=parent,
                    )
                )
            return

        for child in node.children:
            visit(child)

    visit(root)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Walk up to the enclosing func/method; return (name, receiver_type)."""
    cur = node.parent
    while cur is not None:
        if cur.type == "function_declaration":
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                return (_text(name_node, source), None)
        elif cur.type == "method_declaration":
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                return (_text(name_node, source), _receiver_type(cur, source))
        cur = cur.parent
    return None


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
        caller_id = code_symbol_node_id(project_id, "go", file_path, caller_fqn)
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
        if child.type == "import_declaration":
            out.append(_text(child, source).strip())
    return out


__all__ = ["parse"]
