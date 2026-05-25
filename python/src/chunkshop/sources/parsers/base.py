"""Per-extension file parsers. parse(path) returns the extracted text body;
FilesSource wraps that into a Document. Parsers raise ParserError on a file
they recognize but cannot read, and a RuntimeError with an install hint when
their optional library is missing."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class ParserError(Exception):
    """A recognized file could not be parsed (corrupt, encrypted, malformed)."""


@runtime_checkable
class FileParser(Protocol):
    supported_extensions: list[str]

    def parse(self, path: Path) -> str: ...
