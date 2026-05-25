"""Java symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.python` in shape, swapping the
grammar package and the tree-sitter node names used. Methods nested inside
``class_declaration`` get ``parent_name`` set to the enclosing class.
Interface declarations land as ``symbol_type='interface'`` so consumers
can filter them separately when needed.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-java"

_CALL_QUERY = (
    "(method_invocation name: (identifier) @callee_name) @call\n"
    "(object_creation_expression type: (type_identifier) @callee_name) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse Java source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_java

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_java.language())
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
        language="java",
        parser="tree-sitter",
    )


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Any, parent_class: Optional[str]) -> None:
        ntype = node.type
        if ntype in ("class_declaration", "interface_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = source[name_node.start_byte : name_node.end_byte].decode(
                    errors="replace"
                )
                sym_type = "class" if ntype == "class_declaration" else "interface"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type=sym_type,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=None,
                    )
                )
                for child in node.children:
                    visit(child, name)
                return
        elif ntype in ("method_declaration", "constructor_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = source[name_node.start_byte : name_node.end_byte].decode(
                    errors="replace"
                )
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent_class),
                        symbol_type="method",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=parent_class,
                    )
                )
                return  # SP-A: one level deep.

        for child in node.children:
            visit(child, parent_class)

    visit(root, None)
    return symbols


def _enclosing_method(node: Any, source: bytes) -> Optional[tuple[str, Optional[str]]]:
    cur = node.parent
    method_name: Optional[str] = None
    parent_class: Optional[str] = None
    while cur is not None:
        if cur.type in ("method_declaration", "constructor_declaration") and method_name is None:
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                method_name = source[
                    name_node.start_byte : name_node.end_byte
                ].decode(errors="replace")
        elif cur.type in ("class_declaration", "interface_declaration"):
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                parent_class = source[
                    name_node.start_byte : name_node.end_byte
                ].decode(errors="replace")
            if method_name:
                return (method_name, parent_class)
        cur = cur.parent
    if method_name:
        return (method_name, parent_class)
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
    except ImportError:  # pragma: no cover
        return []
    try:
        q = Query(language, _CALL_QUERY)
    except Exception:  # pragma: no cover
        return []

    intra_file_names = {s.name for s in symbols}
    call_sites: list[CallSite] = []
    cursor = QueryCursor(q)
    captures = cursor.captures(root)
    callee_nodes = captures.get("callee_name", [])

    for callee_node in callee_nodes:
        callee_name = source[
            callee_node.start_byte : callee_node.end_byte
        ].decode(errors="replace")
        enclosing = _enclosing_method(callee_node, source)
        if enclosing is None:
            continue
        method_name, parent_class = enclosing
        caller_fqn = build_fqn(file_path, method_name, parent_class)
        caller_id = code_symbol_node_id(project_id, "java", file_path, caller_fqn)
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
                    callee_name in intra_file_names and callee_name != method_name
                ),
            )
        )
    return call_sites


def _extract_imports(root: Any, source: bytes) -> list[str]:
    out: list[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            out.append(
                source[child.start_byte : child.end_byte]
                .decode(errors="replace")
                .strip()
            )
    return out


__all__ = ["parse"]
