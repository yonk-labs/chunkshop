import pytest
from chunkshop.consolidators import lede_spacy_facts


def _require_model():
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm model not installed")


def test_empty_text_yields_no_facts():
    assert lede_spacy_facts.extract_facts("") == []

def test_svo_triple_from_simple_sentence(monkeypatch):
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    _require_model()
    facts = lede_spacy_facts.extract_facts("Alice wrote the report.")
    assert any(
        f["subject"] == "Alice" and f["predicate"] == "write" and "report" in (f["object"] or "")
        for f in facts
    )
    for f in facts:
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["support_span"]


def test_subject_only_yields_partial_confidence(monkeypatch):
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    _require_model()
    facts = lede_spacy_facts.extract_facts("It is raining.")
    assert len(facts) == 1
    fact = facts[0]
    assert fact["object"] is None
    assert fact["confidence"] == 0.6


def test_prepositional_object_yields_full_confidence(monkeypatch):
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    _require_model()
    facts = lede_spacy_facts.extract_facts("The cat sat on the mat.")
    match = next(f for f in facts if f["object"] and "mat" in f["object"])
    assert match["confidence"] == 1.0


def test_max_facts_truncates(monkeypatch):
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    _require_model()
    text = "Alice wrote the report. Bob read the book. Carol fixed the bug."
    facts = lede_spacy_facts.extract_facts(text, max_facts=1)
    assert len(facts) == 1
