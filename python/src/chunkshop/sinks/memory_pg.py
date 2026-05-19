"""MemorySink — PgSink + agent-memory write semantics.

Owns the cell-level promoted fields so chunkers/framers stay memory-agnostic:
stamps tier/namespace/recorded_at unconditionally and defaults kind='episode'
when absent. Adds supersede (consolidated replaces provisional per session)
and soft-invalidate (contradicted prior facts -> retracted). Strips leftover
'_'-prefixed transient metadata keys before insert."""
from __future__ import annotations
import datetime as _dt
from dataclasses import replace

from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.pg import PgSink


def _iso(ts):
    """Coerce an epoch (int/float, as emitted by SessionStagingSource) to an
    ISO-8601 UTC string for the timestamptz promote columns. The framer needs
    `ts` numeric for gap arithmetic, but `effective_from`/`effective_to` are
    timestamptz; ISO strings adapt to timestamptz (same path as recorded_at)
    and stay JSON-serializable for the metadata jsonb column."""
    if ts is None or isinstance(ts, str):
        return ts
    if isinstance(ts, (int, float)):
        return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()
    if isinstance(ts, _dt.datetime):
        return ts.isoformat()
    return ts


class MemorySink(PgSink):
    def __init__(self, cfg, backend, embed_dim: int):
        super().__init__(cfg=cfg, backend=backend, embed_dim=embed_dim)
        self._mem = cfg.memory
        self._namespace = self._mem.namespace or cfg.source_tag
        self._recorded_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        self._superseded: set[str] = set()
        self._seq_base: dict[str, int] = {}

    def _row_id(self, doc_id: str, seq_num: int) -> str:
        return f"{self._namespace}::{doc_id}::{seq_num}"

    def _stamp(self, chunks: list[Chunk]) -> list[Chunk]:
        out = []
        for c in chunks:
            m = {k: v for k, v in c.metadata.items() if not k.startswith("_")}
            m.setdefault("kind", "episode")
            m.setdefault("retracted", False)
            m["tier"] = self._mem.tier
            m["namespace"] = self._namespace
            m["recorded_at"] = self._recorded_at
            m.setdefault("effective_from", _iso(m.get("episode_end_ts")))
            out.append(replace(c, metadata=m))
        return out

    def write_document(self, doc_id, chunks, embeddings, tags_per_chunk) -> None:
        chunks = self._stamp(chunks)
        base = self._seq_base.get(doc_id, 0)
        chunks = [replace(c, seq_num=base + i) for i, c in enumerate(chunks)]
        self._seq_base[doc_id] = base + len(chunks)
        # NOTE: supersede DELETE, super().write_document insert, and _invalidate
        # run as three separate transactions (not atomic). This is intentional
        # and crash-safe: delete-then-insert is an idempotent rebuild on rerun.
        if (self._mem.supersede and self._mem.tier == "consolidated"
                and doc_id not in self._superseded):
            with self.backend.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND source = %s",
                    (doc_id, self.cfg.source_tag))
                conn.commit()
            self._superseded.add(doc_id)
        super().write_document(doc_id, chunks, embeddings, tags_per_chunk)
        self._invalidate(chunks)

    def _invalidate(self, chunks: list[Chunk]) -> None:
        facts = [c for c in chunks if c.metadata.get("kind") == "fact"
                 and c.metadata.get("subject") and c.metadata.get("predicate")
                 and c.metadata.get("effective_from") is not None]
        if not facts:
            return
        with self.backend.connect() as conn, conn.cursor() as cur:
            for c in facts:
                m = c.metadata
                cur.execute(
                    f"UPDATE {self._fq()} SET retracted = true, "
                    f"retracted_at = now(), effective_to = %s::timestamptz "
                    f"WHERE source = %s AND subject = %s AND predicate = %s "
                    f"AND effective_from < %s::timestamptz "
                    f"AND coalesce(retracted, false) = false",
                    (m.get("effective_from"), self.cfg.source_tag,
                     m.get("subject"), m.get("predicate"),
                     m.get("effective_from")))
            conn.commit()
