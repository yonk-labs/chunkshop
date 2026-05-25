"""Symbol-aware code parsing primitives for chunkshop.

Public surface (stable for SP-B / SP-C / SP-D)::

    from chunkshop.codeparse import (
        parse_file,
        Symbol,
        CallSite,
        ParseResult,
        build_fqn,
        code_symbol_node_id,
    )

The package is intentionally light: importing it does NOT pull in
tree-sitter or any language grammar. Each language module imports its
tree-sitter package lazily on first ``parse_file()`` call.

When the optional ``chunkshop[code]`` extra isn't installed (or a
specific language grammar is missing), ``parse_file`` falls through to a
regex extractor. Coverage is reduced but never zero — every supported
language always returns a non-empty ParseResult for a non-empty file
that contains recognisable declarations.
"""
from __future__ import annotations

from chunkshop.codeparse.base import CallSite, ParseResult, Symbol
from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id
from chunkshop.codeparse.tree_sitter_wrapper import parse_file

__all__ = [
    "CallSite",
    "ParseResult",
    "Symbol",
    "build_fqn",
    "code_symbol_node_id",
    "parse_file",
]
