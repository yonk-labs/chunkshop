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


class AmbiguousDeriverError(ParserError):
    """Two different `derive:` keys in a `files` source config both route
    the same file extension to a deriver (Task 9 Correction 2, chunkshop
    Rust media-parity plan).

    `FilesSource.__init__` builds its per-extension deriver table by
    iterating `cfg.derive.items()` in dict order, and Python dicts preserve
    YAML insertion order — so ordinarily the *later* key silently wins on a
    collision. The Rust port's `derive:` field is a `BTreeMap` (lexicographic
    key order) for its own good reasons, which means the two languages can
    pick OPPOSITE winners for the byte-identical YAML config. Rather than
    let that silently diverge across ports, both languages reject the
    ambiguity outright with a named error citing the extension and both
    colliding keys.
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
