"""Deterministic chunker + embedder key derivation for combo table names (SC-004).

The bakeoff runner writes one Postgres table per combo: `{chunker_key}__{embedder_key}`
under the target schema. Table names must be lowercase-underscore idents so
pgvector operators + the sink's regex-allowlisted identifier validator both
accept them. Derivations here are pure functions — same config in, same key out.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

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


def target_display_key(tgt, duplicate_backend_types: set[str] | None = None) -> str:
    """Stable human/report key for a bakeoff target row.

    `backend` remains the real sink type ("postgres", "sqlite", ...). This key
    distinguishes multiple targets of the same backend, e.g. a Postgres metric
    bakeoff with cosine / inner product / L2 rows.
    """
    explicit = getattr(tgt, "label", None)
    if explicit:
        return _ID_SAFE.sub("_", explicit.lower()).strip("_")

    backend = getattr(tgt, "type")
    duplicate_backend_types = duplicate_backend_types or set()
    if backend == "postgres":
        metric = getattr(tgt, "vector_metric", "cosine")
        if backend in duplicate_backend_types or metric != "cosine":
            return f"postgres_{metric}"
    return backend


def target_display_keys(targets: Iterable[object]) -> list[str]:
    """Return unique display keys, preserving target order."""
    targets = list(targets)
    counts = Counter(getattr(t, "type") for t in targets)
    duplicate_backend_types = {backend for backend, n in counts.items() if n > 1}

    seen: Counter[str] = Counter()
    keys: list[str] = []
    for tgt in targets:
        base = target_display_key(tgt, duplicate_backend_types)
        seen[base] += 1
        keys.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return keys
