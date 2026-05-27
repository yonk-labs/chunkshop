import pytest
from chunkshop.consolidators import lede_spacy_facts

def test_empty_text_yields_no_facts():
    assert lede_spacy_facts.extract_facts("") == []

def test_svo_triple_from_simple_sentence(monkeypatch):
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm model not installed")
    facts = lede_spacy_facts.extract_facts("Alice wrote the report.")
    assert any(
        f["subject"] == "Alice" and f["predicate"] == "write" and "report" in (f["object"] or "")
        for f in facts
    )
    for f in facts:
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["support_span"]
