from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from chunkshop.sources.parsers.result import ParseResult


def default_cache_root() -> Path:
    env = os.environ.get("CHUNKSHOP_CACHE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "chunkshop" / "derived"


class DerivationCache:
    """Memoises derivations on (content_hash, derivation_hash).

    The key is the provenance: a different model, prompt, or parameter is a
    different derivation_hash and therefore a miss. Re-chunking or changing
    the target table does not touch either half of the key, so it re-derives
    nothing.

    Entries hold derived text — for some corpora that is sensitive material.
    The root is configurable so it can live on an encrypted volume, and
    ``purge()`` deletes everything. There is no encryption-at-rest in v1.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else default_cache_root()

    def _path(self, content_hash: str, derivation_hash: str) -> Path:
        key = hashlib.sha256(f"{content_hash}\x00{derivation_hash}".encode()).hexdigest()
        return self.root / key[:2] / f"{key}.json"

    def get(self, content_hash: str, derivation_hash: str) -> Optional[ParseResult]:
        path = self._path(content_hash, derivation_hash)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ParseResult(text=payload["text"], metadata=payload["metadata"])
        except (OSError, ValueError, KeyError, TypeError):
            # Missing, unreadable, malformed, or structurally wrong entries are
            # misses, never errors — a damaged cache must degrade to recomputation.
            return None

    def put(self, content_hash: str, derivation_hash: str, result: ParseResult) -> None:
        path = self._path(content_hash, derivation_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"text": result.text, "metadata": result.metadata})
        # Atomic write so a crash mid-write cannot leave a half entry.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def purge(self) -> int:
        count = 0
        for f in self.root.rglob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        return count


def cached_derive(
    cache: Optional[DerivationCache],
    content_hash: str,
    derivation_hash: str,
    compute: Callable[[], ParseResult],
) -> tuple[ParseResult, bool]:
    """Return (result, was_cache_hit). A None cache disables memoisation."""
    if cache is None:
        return compute(), False
    hit = cache.get(content_hash, derivation_hash)
    if hit is not None:
        return hit, True
    result = compute()
    cache.put(content_hash, derivation_hash, result)
    return result, False
