from __future__ import annotations

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.config import NeighborExpandChunker as Cfg
from chunkshop.sources.base import Document


class NeighborExpandChunker:
    def __init__(self, cfg: Cfg, base: Chunker, build_chunker=None):
        self.cfg = cfg
        self.base = base
        self._build_chunker = build_chunker
        ceiling = cfg.effective_max_chars()
        self._warner = DedupedWarner("neighbor_expand", ceiling) if ceiling is not None else None

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        out: list[Chunk] = []
        w = self.cfg.window
        for i, bc in enumerate(base_chunks):
            lo = max(0, i - w)
            hi = min(len(base_chunks) - 1, i + w)
            joined = "\n\n".join(base_chunks[j].embedded_content for j in range(lo, hi + 1))
            out.append(Chunk(
                doc_id=bc.doc_id,
                seq_num=bc.seq_num,
                original_content=bc.original_content,
                embedded_content=joined,
                metadata={**bc.metadata, "neighbor_expand_window": w},
            ))
        return apply_if_oversize(
            out,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="neighbor_expand",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )
