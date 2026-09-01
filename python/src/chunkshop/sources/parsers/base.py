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

    THE CACHE-SOUNDNESS INVARIANT. A deriver participating in the
    derivation cache must be a pure function of its file's CONTENT plus
    everything it declares in the cache key. The key is
    ``H(content_hash, derivation_hash, cache_key_extra)`` — the content
    bytes, the (rung, model, version, engine, params) tuple, and whatever
    per-document material the deriver names. Anything else a deriver reads
    — the filename, the parent directory, the mtime, an environment
    variable — is invisible to the key, so two files that agree on the key
    are served each other's output. That is not a hypothetical: it shipped
    as a silent data-corruption bug, because ``ImageMetaDeriver`` derives
    its output text from the filename while the key contained no path at
    all.

    ``cache_key_extra`` is the escape hatch, not a licence. A deriver that
    consumes per-document material outside its own bytes returns a string
    capturing exactly that material — and only what it actually consumes
    under its CURRENT config — so it stops colliding. A deriver that is
    genuinely pure returns ``None`` (or omits the method) and keeps full
    content-addressed sharing: two copies of the same PDF still derive
    once, which is the property worth preserving. A deriver that wraps
    another must delegate, or it silently drops the inner deriver's
    contribution.

    Both methods are optional for backward compatibility with third-party
    parsers; ``FilesSource`` probes for them with ``getattr``.
    """

    supported_extensions: list[str]

    def derive(self, path: Path) -> str | ParseResult: ...

    def cache_key_extra(self, path: Path) -> str | None:
        """Per-document material this deriver consumes beyond its bytes.

        ``None`` (the default, and the correct answer for any deriver that
        is a pure function of content) preserves content-addressed sharing.
        """
        return None


def cache_key_extra_for(obj, path: Path) -> str | None:
    """Read a deriver's per-document cache-key contribution, if it has one.

    Duck-typed with ``getattr``, matching how ``files.py`` probes for
    ``derivation_hash``: a third-party parser predating this protocol
    method is simply treated as pure, which is what it was before.
    """
    fn = getattr(obj, "cache_key_extra", None)
    return fn(path) if callable(fn) else None


def call_deriver(obj, path: Path) -> ParseResult:
    """Invoke a deriver or a legacy parser and normalise its result."""
    fn = getattr(obj, "derive", None) or getattr(obj, "parse", None)
    if fn is None:
        raise TypeError(
            f"{type(obj).__name__} has neither .derive() nor .parse()"
        )
    return normalize_parse_result(fn(path))
