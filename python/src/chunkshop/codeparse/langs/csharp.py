"""C# symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.typescript` in shape — a class with
methods. ``class_declaration`` lands as ``class``; ``interface_declaration``
lands as ``interface`` (its ``method_declaration`` signatures are NOT emitted —
the interface is a marker, matching the TS/Rust posture). ``method_declaration``
nodes inside a class body become methods with ``parent_name`` set to the
enclosing class.

``namespace_declaration`` (and ``file_scoped_namespace_declaration``) are
descended into transparently; the namespace is NOT prepended to the FQN in v1.

Scope is one level deep: a ``local_function_statement`` (C# local function)
nested inside a method is NOT emitted as a symbol, and a call inside it
attributes to the OUTERMOST enclosing ``method_declaration`` — never the local
function. Local functions are the orphan trigger.

Calls are ``invocation_expression`` nodes: the ``function`` field is either an
``identifier`` (``Foo()``) or a ``member_access_expression`` whose ``name``
field is the called method (``this.Foo()`` / ``obj.Foo()``).

On any import / parse failure the caller drops through to the regex fallback;
this module signals failure by raising.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-csharp"

_CALL_QUERY = (
    "(invocation_expression function: (identifier) @callee_name) @call\n"
    "(invocation_expression function: (member_access_expression"
    " name: (identifier) @callee_name)) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse C# source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_c_sharp  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_c_sharp.language())
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
        language="csharp",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Any, parent: Optional[str]) -> None:
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
                # Interface bodies hold method_declaration signatures we do NOT
                # emit — the interface is a marker (matches TS/Rust posture).
                if ntype == "class_declaration":
                    for child in node.children:
                        visit(child, name)
            return
        if ntype == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _text(name_node, source)
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
            # Don't descend — one level deep. A local_function_statement nested
            # inside the method body is NOT emitted as a symbol.
            return

        for child in node.children:
            visit(child, parent)

    visit(root, None)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing ``method_declaration`` as (name, class).

    Mirrors :func:`_walk_symbols`: a call inside a nested
    ``local_function_statement`` rolls up to the outermost emitted
    ``method_declaration`` (never the local function), and the parent is the
    nearest enclosing ``class_declaration`` name. Returns None for a call with
    no enclosing method.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "method_declaration":
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
        if anc.type == "class_declaration":
            cn = anc.child_by_field_name("name")
            parent = _text(cn, source) if cn is not None else None
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
        caller_id = code_symbol_node_id(
            project_id, "csharp", file_path, caller_fqn
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
                parser=_PARSER_NAME,
                resolved_intra_file=(
                    callee_name in intra_file_names and callee_name != func_name
                ),
            )
        )
    return call_sites


def _extract_imports(root: Any, source: bytes) -> list[str]:
    out: list[str] = []

    def visit(node: Any) -> None:
        if node.type == "using_directive":
            out.append(_text(node, source).strip())
            return
        for child in node.children:
            visit(child)

    visit(root)
    return out


__all__ = ["parse"]
