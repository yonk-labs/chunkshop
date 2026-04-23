"""Tests for SummaryEmbedChunker wrapper (SC-001 + SC-002)."""
import pytest

from chunkshop.config import SummaryEmbedChunker, SentenceAwareChunker
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document


TEXT = (
    "# Alpha\n\n"
    + ("Alpha bravo charlie delta echo foxtrot. " * 20)
    + "\n\n# Golf\n\n"
    + ("Golf hotel india juliet kilo lima. " * 20)
)


def test_summary_embed_passthrough():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
    )
    chunker = load_chunker(cfg)
    chunks = chunker.chunk(Document(id="d1", content=TEXT, title="t", metadata={}))
    assert len(chunks) >= 1
    for c in chunks:
        # Passthrough: embedded == original (summary = raw chunk text).
        assert c.original_content == c.embedded_content
        assert c.metadata.get("summarizer") == "passthrough"


def test_summary_embed_external():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base=SentenceAwareChunker(),
        summarizer={"mode": "external", "field": "summary"},
    )
    chunker = load_chunker(cfg)
    doc = Document(
        id="d1",
        content=TEXT,
        title="t",
        metadata={"summary": "pre-computed one-line summary"},
    )
    chunks = chunker.chunk(doc)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.embedded_content == "pre-computed one-line summary"
        # original_content is the raw chunk from the base chunker.
        assert c.original_content != c.embedded_content
        assert c.metadata.get("summarizer") == "external"


def test_summary_embed_external_missing_field_raises():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base=SentenceAwareChunker(),
        summarizer={"mode": "external", "field": "summary"},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT, title="t", metadata={})
    with pytest.raises(RuntimeError, match="no field 'summary'"):
        chunker.chunk(doc)


def test_summary_embed_callable_with_fake_module(tmp_path, monkeypatch):
    """Callable mode imports a user-supplied module and forwards kwargs."""
    import sys
    (tmp_path / "fake_summer.py").write_text(
        "def summarize(text, **kwargs):\n"
        "    prefix = kwargs.get('prefix', 'SUM')\n"
        "    return f'{prefix}[{len(text)}]'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fake_summer", None)
    try:
        cfg = SummaryEmbedChunker(
            type="summary_embed",
            base=SentenceAwareChunker(),
            summarizer={
                "mode": "callable",
                "module": "fake_summer",
                "function": "summarize",
                "kwargs": {"prefix": "ZZZ"},
            },
        )
        chunker = load_chunker(cfg)
        chunks = chunker.chunk(Document(id="d1", content=TEXT, title="t", metadata={}))
        assert len(chunks) >= 1
        for c in chunks:
            assert c.embedded_content.startswith("ZZZ[")
            assert c.metadata.get("summarizer") == "callable"
            # Original preserved
            assert c.original_content != c.embedded_content
    finally:
        sys.modules.pop("fake_summer", None)


def test_summary_embed_preserves_base_metadata():
    """Base chunker's metadata (strategy, heading, etc.) must survive the wrap."""
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base={"type": "hierarchy"},
        summarizer={"mode": "passthrough"},
    )
    chunker = load_chunker(cfg)
    chunks = chunker.chunk(Document(id="d1", content=TEXT, title="t", metadata={}))
    assert len(chunks) >= 1
    for c in chunks:
        # hierarchy chunker emits strategy='hierarchy' + heading
        assert c.metadata.get("strategy") == "hierarchy"
        assert "heading" in c.metadata
        assert c.metadata.get("summarizer") == "passthrough"
