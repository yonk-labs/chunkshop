"""Unit tests for build_consolidator (mirrors _summarizer.build_summarizer)."""
import pytest
from chunkshop.config import (CallableConsolidator, PassthroughConsolidator,
                              ConsolidationChunker, LedeConsolidator)
from chunkshop.chunkers._consolidator import build_consolidator


_BASE = {"type": "sentence_aware", "doc_type": "prose"}


def test_lede_consolidator_parses():
    cfg = ConsolidationChunker.model_validate({
        "type": "consolidation",
        "base": _BASE,
        "consolidator": {"mode": "lede", "confidence_floor": 0.3, "max_facts": 8},
    })
    assert cfg.consolidator.mode == "lede"
    assert cfg.consolidator.confidence_floor == 0.3


def test_lede_spacy_consolidator_parses_with_summarizer_slot():
    cfg = ConsolidationChunker.model_validate({
        "type": "consolidation",
        "base": _BASE,
        "consolidator": {"mode": "lede_spacy",
            "summarizer": {"mode": "callable", "module": "chunkshop.summarizers.caveman"}},
    })
    assert cfg.consolidator.mode == "lede_spacy"
    assert cfg.consolidator.summarizer.module == "chunkshop.summarizers.caveman"


def test_confidence_floor_bounds_rejected():
    with pytest.raises(Exception):
        ConsolidationChunker.model_validate({
            "type": "consolidation",
            "base": _BASE,
            "consolidator": {"mode": "lede", "confidence_floor": 1.5},
        })


def test_build_lede_applies_confidence_floor(monkeypatch):
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [
        {"subject": None, "predicate": None, "object": None, "support_span": "high", "confidence": 0.9},
        {"subject": None, "predicate": None, "object": None, "support_span": "low", "confidence": 0.1},
    ])
    fn = build_consolidator(LedeConsolidator(mode="lede", confidence_floor=0.5))
    out = fn("some episode text", {})
    assert [f["support_span"] for f in out["facts"]] == ["high"]
    assert out["summary"] == ""


def test_build_lede_summarizer_slot_fills_summary(monkeypatch):
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [])
    cfg = LedeConsolidator.model_validate({"mode": "lede", "summarizer": {"mode": "passthrough"}})
    fn = build_consolidator(cfg)
    out = fn("EPISODE BODY", {})
    assert out["summary"] == "EPISODE BODY"


def test_consolidation_chunker_emits_fact_chunks(monkeypatch):
    from chunkshop.chunkers import load_chunker
    from chunkshop.config import SentenceAwareChunker
    from chunkshop.sources.base import Document
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [
        {"subject": "A", "predicate": "is", "object": "B", "support_span": "A is B", "confidence": 0.9},
    ])
    cfg = ConsolidationChunker(
        type="consolidation",
        base=SentenceAwareChunker(type="sentence_aware", doc_type="prose"),
        consolidator=LedeConsolidator(mode="lede"),
    )
    chunker = load_chunker(cfg)
    chunks = chunker.chunk(Document(id="doc1", content="A is B. C is D.", metadata={}))
    kinds = [c.metadata.get("kind") for c in chunks]
    assert "episode" in kinds and "fact" in kinds


def test_passthrough_returns_summary_and_no_facts():
    fn = build_consolidator(PassthroughConsolidator(mode="passthrough"))
    out = fn("episode text", {})
    assert out["summary"] == "episode text"
    assert out["facts"] == []


def test_callable_invokes_module_function(tmp_path, monkeypatch):
    import sys, types
    mod = types.ModuleType("fake_consolidator")
    mod.consolidate = lambda text, **kw: {
        "summary": "S:" + text[:3],
        "facts": [{"subject": "a", "predicate": "is", "object": "b",
                   "support_span": "a is b", "confidence": 0.9}]}
    sys.modules["fake_consolidator"] = mod
    fn = build_consolidator(CallableConsolidator(
        mode="callable", module="fake_consolidator"))
    out = fn("hello world", {})
    assert out["summary"] == "S:hel"
    assert out["facts"][0]["predicate"] == "is"


def test_callable_bad_module_raises_actionable():
    fn_cfg = CallableConsolidator(mode="callable", module="nope.not.here")
    with pytest.raises(RuntimeError, match="could not import"):
        build_consolidator(fn_cfg)


def test_callable_bad_function_raises_actionable():
    fn_cfg = CallableConsolidator(
        mode="callable", module="json", function="this_does_not_exist"
    )
    with pytest.raises(RuntimeError, match="has no attribute"):
        build_consolidator(fn_cfg)
