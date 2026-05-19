"""ConsolidationChunker — emit episode + atomic-fact chunks via a user-wired
consolidator. Mirrors SummaryEmbedChunker's wrap-base + callable pattern.

Per spec C5/O4: on consolidator failure, degrade to a passthrough episode
chunk (raw text, zero facts, metadata.consolidation_error) — never raise, so
one poisoned session can't abort the nightly cell. Facts are length-capped
(metadata.truncated), not split (splitting breaks the proposition)."""
from __future__ import annotations
import logging

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._consolidator import build_consolidator
from chunkshop.config import ConsolidationChunker as Cfg
from chunkshop.sources.base import Document

logger = logging.getLogger(__name__)


def _strip_transient(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if not k.startswith("_")}


class ConsolidationChunker:
    def __init__(self, cfg: Cfg, base: Chunker, build_chunker=None):
        self.cfg = cfg
        self.base = base
        self._consolidate = build_consolidator(cfg.consolidator)
        self._mode = cfg.consolidator.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        # base.chunk() is intentionally OUTSIDE the O4 try below: a base-chunker
        # failure is a config error (wrong model/doc_type) the user must see,
        # not a degradable per-session consolidator failure. O4 covers only the
        # user-wired consolidator callable.
        base_chunks = self.base.chunk(doc)
        episode_text = "\n".join(c.original_content for c in base_chunks) or doc.content
        meta = _strip_transient(dict(doc.metadata or {}))
        seq = 0
        try:
            result = self._consolidate(episode_text, dict(doc.metadata or {}))
        except Exception as exc:  # O4: degrade, never raise
            logger.warning("consolidator failed for doc %s: %s", doc.id, exc)
            em = {**meta, "kind": "episode", "consolidation_error": str(exc),
                  "consolidator": self._mode, "extractor": self._mode}
            return [Chunk(doc_id=doc.id, seq_num=0,
                          original_content=episode_text,
                          embedded_content=episode_text, metadata=em)]
        out: list[Chunk] = []
        ep_meta = {**meta, "kind": "episode", "consolidator": self._mode,
                   "extractor": self._mode}
        episode = Chunk(doc_id=doc.id, seq_num=seq,
                        original_content=episode_text,
                        embedded_content=result["summary"] or episode_text,
                        metadata=ep_meta)
        out.append(episode)
        seq += 1
        cap = self.cfg.fact_max_chars
        for f in result["facts"]:
            span = f["support_span"] or ""
            truncated = len(span) > cap
            if truncated:
                span = span[:cap]
            fm = {**meta, "kind": "fact",
                  "subject": f["subject"], "predicate": f["predicate"],
                  "object": f["object"], "support_span": span,
                  "confidence": f["confidence"], "truncated": truncated,
                  "source_chunk_seq": episode.seq_num,
                  "consolidator": self._mode, "extractor": self._mode}
            out.append(Chunk(doc_id=doc.id, seq_num=seq,
                             original_content=span, embedded_content=span,
                             metadata=fm))
            seq += 1
        return out
