"""L1 e2e (SC-005): hint kwargs flow text -> summary_embed -> lede, biasing output."""
import importlib.util

import pytest

from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.config import SummaryEmbedChunker as SummaryEmbedCfg
from chunkshop.config import SentenceAwareChunker as SentenceAwareCfg
from chunkshop.sources.base import Document


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# A doc with one clearly hint-bearing sentence and several distractor sentences.
TEXT = (
    "The annual budget was approved last spring. "
    "John Smith lives in Cook County and runs a small hardware business. "
    "The weather has been mild this year. "
    "Several committees met to discuss zoning. "
    "Trade volumes rose in the third quarter."
)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_hard_hint_biases_summary():
    cfg = SummaryEmbedCfg(
        type="summary_embed",
        base=SentenceAwareCfg(type="sentence_aware"),
        summarizer={
            "mode": "callable",
            "module": "chunkshop.summarizers.lede",
            "function": "summarize",
            "kwargs": {
                "max_length": 200,
                "hints": ["John Smith"],
                "hint_focus": 1.0,
                "hint_mode": "hard",
            },
        },
    )
    base = SentenceAwareChunker(cfg.base)
    chunker = SummaryEmbedChunker(cfg, base)
    doc = Document(id="d1", content=TEXT, metadata={})
    chunks = chunker.chunk(doc)
    joined = " ".join(c.embedded_content for c in chunks)
    assert "John Smith" in joined, f"hard hint did not bias output: {joined!r}"
