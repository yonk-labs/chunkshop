"""TypeScript symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.python` / ``java`` in shape. Class,
interface, and top-level function declarations become symbols; ``method_
definition`` nodes inside a ``class_body`` become methods with ``parent_name``
set to the enclosing class. Declarations wrapped in an ``export_statement``
are reached transparently because the walker recurses through unrecognised
node types.

Uses ``tree_sitter_typescript.language_typescript()`` (the ``.ts`` grammar;
the package also ships ``language_tsx()``). On any import / parse failure the
caller falls through to the regex fallback — this module signals by raising.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-typescript"

_CALL_QUERY = (
    "(call_expression function: (identifier) @callee_name) @call\n"
    "(call_expression function: (member_expression"
    " property: (property_identifier) @callee_name)) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse TypeScript source via tree-sitter. Raises on import/parse error."""
    import tree_sitter_typescript  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_typescript.language_typescript())
    parser = Parser(language)
    tree = parser.parse(source)
    root = tree.root_node

    symbols = _walk_symbols(root, file_path, source, language_name="typescript")
    call_sites = _extract_call_sites(
        root=root,
        language=language,
        symbols=symbols,
        file_path=file_path,
        project_id=project_id,
        source=source,
        language_name="typescript",
    )
    imports = _extract_imports(root, source)
    return ParseResult(
        symbols=symbols,
        call_sites=call_sites,
        imports=imports,
        language="typescript",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _walk_symbols(
    root: Any, file_path: str, source: bytes, *, language_name: str
) -> list[Symbol]:
    """Shared ECMAScript-family walker (TypeScript + JavaScript).

    ``language_name`` only affects nothing in the walk itself; it's accepted so
    the JavaScript module can reuse this exact logic via import.
    """
    symbols: list[Symbol] = []

    def visit(node: Any, parent_class: Optional[str]) -> None:
        ntype = node.type
        if ntype in ("class_declaration", "interface_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
                sym_type = (
                    "interface"
                    if ntype == "interface_declaration"
                    else "class"
                )
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
                # Descend into the class body so methods pick up the parent.
                # Interface bodies hold method_signatures (no body), which we
                # intentionally don't emit — matching the prior regex baseline.
                if ntype == "class_declaration":
                    for child in node.children:
                        visit(child, name)
                return
        elif ntype == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
                sym_type = "method" if parent_class is not None else "function"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent_class),
                        symbol_type=sym_type,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=parent_class,
                    )
                )
                return  # one level deep
        elif ntype == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
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
                return

        for child in node.children:
            visit(child, parent_class)

    visit(root, None)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Walk up to the enclosing method/function; return (name, parent_class)."""
    cur = node.parent
    func_name: Optional[str] = None
    is_method = False
    while cur is not None:
        if cur.type in ("method_definition", "function_declaration") and func_name is None:
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                func_name = _text(name_node, source)
                is_method = cur.type == "method_definition"
        elif cur.type == "class_declaration" and func_name is not None:
            if is_method:
                name_node = cur.child_by_field_name("name")
                parent = _text(name_node, source) if name_node else None
                return (func_name, parent)
            return (func_name, None)
        cur = cur.parent
    if func_name:
        return (func_name, None)
    return None


def _extract_call_sites(
    *,
    root: Any,
    language: Any,
    symbols: list[Symbol],
    file_path: str,
    project_id: str,
    source: bytes,
    language_name: str,
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
        func_name, parent_class = enclosing
        caller_fqn = build_fqn(file_path, func_name, parent_class)
        caller_id = code_symbol_node_id(
            project_id, language_name, file_path, caller_fqn
        )
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
                parser=f"tree-sitter-{language_name}",
                resolved_intra_file=(
                    callee_name in intra_file_names and callee_name != func_name
                ),
            )
        )
    return call_sites


def _extract_imports(root: Any, source: bytes) -> list[str]:
    out: list[str] = []
    for child in root.children:
        if child.type == "import_statement":
            out.append(_text(child, source).strip())
    return out


__all__ = ["parse"]
