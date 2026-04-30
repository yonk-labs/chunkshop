"""Config-layer tests for the 0.3.2 if_oversize field.

Brief SCs covered: SC-001, SC-002, SC-003. Brief NEVER: validator rejects
if_oversize-without-ceiling combos at config-load time.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from chunkshop.config import (
    ChunkerConfig,
    FixedOverlapChunker,
    HierarchyChunker,
    NeighborExpandChunker,
    SemanticChunker,
    SentenceAwareChunker,
    SummaryEmbedChunker,
    HierarchicalSummaryChunker,
)
from pydantic import TypeAdapter

ADAPTER = TypeAdapter(ChunkerConfig)


def _parse(d: dict):
    return ADAPTER.validate_python(d)


# -------- SC-001: every chunker accepts if_oversize: None and if_oversize: <cfg> --------

@pytest.mark.parametrize("base_type,base_extra", [
    ("sentence_aware", {}),
    ("fixed_overlap", {"window_words": 100, "step_words": 80}),
    ("hierarchy", {}),
    ("semantic", {}),
])
def test_simple_chunker_accepts_if_oversize_none(base_type, base_extra):
    cfg = _parse({"type": base_type, **base_extra})
    assert cfg.if_oversize is None


def test_fixed_overlap_with_if_oversize_and_max_chars():
    cfg = _parse({
        "type": "fixed_overlap",
        "window_words": 1000,
        "step_words": 800,
        "max_chars": 2000,
        "if_oversize": {"type": "fixed_overlap", "window_words": 200, "step_words": 160},
    })
    assert isinstance(cfg, FixedOverlapChunker)
    assert cfg.max_chars == 2000
    assert cfg.if_oversize is not None
    assert cfg.if_oversize.window_words == 200


def test_neighbor_expand_inherits_base_max_chars():
    cfg = _parse({
        "type": "neighbor_expand",
        "window": 1,
        "base": {"type": "hierarchy"},
        "if_oversize": {"type": "fixed_overlap", "window_words": 200, "step_words": 160, "max_chars": 1500},
    })
    # SC-003: wrapper without explicit max_chars resolves from base
    assert cfg.effective_max_chars() == 2000  # hierarchy default


def test_neighbor_expand_explicit_max_chars_overrides_base():
    cfg = _parse({
        "type": "neighbor_expand",
        "window": 1,
        "max_chars": 6000,
        "base": {"type": "hierarchy"},
        "if_oversize": {"type": "fixed_overlap", "window_words": 200, "step_words": 160, "max_chars": 1500},
    })
    assert cfg.effective_max_chars() == 6000


def test_summary_embed_explicit_max_chars():
    cfg = _parse({
        "type": "summary_embed",
        "max_chars": 1500,
        "base": {"type": "hierarchy"},
        "summarizer": {"mode": "passthrough"},
    })
    assert cfg.effective_max_chars() == 1500


def test_hierarchical_summary_inherits_base_max_chars():
    cfg = _parse({
        "type": "hierarchical_summary",
        "base": {"type": "hierarchy"},
        "summarizer": {"mode": "passthrough"},
    })
    assert cfg.effective_max_chars() == 2000


# -------- SC-002: fixed_overlap accepts max_chars optionally --------

def test_fixed_overlap_max_chars_optional():
    cfg = _parse({"type": "fixed_overlap", "window_words": 100, "step_words": 80})
    assert cfg.max_chars is None


# -------- D8 (Brief NEVER): if_oversize without effective ceiling rejected --------

def test_fixed_overlap_if_oversize_without_max_chars_rejected():
    with pytest.raises(ValidationError, match="effective ceiling"):
        _parse({
            "type": "fixed_overlap",
            "window_words": 100,
            "step_words": 80,
            "if_oversize": {"type": "fixed_overlap", "window_words": 50, "step_words": 40, "max_chars": 1000},
        })


def test_neighbor_expand_if_oversize_with_no_ceiling_anywhere_rejected():
    # Wrapper has no max_chars; base is fixed_overlap with no max_chars either → no ceiling.
    with pytest.raises(ValidationError, match="effective ceiling"):
        _parse({
            "type": "neighbor_expand",
            "window": 1,
            "base": {"type": "fixed_overlap", "window_words": 100, "step_words": 80},
            "if_oversize": {"type": "fixed_overlap", "window_words": 50, "step_words": 40, "max_chars": 1000},
        })


# -------- Recursive nesting (forward-ref re-binding) --------

def test_if_oversize_can_itself_have_if_oversize():
    cfg = _parse({
        "type": "fixed_overlap",
        "window_words": 1000,
        "step_words": 800,
        "max_chars": 4000,
        "if_oversize": {
            "type": "fixed_overlap",
            "window_words": 500,
            "step_words": 400,
            "max_chars": 2000,
            "if_oversize": {
                "type": "fixed_overlap",
                "window_words": 200,
                "step_words": 160,
                "max_chars": 1000,
            },
        },
    })
    assert cfg.if_oversize.if_oversize is not None
