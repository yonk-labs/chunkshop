"""SummaryEmbedChunker — wrap any base chunker, replace embedded_content with a summary.

Per brief SC-001/SC-002: ``original_content`` stays as the raw base-chunker output;
``embedded_content`` is the summary. ``metadata.summarizer`` is stamped with the
chosen mode (``external`` / ``callable`` / ``passthrough``) for traceability.
"""
from __future__ import annotations
from dataclasses import replace

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import SummaryEmbedChunker as Cfg
from chunkshop.sources.base import Document


class SummaryEmbedChunker:
    """Wrap any base chunker; replace each chunk's ``embedded_content`` with a summary."""

    def __init__(self, cfg: Cfg, base: Chunker):
        self.cfg = cfg
        self.base = base
        self._summarize = build_summarizer(cfg.summarizer)
        self._mode = cfg.summarizer.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        doc_meta = dict(doc.metadata or {})
        out: list[Chunk] = []
        for bc in base_chunks:
            summary = self._summarize(bc.original_content, doc_meta)
            meta = {**bc.metadata, "summarizer": self._mode}
            out.append(replace(bc, embedded_content=summary, metadata=meta))
        return out
