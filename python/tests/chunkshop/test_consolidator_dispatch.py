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
