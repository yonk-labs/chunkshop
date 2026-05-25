"""Pluggable per-extension file parsers.

Importing parser *classes* is safe: backing libs (pypdf, python-docx, etc.)
import lazily inside each parser's `parse()` method, so this module loads
with zero parser extras installed.
"""

from __future__ import annotations

from chunkshop.sources.parsers.base import FileParser, ParserError
from chunkshop.sources.parsers.docx import DOCXParser
from chunkshop.sources.parsers.html import HTMLParser
from chunkshop.sources.parsers.pdf import PDFParser
from chunkshop.sources.parsers.pptx import PPTXParser
from chunkshop.sources.parsers.text import TextParser
from chunkshop.sources.parsers.xlsx import XLSXParser

_TEXT = TextParser()

DEFAULT_PARSERS: dict[str, FileParser] = {
    # Text family (cheap, no extras).
    "txt": _TEXT,
    "md": _TEXT,
    "markdown": _TEXT,
    "rst": _TEXT,
    "log": _TEXT,
    "csv": _TEXT,
    "tsv": _TEXT,
    # Rich formats (each behind an optional extra).
    "pdf": PDFParser(),
    "docx": DOCXParser(),
    "pptx": PPTXParser(),
    "xlsx": XLSXParser(),
    "html": HTMLParser(),
    "htm": HTMLParser(),
}


def get_parser(ext: str, parsers: dict[str, FileParser] | None = None) -> FileParser:
    """Return the parser for `ext` (case-insensitive, leading dot tolerated).

    Falls back to a TextParser when the extension is unknown. Custom parser
    overrides take precedence over the defaults.
    """
    table = parsers or DEFAULT_PARSERS
    key = ext.lower().lstrip(".")
    return table.get(key, _TEXT)


__all__ = [
    "FileParser",
    "ParserError",
    "DEFAULT_PARSERS",
    "get_parser",
    "TextParser",
    "PDFParser",
    "DOCXParser",
    "PPTXParser",
    "XLSXParser",
    "HTMLParser",
]
