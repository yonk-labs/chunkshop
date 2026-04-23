"""Deterministic key derivation for bakeoff combo tables (SC-004).

Keys must be lowercase, underscore-only, and stable across runs so the same
YAML produces the same combo-table names every time.
"""
from __future__ import annotations

from chunkshop.bakeoff.keys import chunker_key, combo_table, embedder_key
from chunkshop.config import (
    FastembedEmbedder,
    FixedOverlapChunker,
    HierarchyChunker,
    NeighborExpandChunker,
    SentenceAwareChunker,
)


def test_embedder_key_strips_org_and_punctuation():
    e = FastembedEmbedder(type="fastembed", model_name="Xenova/bge-base-en-v1.5-int8", dim=768)
    assert embedder_key(e) == "bge_base_en_v1_5_int8"


def test_chunker_key_simple():
    assert chunker_key(HierarchyChunker(type="hierarchy")) == "hierarchy"
    assert chunker_key(SentenceAwareChunker(type="sentence_aware")) == "sentence_aware"


def test_chunker_key_fixed_overlap_includes_window():
    c = FixedOverlapChunker(type="fixed_overlap", window_words=300, step_words=150)
    assert chunker_key(c) == "fixed_overlap_w300_s150"


def test_chunker_key_neighbor_expand_includes_base():
    c = NeighborExpandChunker(
        type="neighbor_expand",
        base=SentenceAwareChunker(type="sentence_aware"),
        window=1,
    )
    assert chunker_key(c) == "neighbor_expand_w1_over_sentence_aware"


def test_combo_table_joins_keys():
    e = FastembedEmbedder(type="fastembed", model_name="Xenova/bge-small-en-v1.5-int8", dim=384)
    c = HierarchyChunker(type="hierarchy")
    assert combo_table(c, e) == "hierarchy__bge_small_en_v1_5_int8"
