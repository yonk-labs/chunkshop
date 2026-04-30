"""Chunker registry."""
from __future__ import annotations

from typing import Any, Optional

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers.fixed_overlap import FixedOverlapChunker
from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.chunkers.neighbor_expand import NeighborExpandChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.chunkers.semantic import SemanticChunker
from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.hierarchical_summary import HierarchicalSummaryChunker
from chunkshop.config import (
    ChunkerConfig,
    EmbedderConfig,
    FixedOverlapChunker as FixedCfg,
    HierarchyChunker as HierCfg,
    NeighborExpandChunker as NeighborCfg,
    SemanticChunker as SemanticCfg,
    SentenceAwareChunker as SentCfg,
    SummaryEmbedChunker as SummaryEmbedCfg,
    HierarchicalSummaryChunker as HierSummaryCfg,
)


def load_chunker(
    cfg: ChunkerConfig,
    *,
    main_embedder: Optional[EmbedderConfig] = None,
    shared_boundary_model: Any = None,
) -> Chunker:
    """Build a chunker from config.

    Args:
      cfg: chunker config.
      main_embedder: the cell's main embedder config. Required when the chunker
        (or any nested chunker) is `semantic` with `boundary_model="same"` — the
        semantic chunker needs the main model's name to know what to reuse.
      shared_boundary_model: an already-instantiated `TextEmbedding`. When
        present AND the semantic chunker's `boundary_model == "same"`, the
        chunker reuses this instance rather than loading its own (SC-002).

    For nested chunkers (neighbor_expand, summary_embed, hierarchical_summary),
    `main_embedder` and `shared_boundary_model` propagate to the inner call.

    NEW in 0.3.2: chunkers may receive a `build_chunker` callable so they can
    construct their `if_oversize` fallback at chunk-time. The callable is
    curried with `main_embedder` and `shared_boundary_model` so nested
    chunkers see the same embedder context. Currently only FixedOverlapChunker
    accepts this kwarg; the other chunkers gain it in subsequent tasks.
    """
    def _build(inner_cfg: ChunkerConfig) -> Chunker:
        return load_chunker(
            inner_cfg,
            main_embedder=main_embedder,
            shared_boundary_model=shared_boundary_model,
        )

    if isinstance(cfg, SentCfg):
        return SentenceAwareChunker(cfg)
    if isinstance(cfg, FixedCfg):
        return FixedOverlapChunker(cfg, build_chunker=_build)
    if isinstance(cfg, HierCfg):
        return HierarchyChunker(cfg)
    if isinstance(cfg, NeighborCfg):
        base = load_chunker(
            cfg.base,
            main_embedder=main_embedder,
            shared_boundary_model=shared_boundary_model,
        )
        return NeighborExpandChunker(cfg, base)
    if isinstance(cfg, SummaryEmbedCfg):
        base = load_chunker(
            cfg.base,
            main_embedder=main_embedder,
            shared_boundary_model=shared_boundary_model,
        )
        return SummaryEmbedChunker(cfg, base)
    if isinstance(cfg, HierSummaryCfg):
        base = load_chunker(
            cfg.base,
            main_embedder=main_embedder,
            shared_boundary_model=shared_boundary_model,
        )
        return HierarchicalSummaryChunker(cfg, base)
    if isinstance(cfg, SemanticCfg):
        main_model_name = getattr(main_embedder, "model_name", None) if main_embedder else None
        # Only pass shared_boundary_model when the chunker actually wants "same".
        shared = shared_boundary_model if cfg.boundary_model == "same" else None
        return SemanticChunker(
            cfg,
            main_embedder_model_name=main_model_name,
            shared_model=shared,
        )
    raise ValueError(f"unknown chunker type: {type(cfg).__name__}")


__all__ = ["Chunk", "Chunker", "load_chunker"]
