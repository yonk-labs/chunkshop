"""Pluggable per-extension file parsers (lazy-import their backing libs)."""
from __future__ import annotations

from chunkshop.sources.parsers.base import FileParser, ParserError
from chunkshop.sources.parsers.text import TextParser

__all__ = ["FileParser", "ParserError", "TextParser"]
