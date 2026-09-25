from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Optional

from chunkshop.sources.parsers.result import ParseResult

#: The ONLY shape a real cache entry can have: `<2 hex>/<64 hex>.json`,
#: relative to root. Anything else sharing the root — a stray
#: `package.json`, a deeply/shallowly nested `.json`, or a `.json` in a
#: `<2 hex>` directory that doesn't match its own filename prefix — is not
#: chunkshop's and must survive both `stats()` and `purge()`.
_ENTRY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}\.json$")


def _iter_entries(root: Path) -> Iterator[Path]:
    for f in root.glob("*/*.json"):
        rel = f.relative_to(root).as_posix()
        if _ENTRY_RE.match(rel) and f.parent.name == f.name[:2]:
            yield f


def _iter_orphan_tmp(root: Path) -> Iterator[Path]:
    """`put()`'s atomic write (`mkstemp` + `os.replace`) leaves a `*.tmp`
    sibling in the entry's own `<2 hex>/` subdirectory if a crash lands
    between the two. These are chunkshop's own leftovers — never counted
    as entries (they never got renamed into place) but safe for `purge()`
    to sweep.
    """
    for f in root.glob("*/*.tmp"):
        if re.fullmatch(r"[0-9a-f]{2}", f.parent.name):
            yield f


def default_cache_root() -> Path:
    env = os.environ.get("CHUNKSHOP_CACHE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "chunkshop" / "derived"


class DerivationCache:
    """Memoises derivations on (content_hash, derivation_hash, extra).

    The key is the provenance: a different model, prompt, or parameter is a
    different derivation_hash and therefore a miss. Re-chunking or changing
    the target table does not touch any part of the key, so it re-derives
    nothing.

    ``extra`` is the deriver's own per-document cache-key contribution
    (``Deriver.cache_key_extra(path)``), and it exists because
    content-addressing alone is only sound for a deriver that is a PURE
    function of (bytes, params). ``ImageMetaDeriver`` is not: it derives its
    output text from the filename and path segments, so two byte-identical
    images with different names must NOT share an entry. Rather than fold
    the path into every key — which would defeat content-addressing for the
    derivers that genuinely are pure, e.g. two copies of the same PDF that
    should derive once — each deriver declares what per-document material it
    actually consumes. Pure derivers contribute ``None`` and keep the exact
    sharing behaviour they had before.

    Entries hold derived text — for some corpora that is sensitive material.
    The root is configurable so it can live on an encrypted volume, and
    ``purge()`` deletes everything. There is no encryption-at-rest in v1.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else default_cache_root()

    def _path(
        self,
        content_hash: str,
        derivation_hash: str,
        extra: Optional[str] = None,
    ) -> Path:
        # `extra` is always folded in — as the empty string when a deriver
        # contributes nothing — so there is exactly ONE key formula rather
        # than a two-shape scheme where a pure deriver and a deriver whose
        # extra happens to be "" could land on different paths.
        material = f"{content_hash}\x00{derivation_hash}\x00{extra or ''}"
        key = hashlib.sha256(material.encode()).hexdigest()
        return self.root / key[:2] / f"{key}.json"

    def get(
        self,
        content_hash: str,
        derivation_hash: str,
        extra: Optional[str] = None,
    ) -> Optional[ParseResult]:
        path = self._path(content_hash, derivation_hash, extra)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ParseResult(text=payload["text"], metadata=payload["metadata"])
        except (OSError, ValueError, KeyError, TypeError):
            # Missing, unreadable, malformed, or structurally wrong entries are
            # misses, never errors — a damaged cache must degrade to recomputation.
            return None

    def put(
        self,
        content_hash: str,
        derivation_hash: str,
        result: ParseResult,
        extra: Optional[str] = None,
    ) -> None:
        path = self._path(content_hash, derivation_hash, extra)
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

    def stats(self) -> tuple[int, int]:
        """Return ``(entry_count, total_bytes)`` for the entries under ``root``.

        Walks exactly the same set ``purge()`` deletes — files matching the
        cache's own layout, ``<2 hex>/<64 hex>.json`` — so ``chunkshop cache
        info``'s count is a truthful preview of what a purge will remove,
        and unrelated files sharing the root (at any depth, any name) are
        neither counted nor deleted. A missing root walks to nothing and
        reports ``(0, 0)`` rather than raising; an entry that vanishes
        mid-walk still counts (purge would have counted it too) but
        contributes no size.
        """
        count = 0
        total = 0
        for f in _iter_entries(self.root):
            count += 1
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return count, total

    def purge(self) -> int:
        count = 0
        for f in _iter_entries(self.root):
            f.unlink(missing_ok=True)
            count += 1
        # Not counted in the return value (they were never entries), but
        # chunkshop's own — sweep the crash leftovers while we're here.
        for f in _iter_orphan_tmp(self.root):
            f.unlink(missing_ok=True)
        return count


def cached_derive(
    cache: Optional[DerivationCache],
    content_hash: str,
    derivation_hash: str,
    compute: Callable[[], ParseResult],
    extra: Optional[str] = None,
) -> tuple[ParseResult, bool]:
    """Return (result, was_cache_hit). A None cache disables memoisation.

    ``extra`` is the deriver's ``cache_key_extra(path)`` contribution; see
    ``DerivationCache`` for why it is per-deriver rather than always the
    path.
    """
    if cache is None:
        return compute(), False
    hit = cache.get(content_hash, derivation_hash, extra)
    if hit is not None:
        return hit, True
    result = compute()
    cache.put(content_hash, derivation_hash, result, extra)
    return result, False
