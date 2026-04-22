"""Unit tests for the composite extractor.

SC-004: chain spacy + lang (or rake + lang), verify metadata merge + tag
concatenation. Child failures must raise with the child-type name in the
message — no silent swallowing.
"""
from __future__ import annotations

import pytest

from chunkshop.config import (
    CompositeExtractor,
    LangDetectExtractor,
    NoneExtractor,
    RakeKeywordsExtractor,
)
from chunkshop.extractors import load_extractor
from chunkshop.extractors.composite import CompositeExtractor as CompositeImpl
from chunkshop.extractors.result import ExtractResult


SAMPLE_TEXT = (
    "The Supreme Court ruled on civil rights in Bostock v. Clayton County. "
    "Justice Neil Gorsuch wrote the majority opinion."
)


def test_composite_empty_children_returns_empty_result():
    ex = load_extractor(CompositeExtractor(type="composite", extractors=[]))
    r = ex.extract("any text")
    assert r.tags == []
    assert r.metadata == {}


@pytest.mark.skipif(
    pytest.importorskip("langdetect", reason="requires [lang] extra") is None,
    reason="requires [lang] extra",
)
def test_composite_merges_metadata_and_concats_tags_rake_lang():
    ex = load_extractor(
        CompositeExtractor(
            type="composite",
            extractors=[
                RakeKeywordsExtractor(type="rake_keywords", top_k=3),
                LangDetectExtractor(type="lang_detect"),
            ],
        )
    )
    r = ex.extract(SAMPLE_TEXT)
    # RAKE contributes tags (1-3); lang_detect contributes language metadata.
    assert 1 <= len(r.tags) <= 3
    assert all(isinstance(t, str) for t in r.tags)
    assert r.metadata.get("language") == "en"
    assert r.metadata.get("language_confidence", 0.0) > 0.5


def test_composite_metadata_last_wins_on_key_collision():
    """Two children emit the same metadata key — later child overwrites earlier.

    Documented behavior: users should namespace their keys. We verify the
    guarantee by constructing the composite impl directly with stubbed children
    (avoids dependency on any extra).
    """

    class _EchoChild:
        def __init__(self, payload):
            self._payload = payload

        def extract(self, text):
            return ExtractResult(tags=list(self._payload.get("_tags", [])),
                                  metadata={k: v for k, v in self._payload.items()
                                             if k != "_tags"})

    c = CompositeImpl.__new__(CompositeImpl)
    c._children = [
        _EchoChild({"shared": "first", "_tags": ["a"]}),
        _EchoChild({"shared": "second", "_tags": ["b"]}),
    ]
    r = c.extract("anything")
    assert r.tags == ["a", "b"]
    assert r.metadata["shared"] == "second"  # later-wins documented behavior


def test_composite_child_failure_raises_with_child_type():
    """Child failure must surface immediately with the child class name."""

    class _BrokenChild:
        def extract(self, text):
            raise RuntimeError("child kaboom")

    c = CompositeImpl.__new__(CompositeImpl)
    c._children = [_BrokenChild()]

    with pytest.raises(RuntimeError) as excinfo:
        c.extract("anything")

    msg = str(excinfo.value)
    assert "composite" in msg.lower()
    assert "_BrokenChild" in msg  # child type preserved in the message
    assert "child kaboom" in msg  # original exception message chained through


def test_composite_with_only_none_children():
    ex = load_extractor(
        CompositeExtractor(
            type="composite",
            extractors=[NoneExtractor(), NoneExtractor()],
        )
    )
    r = ex.extract("hello world")
    assert r.tags == []
    assert r.metadata == {}
