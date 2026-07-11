from __future__ import annotations

import re

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.config import FixedOverlapChunker as Cfg
from chunkshop.sources.base import Document


class FixedOverlapChunker:
    def __init__(self, cfg: Cfg, build_chunker=None):
        self.cfg = cfg
        if cfg.step_words <= 0 or cfg.window_words <= 0:
            raise ValueError("window_words and step_words must be positive")
        self._build_chunker = build_chunker
        ceiling = cfg.effective_max_chars()
        self._warner = DedupedWarner("fixed_overlap", ceiling) if ceiling is not None else None

    def chunk(self, doc: Document) -> list[Chunk]:
        # Word *spans* into the original text, not split-off word copies. Slicing
        # content[first_start:last_end] preserves all original whitespace inside
        # the window (newlines, indentation) — critical when fixed_overlap is the
        # symbol_aware if_oversize fallback for whitespace-significant code. See #79.
        spans = [m.span() for m in re.finditer(r"\S+", doc.content)]
        window = self.cfg.window_words
        step = self.cfg.step_words
        chunks: list[Chunk] = []
        seq = 0
        i = 0
        while i < len(spans):
            slice_spans = spans[i : i + window]
            text = doc.content[slice_spans[0][0] : slice_spans[-1][1]]
            chunks.append(Chunk(
                doc_id=doc.id,
                seq_num=seq,
                original_content=text,
                embedded_content=text,
                metadata={"strategy": "fixed_overlap", "start_word": i, "n_words": len(slice_spans)},
            ))
            seq += 1
            if i + window >= len(spans):
                break
            i += step
        return apply_if_oversize(
            chunks,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="fixed_overlap",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )
