"""JavaScript symbol + call-site extraction via tree-sitter.

JavaScript and TypeScript share the same ECMAScript grammar shape
(``class_declaration`` / ``function_declaration`` / ``method_definition`` /
``call_expression`` / ``import_statement``), so the symbol walk, call-site
extraction, and import scan are reused verbatim from
:mod:`chunkshop.codeparse.langs.typescript`. Only the grammar package and the
language tag differ here. (TypeScript-only ``interface_declaration`` nodes
simply never appear in a JS parse tree, so sharing the walker is safe.)

Both modules import their tree-sitter grammar lazily inside ``parse`` so plain
``import chunkshop.codeparse`` stays light. On any import / parse failure the
caller falls through to the regex fallback.
"""
from __future__ import annotations

from chunkshop.codeparse.base import ParseResult
from chunkshop.codeparse.langs.typescript import (
    _extract_call_sites,
    _extract_imports,
    _walk_symbols,
)


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Parse JavaScript source via tree-sitter. Raises on import/parse error."""
    import tree_sitter_javascript  # local import: lazy per chunkshop convention

    from tree_sitter import Language, Parser  # type: ignore[import-untyped]

    language = Language(tree_sitter_javascript.language())
    parser = Parser(language)
    tree = parser.parse(source)
    root = tree.root_node

    symbols = _walk_symbols(root, file_path, source, language_name="javascript")
    call_sites = _extract_call_sites(
        root=root,
        language=language,
        symbols=symbols,
        file_path=file_path,
        project_id=project_id,
        source=source,
        language_name="javascript",
    )
    imports = _extract_imports(root, source)
    return ParseResult(
        symbols=symbols,
        call_sites=call_sites,
        imports=imports,
        language="javascript",
        parser="tree-sitter",
    )


__all__ = ["parse"]
