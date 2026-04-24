"""Tests for HierarchicalSummaryChunker wrapper (SC-003 + SC-004)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import (
    HierarchicalSummaryChunker,
    HierarchyChunker,
    SentenceAwareChunker,
)
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document


TEXT_WITH_HEADINGS = (
    "# Alpha\n\n" + ("Alpha body sentence here. " * 30) + "\n\n"
    "# Bravo\n\n" + ("Bravo body sentence here. " * 30) + "\n\n"
    "# Charlie\n\n" + ("Charlie body sentence here. " * 30)
)


def test_hierarchical_fixed_n_emits_fine_plus_coarse():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "fixed_n", "n": 2},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)

    fine = [c for c in chunks if c.metadata.get("granularity") == "fine"]
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    assert len(fine) > 0
    assert len(coarse) > 0

    # Every coarse group_id must have fine rows sharing the same group_id.
    fine_groups = {c.metadata["group_id"] for c in fine}
    coarse_groups = {c.metadata["group_id"] for c in coarse}
    assert coarse_groups == fine_groups, (
        "every group_id should appear on both fine and coarse rows"
    )

    # Summarizer stamp present everywhere.
    for c in chunks:
        assert c.metadata.get("summarizer") == "passthrough"


def test_hierarchical_fixed_n_group_sizes():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(min_chars=50, max_chars=200),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "fixed_n", "n": 3},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    fine = [c for c in chunks if c.metadata.get("granularity") == "fine"]
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    # ceil(len(fine) / 3) == len(coarse)
    import math
    assert len(coarse) == math.ceil(len(fine) / 3)


def test_hierarchical_section_aware_requires_hierarchy_base_at_config_load():
    """section_aware must error at config-load time, not runtime."""
    with pytest.raises(ValidationError, match="section_aware"):
        HierarchicalSummaryChunker(
            type="hierarchical_summary",
            base=SentenceAwareChunker(),  # NOT hierarchy
            summarizer={"mode": "passthrough"},
            grouping={"strategy": "section_aware"},
        )


def test_hierarchical_section_aware_with_hierarchy_base():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=HierarchyChunker(type="hierarchy"),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "section_aware"},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    fine = [c for c in chunks if c.metadata.get("granularity") == "fine"]
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    # section_aware: one coarse row per heading section (Alpha, Bravo, Charlie)
    assert len(coarse) == 3
    # Fine count depends on hierarchy chunker output; at least one per section.
    assert len(fine) >= 3

    # Each coarse group_id matches at least one fine group_id.
    coarse_groups = {c.metadata["group_id"] for c in coarse}
    fine_groups = {c.metadata["group_id"] for c in fine}
    assert coarse_groups == fine_groups


def test_hierarchical_word_budget():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(min_chars=50, max_chars=300),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "word_budget", "max_words": 100},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    assert len(coarse) >= 2  # small budget → multiple groups


def test_hierarchical_group_ids_stable_and_unique():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "fixed_n", "n": 2},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    gids = [c.metadata["group_id"] for c in coarse]
    assert len(gids) == len(set(gids)), "group_ids must be unique per coarse row"
    # Stable across runs with same input
    chunks2 = chunker.chunk(doc)
    coarse2 = [c for c in chunks2 if c.metadata.get("granularity") == "coarse"]
    assert gids == [c.metadata["group_id"] for c in coarse2]


def test_hierarchical_seq_nums_are_contiguous():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "fixed_n", "n": 3},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    seqs = [c.seq_num for c in chunks]
    assert seqs == list(range(len(chunks)))  # 0, 1, 2, ... N-1, no duplicates/gaps
