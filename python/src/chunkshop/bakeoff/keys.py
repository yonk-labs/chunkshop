"""Deterministic chunker + embedder key derivation for combo table names (SC-004).

The bakeoff runner writes one Postgres table per combo: `{chunker_key}__{embedder_key}`
under the target schema. Table names must be lowercase-underscore idents so
pgvector operators + the sink's regex-allowlisted identifier validator both
accept them. Derivations here are pure functions — same config in, same key out.
"""
from __future__ import annotations

import re

from chunkshop.config import (
    ChunkerConfig,
    ConsolidationChunker,
    FastembedEmbedder,
    FixedOverlapChunker,
    HierarchicalSummaryChunker,
    HierarchyChunker,
    NeighborExpandChunker,
    SemanticChunker,
    SentenceAwareChunker,
    SummaryEmbedChunker,
)

_ID_SAFE = re.compile(r"[^a-z0-9]+")


def embedder_key(cfg: FastembedEmbedder) -> str:
    """Model short name stripped of org prefix + punctuation.

    `Xenova/bge-base-en-v1.5-int8` -> `bge_base_en_v1_5_int8`.
    """
    short = cfg.model_name.split("/")[-1].lower()
    return _ID_SAFE.sub("_", short).strip("_")


def chunker_key(cfg: ChunkerConfig) -> str:
    """One deterministic key per chunker shape. Include params that change behavior.

    `fixed_overlap` includes window/step so (w=300,s=150) and (w=500,s=100)
    don't collide. `neighbor_expand` recurses into its `base` so the outer
    window + the underlying strategy both land in the ident.
    """
    if isinstance(cfg, HierarchyChunker):
        return "hierarchy"
    if isinstance(cfg, SentenceAwareChunker):
        return "sentence_aware"
    if isinstance(cfg, FixedOverlapChunker):
        return f"fixed_overlap_w{cfg.window_words}_s{cfg.step_words}"
    if isinstance(cfg, NeighborExpandChunker):
        return f"neighbor_expand_w{cfg.window}_over_{chunker_key(cfg.base)}"
    if isinstance(cfg, SemanticChunker):
        return "semantic"
    if isinstance(cfg, SummaryEmbedChunker):
        return f"summary_embed_over_{chunker_key(cfg.base)}"
    if isinstance(cfg, HierarchicalSummaryChunker):
        return f"hierarchical_summary_over_{chunker_key(cfg.base)}"
    if isinstance(cfg, ConsolidationChunker):
        return f"consolidation_over_{chunker_key(cfg.base)}"
    raise ValueError(f"unknown chunker type for key derivation: {type(cfg).__name__}")


def combo_table(chunker: ChunkerConfig, embedder: FastembedEmbedder) -> str:
    """Build the combo's table name: `{chunker_key}__{embedder_key}`."""
    return f"{chunker_key(chunker)}__{embedder_key(embedder)}"
