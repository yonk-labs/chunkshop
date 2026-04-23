"""Chunker registry."""
from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers.fixed_overlap import FixedOverlapChunker
from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.chunkers.neighbor_expand import NeighborExpandChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.config import (
    ChunkerConfig,
    FixedOverlapChunker as FixedCfg,
    HierarchyChunker as HierCfg,
    NeighborExpandChunker as NeighborCfg,
    SentenceAwareChunker as SentCfg,
    SummaryEmbedChunker as SummaryEmbedCfg,
    HierarchicalSummaryChunker as HierSummaryCfg,
)


def load_chunker(cfg: ChunkerConfig) -> Chunker:
    if isinstance(cfg, SentCfg):
        return SentenceAwareChunker(cfg)
    if isinstance(cfg, FixedCfg):
        return FixedOverlapChunker(cfg)
    if isinstance(cfg, HierCfg):
        return HierarchyChunker(cfg)
    if isinstance(cfg, NeighborCfg):
        base = load_chunker(cfg.base)
        return NeighborExpandChunker(cfg, base)
    if isinstance(cfg, SummaryEmbedCfg):
        base = load_chunker(cfg.base)
        return SummaryEmbedChunker(cfg, base)
    if isinstance(cfg, HierSummaryCfg):
        from chunkshop.chunkers.hierarchical_summary import HierarchicalSummaryChunker
        base = load_chunker(cfg.base)
        return HierarchicalSummaryChunker(cfg, base)
    raise ValueError(f"unknown chunker type: {type(cfg).__name__}")


__all__ = ["Chunk", "Chunker", "load_chunker"]
