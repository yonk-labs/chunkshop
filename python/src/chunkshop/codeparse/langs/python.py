"""Python symbol + call-site extraction via tree-sitter.

Lifts the per-language extractor pattern from yonk-full-stack-mig-srv
``edge_queries/python.py`` with two refinements:

1. ``tree_sitter_python`` is imported lazily on first parse so plain
   ``import chunkshop.codeparse`` stays light.
2. Returns chunkshop's pydantic ``ParseResult`` (with ``call_sites``
   embedded) instead of the migration server's three-tuple. Downstream
   chunkers / extractors can consume one object.

On any import or query failure the caller (``tree_sitter_wrapper``) drops
through to the regex fallback. That's the contract: this module is best-
effort and signals failure by raising — never by returning a bogus
ParseResult.
"""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

_PARSER_NAME = "tree-sitter-python"

_CALL_QUERY = (
    "(call function: (identifier) @callee_name) @call\n"
    "(call function: (attribute attribute: (identifier) @callee_name)) @call"
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse Python source via tree-sitter. Raises on import or parse error.

    The "raises on failure" contract is what lets the wrapper layer cleanly
    fall through to regex without inspecting return shapes.
    """
    import tree_sitter_python  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_python.language())
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
        language="python",
        parser="tree-sitter",
    )


def _walk_symbols(root: Any, file_path: str, source: bytes) -> list[Symbol]:
    """Collect classes / functions / methods from the tree.

    A ``function_definition`` nested inside a ``class_definition`` is a
    ``method`` with ``parent_name`` = the enclosing class. Top-level
    ``function_definition`` is a ``function``. Nested functions inside
    other functions are NOT emitted — SP-A scope is one level deep, same
    as the migration server.
    """
    symbols: list[Symbol] = []

    def _span(node: Any) -> tuple[int, int]:
        """1-based inclusive span, widened to the decorator block if any.

        A decorated ``def``/``class`` is wrapped in a ``decorated_definition``
        node whose first child is the ``@decorator``. Without this, the span
        would start at the ``def``/``class`` line and silently drop the
        decorator lines from the symbol's ``original_content``.
        """
        span_node = node
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            span_node = parent
        return (span_node.start_point[0] + 1, span_node.end_point[0] + 1)

    def visit(node: Any, parent_class: Optional[str]) -> None:
        ntype = node.type
        if ntype == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = source[name_node.start_byte : name_node.end_byte].decode(
                    errors="replace"
                )
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, None),
                        symbol_type="class",
                        line_start=_span(node)[0],
                        line_end=_span(node)[1],
                        parent_name=None,
                    )
                )
                for child in node.children:
                    visit(child, name)
                return
        elif ntype == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = source[name_node.start_byte : name_node.end_byte].decode(
                    errors="replace"
                )
                sym_type = "method" if parent_class is not None else "function"
                symbols.append(
                    Symbol(
                        name=name,
                        fqn=build_fqn(file_path, name, parent_class),
                        symbol_type=sym_type,
                        line_start=_span(node)[0],
                        line_end=_span(node)[1],
                        parent_name=parent_class,
                    )
                )
                # Don't descend — SP-A is one level deep.
                return

        for child in node.children:
            visit(child, parent_class)

    visit(root, None)
    return symbols


def _enclosing_function(node: Any, source: bytes) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing function/method as (name, parent_class).

    Mirrors :func:`_walk_symbols`' "one level deep" emission rule: nested
    functions are never emitted as Symbols, so a call inside one must be
    attributed to the outermost enclosing function (the symbol that WAS
    emitted), not the innermost. Returns None for a module-level call (no
    enclosing function — there is no symbol to attribute to).
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "function_definition":
            outermost = cur  # keep climbing; the last one wins (highest)
        cur = cur.parent
    if outermost is None:
        return None
    name_node = outermost.child_by_field_name("name")
    if name_node is None:
        return None
    func_name = source[name_node.start_byte : name_node.end_byte].decode(
        errors="replace"
    )
    # parent_class = nearest class_definition ancestor of the outermost
    # function. Because the outermost function has no function ancestor, any
    # enclosing class makes it a method (matching _walk_symbols).
    parent_class: Optional[str] = None
    anc = outermost.parent
    while anc is not None:
        if anc.type == "class_definition":
            cname = anc.child_by_field_name("name")
            if cname is not None:
                parent_class = source[
                    cname.start_byte : cname.end_byte
                ].decode(errors="replace")
            break
        anc = anc.parent
    return (func_name, parent_class)


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
        callee_name = source[
            callee_node.start_byte : callee_node.end_byte
        ].decode(errors="replace")
        enclosing = _enclosing_function(callee_node, source)
        if enclosing is None:
            continue
        func_name, parent_class = enclosing
        caller_fqn = build_fqn(file_path, func_name, parent_class)
        caller_id = code_symbol_node_id(project_id, "python", file_path, caller_fqn)
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
        if child.type in ("import_statement", "import_from_statement"):
            out.append(
                source[child.start_byte : child.end_byte]
                .decode(errors="replace")
                .strip()
            )
    return out


__all__ = ["parse"]
