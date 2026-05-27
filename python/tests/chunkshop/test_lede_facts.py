from chunkshop.consolidators import lede_facts

def test_empty_text_yields_no_facts():
    assert lede_facts.extract_facts("") == []

def test_facts_have_contract_shape_and_decaying_confidence(monkeypatch):
    monkeypatch.setattr(lede_facts, "_lede_summary",
        lambda text, **kw: "Alpha is first. Beta is second. Gamma is third.")
    facts = lede_facts.extract_facts("ignored", max_facts=2)
    assert len(facts) == 2
    for f in facts:
        assert set(f) == {"subject", "predicate", "object", "support_span", "confidence"}
    assert facts[0]["confidence"] > facts[1]["confidence"]
    assert facts[0]["support_span"] == "Alpha is first."
