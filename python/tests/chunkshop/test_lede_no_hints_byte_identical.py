"""Backward-compat (SC-015): no-hints output is byte-identical to the captured golden.

The golden is captured ONCE (see plan Task 3 Step 1). Do NOT regenerate it
without an explicit instruction — a diff here means the no-hints path changed.
"""
import importlib.util

import pytest

from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.config import SummaryEmbedChunker as C, SentenceAwareChunker as S
from chunkshop.sources.base import Document


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


TEXT = (
    "The annual budget was approved last spring. "
    "John Smith lives in Cook County and runs a small hardware business. "
    "The weather has been mild this year. "
    "Several committees met to discuss zoning. "
    "Trade volumes rose in the third quarter."
)

# Captured 2026-05-21 via Task 3 Step 1 — do not hand-edit
GOLDEN = 'The annual budget was approved last spring. John Smith lives in Cook County and runs a small hardware business. Several committees met to discuss zoning. Trade volumes rose in the third quarter.'


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_no_hints_byte_identical():
    cfg = C(
        type="summary_embed",
        base=S(type="sentence_aware"),
        summarizer={
            "mode": "callable",
            "module": "chunkshop.summarizers.lede",
            "function": "summarize",
            "kwargs": {"max_length": 200},
        },
    )
    ch = SummaryEmbedChunker(cfg, SentenceAwareChunker(cfg.base))
    out = " ".join(c.embedded_content for c in ch.chunk(Document(id="d1", content=TEXT, metadata={})))
    assert out == GOLDEN
