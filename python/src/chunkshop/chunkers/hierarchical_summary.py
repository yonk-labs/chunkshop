"""HierarchicalSummaryChunker — emit base (fine) + coarse summary rows linked by group_id.

Per brief SC-003/SC-004: each group produces (1) all the base chunks stamped with
``metadata.granularity = "fine"`` + ``metadata.group_id`` and (2) one coarse row
with ``granularity = "coarse"`` carrying the summary of the concatenated group
in ``embedded_content`` (original is the concatenated raw text).

Three grouping strategies:
  - ``fixed_n``:       every N consecutive base chunks form one group.
  - ``word_budget``:   accumulate chunks up to M words per group; new group starts
                       when the next chunk would push the running total over M.
  - ``section_aware``: one group per original heading. Requires ``base.type = "hierarchy"``;
                       enforced at config-load (see config.py model_validator).

``group_id`` is ``{doc.id}::g{group_index}`` — deterministic, stable across reruns,
unique per (doc, group) tuple. Not a UUID to keep joins greppable.
"""
from __future__ import annotations
from dataclasses import replace

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import (
    HierarchicalSummaryChunker as Cfg,
    FixedNGrouping,
    WordBudgetGrouping,
    SectionAwareGrouping,
)
from chunkshop.sources.base import Document


class HierarchicalSummaryChunker:
    def __init__(self, cfg: Cfg, base: Chunker):
        self.cfg = cfg
        self.base = base
        self._summarize = build_summarizer(cfg.summarizer)
        self._mode = cfg.summarizer.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        if not base_chunks:
            return []
        groups = self._group(base_chunks)
        doc_meta = dict(doc.metadata or {})

        out: list[Chunk] = []
        seq = 0
        for group_idx, group_chunks in enumerate(groups):
            group_id = f"{doc.id}::g{group_idx}"

            # Fine rows: preserve base metadata, stamp granularity + group_id.
            for bc in group_chunks:
                meta = {
                    **bc.metadata,
                    "granularity": "fine",
                    "group_id": group_id,
                    "summarizer": self._mode,
                }
                out.append(replace(bc, seq_num=seq, metadata=meta))
                seq += 1

            # One coarse row per group — summary of the joined group text.
            joined = "\n\n".join(c.original_content for c in group_chunks)
            summary = self._summarize(joined, doc_meta)
            out.append(Chunk(
                doc_id=doc.id,
                seq_num=seq,
                original_content=joined,
                embedded_content=summary,
                metadata={
                    "granularity": "coarse",
                    "group_id": group_id,
                    "summarizer": self._mode,
                    "strategy": "hierarchical_summary",
                },
            ))
            seq += 1
        return out

    def _group(self, chunks: list[Chunk]) -> list[list[Chunk]]:
        g = self.cfg.grouping
        if isinstance(g, FixedNGrouping):
            n = g.n
            return [chunks[i:i + n] for i in range(0, len(chunks), n)]
        if isinstance(g, WordBudgetGrouping):
            groups: list[list[Chunk]] = []
            cur: list[Chunk] = []
            cur_words = 0
            for c in chunks:
                w = len(c.original_content.split())
                if cur and cur_words + w > g.max_words:
                    groups.append(cur)
                    cur = [c]
                    cur_words = w
                else:
                    cur.append(c)
                    cur_words += w
            if cur:
                groups.append(cur)
            return groups
        if isinstance(g, SectionAwareGrouping):
            # base.type must be 'hierarchy'; enforced at config-load time.
            # Hierarchy chunks carry metadata['heading'] per section.
            groups: list[list[Chunk]] = []
            _SENTINEL = object()
            cur_heading = _SENTINEL
            cur: list[Chunk] = []
            for c in chunks:
                h = c.metadata.get("heading")
                if h != cur_heading and cur:
                    groups.append(cur)
                    cur = []
                cur_heading = h
                cur.append(c)
            if cur:
                groups.append(cur)
            return groups
        raise ValueError(f"unknown grouping: {type(g).__name__}")
