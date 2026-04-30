"""Runtime tests for if_oversize across all wrappers + fixed_overlap.

Brief SC-004 / D1: trigger when len(embedded) > ceiling OR len(original) > ceiling.
Brief D2: re-chunked sub-chunks have embedded_content == original_content.
"""
from __future__ import annotations

import logging

import pytest

from chunkshop.chunkers import load_chunker
from chunkshop.config import (
    FixedOverlapChunker as FixedCfg,
    NeighborExpandChunker as NeighborCfg,
    HierarchyChunker as HierCfg,
    SummaryEmbedChunker as SummaryEmbedCfg,
    HierarchicalSummaryChunker as HierSumCfg,
    PassthroughSummarizer,
    FixedNGrouping,
)
from chunkshop.sources.base import Document


def _doc(text: str) -> Document:
    return Document(id="doc1", content=text, metadata={})


# --------------- fixed_overlap tests ---------------

def test_fixed_overlap_no_max_chars_unchanged():
    """Without max_chars, behavior unchanged from 0.3.1."""
    cfg = FixedCfg(type="fixed_overlap", window_words=10, step_words=10)
    ch = load_chunker(cfg)
    out = ch.chunk(_doc("alpha bravo charlie delta echo foxtrot golf hotel " * 5))
    assert all(c.embedded_content == c.original_content for c in out)


def test_fixed_overlap_max_chars_no_fallback_warns_once(caplog):
    """With max_chars set but no if_oversize, oversize chunks emit ONE warning."""
    caplog.set_level(logging.WARNING, logger="chunkshop")
    cfg = FixedCfg(
        type="fixed_overlap",
        window_words=100,
        step_words=100,
        max_chars=20,  # tiny ceiling — almost everything overflows
    )
    ch = load_chunker(cfg)
    text = " ".join(["word"] * 500)  # ~2500 chars total
    out = ch.chunk(_doc(text))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "fixed_overlap" in warnings[0].getMessage()
    # Output chunks not modified — warning, not fallback.
    assert any(len(c.embedded_content) > 20 for c in out)


def test_fixed_overlap_max_chars_with_fallback_no_warning(caplog):
    """With if_oversize set, fallback fires; no warning."""
    caplog.set_level(logging.WARNING, logger="chunkshop")
    cfg = FixedCfg(
        type="fixed_overlap",
        window_words=100,
        step_words=100,
        max_chars=50,
        if_oversize=FixedCfg(type="fixed_overlap", window_words=5, step_words=5, max_chars=30),
    )
    ch = load_chunker(cfg)
    text = " ".join(["word"] * 500)
    out = ch.chunk(_doc(text))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0
    # All output chunks within the FALLBACK ceiling (30), since the fallback re-chunks oversize ones.
    # (Originals already under 50 stay under 50 — but fallback's max is tighter.)
    assert all(len(c.embedded_content) <= 50 for c in out)
    # D2: sub-chunks have embedded_content == original_content.
    for c in out:
        if len(c.original_content) <= 30:
            # Could be original or fallback; either way embedded == original.
            assert c.embedded_content == c.original_content


# --------------- neighbor_expand tests ---------------

def test_neighbor_expand_oversize_routed_through_fallback():
    """Brief SC-004: neighbor_expand can produce 5-10× base ceiling; fallback fires."""
    base_cfg = HierCfg(type="hierarchy", max_chars=1500)
    cfg = NeighborCfg(
        type="neighbor_expand",
        window=2,
        base=base_cfg,
        if_oversize=FixedCfg(
            type="fixed_overlap",
            window_words=200,
            step_words=160,
            max_chars=1500,
        ),
    )
    # Build a doc with 5 sections of ~1500 chars each — joined window=2 → ~7500
    sections = ["## Section " + str(i) + "\n" + ("lorem ipsum " * 130) for i in range(1, 6)]
    text = "\n\n".join(sections)
    ch = load_chunker(cfg)
    out = ch.chunk(_doc(text))
    assert all(len(c.embedded_content) <= 1500 for c in out)


# --------------- summary_embed tests ---------------

def test_summary_embed_oversize_routed_through_fallback():
    """summary_embed with verbose passthrough: original is huge → fallback fires."""
    base_cfg = HierCfg(type="hierarchy", max_chars=1500)
    cfg = SummaryEmbedCfg(
        type="summary_embed",
        base=base_cfg,
        summarizer=PassthroughSummarizer(mode="passthrough"),
        max_chars=1000,  # tighter than base — forces fallback even on small chunks
        if_oversize=FixedCfg(
            type="fixed_overlap",
            window_words=100,
            step_words=80,
            max_chars=900,
        ),
    )
    text = "## A\n" + ("lorem ipsum " * 130) + "\n\n## B\n" + ("lorem ipsum " * 130)
    out = load_chunker(cfg).chunk(_doc(text))
    assert all(len(c.embedded_content) <= 1000 for c in out)
    assert all(len(c.original_content) <= 1000 for c in out)


# --------------- hierarchical_summary tests ---------------

def test_hierarchical_summary_coarse_rows_exempt(caplog):
    """Brief SC-005: coarse rows can be huge but stay 1-per-group, no warning."""
    caplog.set_level(logging.WARNING, logger="chunkshop")
    base_cfg = HierCfg(type="hierarchy", max_chars=1500)
    cfg = HierSumCfg(
        type="hierarchical_summary",
        base=base_cfg,
        summarizer=PassthroughSummarizer(mode="passthrough"),
        grouping=FixedNGrouping(strategy="fixed_n", n=5),
        # No if_oversize — coarse rows would be flagged if we DID check them.
    )
    sections = ["## Section " + str(i) + "\n" + ("lorem ipsum " * 100) for i in range(1, 6)]
    text = "\n\n".join(sections)
    out = load_chunker(cfg).chunk(_doc(text))

    coarse_rows = [c for c in out if c.metadata.get("granularity") == "coarse"]
    fine_rows = [c for c in out if c.metadata.get("granularity") == "fine"]

    # Coarse rows can have huge original_content (concat of group)
    assert any(len(c.original_content) > 1500 for c in coarse_rows)
    # Fine rows stay bounded by base
    assert all(len(c.original_content) <= 1500 for c in fine_rows)
    # SC-005: NO warning emitted (coarse rows skipped from check)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0
