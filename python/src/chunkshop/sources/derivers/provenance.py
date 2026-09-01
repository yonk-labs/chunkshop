from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from chunkshop.sources.parsers.result import ParseResult

# Bump when the *shape* of the derivation hash changes, so old cache entries
# invalidate cleanly instead of being served against a new formula.
CACHE_SCHEMA_VERSION = "1"


def _params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def derivation_hash(
    rung: str, model_id: str, model_version: str, params: dict
) -> str:
    """Identity of *how* a derivation was produced.

    Combined with the content hash this forms the cache key, which means the
    cache key IS the provenance record: change the model, the version, or any
    parameter and the derivation is redone rather than silently reused.
    """
    parts = [CACHE_SCHEMA_VERSION, rung, model_id, model_version, _params_hash(params)]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def stamp_provenance(
    result: ParseResult,
    *,
    rung: str,
    model_id: str,
    model_version: str,
    params: dict,
) -> ParseResult:
    """Return a new ParseResult carrying full derivation provenance."""
    metadata = {
        **result.metadata,
        "content_origin": "derived",
        "derived_rung": rung,
        "derived": {
            "rung": rung,
            "model": model_id,
            "model_version": model_version,
            "params_hash": _params_hash(params),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
    return ParseResult(text=result.text, metadata=metadata)
