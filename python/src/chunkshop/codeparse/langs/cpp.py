"""C++ symbol + call-site extraction via tree-sitter.

Mirrors :mod:`chunkshop.codeparse.langs.rust` in shape, swapping the grammar
package and node names. C++ groups methods two ways:

* **Inline** — a ``function_definition`` inside a ``class_specifier`` /
  ``struct_specifier``'s ``field_declaration_list``. The enclosing type's
  ``name`` (a ``type_identifier``) is the method's ``parent_name``. We descend
  into the type body and visit its ``function_definition`` children with the
  class name as parent.
* **Out-of-line** — a top-level ``function_definition`` written
  ``int Calculator::add(...) {}``. Its ``function_declarator``'s name is a
  ``qualified_identifier`` whose ``scope`` is the class and ``name`` is the
  method. We emit it as a method with ``parent_name`` = the qualifier.

A free top-level ``function_definition`` lands as ``function``. ``namespace``
bodies are descended into, but the namespace is NOT prepended to FQNs in v1 —
``parent_name`` stays class-based only.

Scope is one level deep: a call inside a lambda (``lambda_expression``, not a
``function_definition``) attributes to the OUTERMOST enclosing
``function_definition`` — the emitted symbol. On any import/parse/query
failure the caller drops through to the regex fallback; this module signals
failure by raising, never by returning a bogus ParseResult.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-cpp"

_CALL_QUERY = (
    "(call_expression function: (identifier) @callee_name) @call\n"
    "(call_expression function: (field_expression"
    " field: (field_identifier) @callee_name)) @call"
)

_TYPE_SYMBOLS = {"class_specifier", "struct_specifier"}
# Wrappers that sit between a function_definition and its function_declarator.
_DECLARATOR_WRAPPERS = {"pointer_declarator", "reference_declarator"}
# Leaf node types that carry a function/method name.
_NAME_NODES = {
    "identifier",
    "field_identifier",
    "qualified_identifier",
    "operator_name",
}


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse C++ source via tree-sitter. Raises on import or parse error."""
    import tree_sitter_cpp  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_cpp.language())
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
        language="cpp",
        parser="tree-sitter",
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _declarator_name_node(node: Any) -> Optional[Any]:
    """Return the ``function_declarator`` for a ``function_definition``.

    The ``declarator`` field may be wrapped in one or more
    ``pointer_declarator`` / ``reference_declarator`` layers (``int* f()`` /
    ``int& f()``); peel them to reach the ``function_declarator``.
    """
    cur = node.child_by_field_name("declarator")
    while cur is not None and cur.type in _DECLARATOR_WRAPPERS:
        cur = cur.child_by_field_name("declarator")
    if cur is not None and cur.type == "function_declarator":
        return cur
    return None


def _func_name_and_qualifier(
    node: Any, source: bytes
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(name, qualifier)`` for a ``function_definition``.

    ``name`` is the final identifier text; ``qualifier`` is the class name for
    an out-of-line ``Class::method`` definition, else ``None``. Returns
    ``(None, None)`` if no name node can be resolved.
    """
    fdecl = _declarator_name_node(node)
    if fdecl is None:
        return (None, None)
    name_node = fdecl.child_by_field_name("declarator")
    if name_node is None or name_node.type not in _NAME_NODES:
        return (None, None)
    if name_node.type == "qualified_identifier":
        scope = name_node.child_by_field_name("scope")
        nm = name_node.child_by_field_name("name")
        qualifier = _text(scope, source) if scope is not None else None
        name = _text(nm, source) if nm is not None else _text(name_node, source)
        return (name, qualifier)
    return (_text(name_node, source), None)


def _type_name(node: Any, source: bytes) -> Optional[str]:
    """The ``name`` of a ``class_specifier`` / ``struct_specifier``."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _text(name_node, source)
    return None


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    symbols: list[Symbol] = []

    def emit_function(node: Any, parent: Optional[str]) -> None:
        name, qualifier = _func_name_and_qualifier(node, source)
        if name is None:
            return
        eff_parent = parent if parent is not None else qualifier
        sym_type = "method" if eff_parent is not None else "function"
        symbols.append(
            Symbol(
                name=name,
                fqn=build_fqn(file_path, name, eff_parent),
                symbol_type=sym_type,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent_name=eff_parent,
            )
        )

    def visit(node: Any, parent: Optional[str]) -> None:
        ntype = node.type
        if ntype in _TYPE_SYMBOLS:
            type_name = _type_name(node, source)
            if type_name is not None:
                symbols.append(
                    Symbol(
                        name=type_name,
                        fqn=build_fqn(file_path, type_name, None),
                        symbol_type="class",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        parent_name=None,
                    )
                )
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.children:
                    visit(child, type_name)
            return
        if ntype == "function_definition":
            emit_function(node, parent)
            # Don't descend — one level deep (nested defs not emitted).
            return

        for child in node.children:
            visit(child, parent)

    visit(root, None)
    return symbols


def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing ``function_definition`` as (name, parent).

    Mirrors :func:`_walk_symbols`: a call inside a lambda (or any nested
    construct) rolls up to the outermost emitted ``function_definition``, and
    the parent is the nearest enclosing ``class_specifier`` /
    ``struct_specifier`` name (inline method), else the ``qualified_identifier``
    qualifier (out-of-line method), else None (free function). Returns None for
    a namespace-level / module-level call.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "function_definition":
            outermost = cur  # keep climbing; the last (highest) wins
        cur = cur.parent
    if outermost is None:
        return None
    name, qualifier = _func_name_and_qualifier(outermost, source)
    if name is None:
        return None
    parent: Optional[str] = qualifier
    if parent is None:
        anc = outermost.parent
        while anc is not None:
            if anc.type in _TYPE_SYMBOLS:
                parent = _type_name(anc, source)
                break
            anc = anc.parent
    return (name, parent)


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
        caller_id = code_symbol_node_id(project_id, "cpp", file_path, caller_fqn)
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
