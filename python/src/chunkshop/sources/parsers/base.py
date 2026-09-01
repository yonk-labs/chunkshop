"""Per-extension file parsers and derivers. FileParser.parse(path) and
Deriver.derive(path) both extract a file's content; FilesSource wraps the
result into a Document. A deriver may return a bare str (the legacy
FileParser contract) or a ParseResult carrying text plus metadata (e.g.
image dimensions, model provenance) — call_deriver normalises either into a
ParseResult and prefers .derive() over .parse() when an object implements
both. Parsers/derivers raise ParserError on a file they recognize but cannot
read, and a RuntimeError with an install hint when their optional library is
missing."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from chunkshop.sources.parsers.result import ParseResult, normalize_parse_result


class ParserError(Exception):
    """A recognized file could not be parsed (corrupt, encrypted, malformed)."""


@runtime_checkable
class FileParser(Protocol):
    supported_extensions: list[str]

    def parse(self, path: Path) -> str: ...


class EmptyDerivationError(ParserError):
    """A file was recognised and read, but produced no text.

    Raised instead of silently indexing an empty document — a scanned PDF
    with no embedded text is a derivation failure, not a zero-length success.
    """


@runtime_checkable
class Deriver(Protocol):
    """Generalisation of FileParser: may return metadata alongside text.

    ``FileParser`` remains valid and supported — a deriver may implement
    either ``derive`` or the legacy ``parse``, and either may return a bare
    ``str``. ``call_deriver`` picks whichever exists.
    """

    supported_extensions: list[str]

    def derive(self, path: Path) -> str | ParseResult: ...


def call_deriver(obj, path: Path) -> ParseResult:
    """Invoke a deriver or a legacy parser and normalise its result."""
    fn = getattr(obj, "derive", None) or getattr(obj, "parse", None)
    if fn is None:
        raise TypeError(
            f"{type(obj).__name__} has neither .derive() nor .parse()"
        )
    return normalize_parse_result(fn(path))
