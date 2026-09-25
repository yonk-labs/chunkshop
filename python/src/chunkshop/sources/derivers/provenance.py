from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from chunkshop.sources.parsers.result import ParseResult

# Bump when the *shape* of the derivation hash changes, so old cache entries
# invalidate cleanly instead of being served against a new formula.
#
# Task 6 added `engine` (5 parts -> 6) WITHOUT bumping this: the media
# deriver cache has never shipped (feat/media-ingest-v1a is unmerged), so
# there were zero cache entries in the wild to invalidate. That reasoning
# still holds for `engine` and is kept here as the record of a deliberate
# NON-bump.
#
# "1" -> "2" IS a bump, for a different and stronger reason than shape
# alone: the cache key gained a third component (the per-deriver
# `cache_key_extra`, see `cache.DerivationCache._path`) to fix a silent
# data-corruption bug. Before this bump the key was
# H(content_hash, derivation_hash) with the file path in NEITHER half,
# while `ImageMetaDeriver` derives its output text FROM the path — so two
# byte-identical images with different names collided and the second
# silently received the first's derived text (and, through
# `ClipTagsDeriver`, the first image's CLIP tags). Any pre-existing entry
# may therefore hold poisoned data attributed to the wrong document, so
# old entries must not merely be re-shaped, they must be unreachable.
#
# GAP-003 (2026-09) — stripping exiftool's `Directory`/`File*`/`SourceFile`
# keys out of `RossDeriver._build_result`'s `ross_metadata.exif` is ALSO a
# NON-bump, same reasoning as `engine` above: rung 5 (`ross`) has not
# shipped either (feat/rust-media-parity is unmerged), so there are zero
# `ross` cache entries in the wild carrying the poisoned keys to invalidate.
CACHE_SCHEMA_VERSION = "2"


def _params_hash(params: dict) -> str:
    """Compute a stable hash of params, raising on non-JSON-serializable values.

    Raises TypeError if any param value is not JSON-serializable, naming the
    offending key so the caller knows exactly which param to fix.
    """
    try:
        canonical = json.dumps(
            params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except TypeError as e:
        # Re-raise with the key name if possible
        for key in params:
            try:
                json.dumps(params[key])
            except TypeError:
                raise TypeError(
                    f"Parameter '{key}' is not JSON-serializable: {type(params[key]).__name__}"
                ) from e
        raise
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def derivation_hash(
    rung: str, model_id: str, model_version: str, engine: str, params: dict
) -> str:
    """Identity of *how* a derivation was produced.

    Combined with the content hash this forms the cache key, which means the
    cache key IS the provenance record: change the model, the version, the
    engine, or any parameter and the derivation is redone rather than
    silently reused.

    `engine` distinguishes implementations that can diverge even with the
    same model id/version (e.g. Python's fastembed vs Rust's fastembed-rs,
    or CTranslate2 vs whisper-rs for ASR) — SC-12's contract-level parity
    clause requires the engine be attributable, not just the model.

    All param values must be JSON-serializable; raises TypeError with the
    offending key name if not.
    """
    return _hash_from_parts(
        rung, model_id, model_version, engine, _params_hash(params)
    )


def _hash_from_parts(
    rung: str, model_id: str, model_version: str, engine: str, params_hash: str
) -> str:
    """The hash assembly itself, taking an ALREADY-COMPUTED ``params_hash``.

    Split out so a test can rebuild the hash from a stamped provenance
    record — which carries ``params_hash``, not the params themselves —
    without restating the join. That matters because ``derivation_hash()``
    and ``stamp_provenance()`` are called with two SEPARATE params literals
    at every deriver's call site; a test that supplies its own third literal
    cannot see them drift apart, while one that feeds the recorded
    ``params_hash`` back through here can.
    """
    parts = [CACHE_SCHEMA_VERSION, rung, model_id, model_version, engine, params_hash]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def stamp_provenance(
    result: ParseResult,
    *,
    rung: str,
    model_id: str,
    model_version: str,
    engine: str,
    params: dict,
) -> ParseResult:
    """Return a new ParseResult carrying full derivation provenance.

    Note: the returned metadata is a shallow copy of the input metadata;
    nested mutable values are shared by reference.
    """
    metadata = {
        **result.metadata,
        "content_origin": "derived",
        "derived_rung": rung,
        "derived": {
            "rung": rung,
            "model": model_id,
            "model_version": model_version,
            "engine": engine,
            "params_hash": _params_hash(params),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
    return ParseResult(text=result.text, metadata=metadata)
