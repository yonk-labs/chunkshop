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
# there are zero cache entries in the wild to invalidate. Once this ships,
# a future shape change DOES need the bump.
CACHE_SCHEMA_VERSION = "1"


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
    parts = [CACHE_SCHEMA_VERSION, rung, model_id, model_version, engine, _params_hash(params)]
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
