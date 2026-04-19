from __future__ import annotations

from chunkshop.chunkers.base import Chunk
from chunkshop.config import FixedOverlapChunker as Cfg
from chunkshop.sources.base import Document


class FixedOverlapChunker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        if cfg.step_words <= 0 or cfg.window_words <= 0:
            raise ValueError("window_words and step_words must be positive")

    def chunk(self, doc: Document) -> list[Chunk]:
        words = doc.content.split()
        window = self.cfg.window_words
        step = self.cfg.step_words
        chunks: list[Chunk] = []
        seq = 0
        i = 0
        while i < len(words):
            slice_words = words[i : i + window]
            text = " ".join(slice_words)
            chunks.append(Chunk(
                doc_id=doc.id,
                seq_num=seq,
                original_content=text,
                embedded_content=text,
                metadata={"strategy": "fixed_overlap", "start_word": i, "n_words": len(slice_words)},
            ))
            seq += 1
            if i + window >= len(words):
                break
            i += step
        return chunks
