"""L3 (SC-014): top_terms extractor — kinds filtering, hint bias, lazy-import error."""
import importlib.util

import pytest

from chunkshop.config import LedeTopTermsExtractor as Cfg
from chunkshop.extractors.lede_top_terms import LedeTopTermsExtractor


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


TEXT = (
    "John Smith attended the Cook County council meeting about property taxes. "
    "The council debated zoning, taxes, and the county budget at length. "
    "Property taxes and the county budget dominated the discussion."
)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_words_only_kind():
    r = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=5, kinds=("words",))).extract(TEXT)
    assert all(t["kind"] == "word" for t in r.metadata["top_terms"])
    assert len(r.tags) <= 5
    assert list(r.tags) == [t["term"] for t in r.metadata["top_terms"]]


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_phrases_only_kind():
    r = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=5, kinds=("phrases",))).extract(TEXT)
    assert all(t["kind"] == "phrase" for t in r.metadata["top_terms"])


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_scores_are_real_and_descending():
    # lede 0.4.1 with_scores returns real composite scores in [0,1],
    # score-descending. Concern 2: not rank-derived placeholders.
    r = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=8)).extract(TEXT)
    scores = [t["score"] for t in r.metadata["top_terms"]]
    assert scores == sorted(scores, reverse=True), f"not descending: {scores}"
    assert all(0.0 <= s <= 1.0 for s in scores), f"out of [0,1]: {scores}"
    # Not all identical rank-derived values: real scores vary below the top.
    assert len(set(scores)) > 1, f"scores look synthetic: {scores}"


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_mixed_kinds_both_represented():
    # Concern 3: lede's unified ranking interleaves kinds; with both kinds
    # requested on text rich in each, words do not wholly crowd out phrases.
    r = LedeTopTermsExtractor(
        Cfg(type="lede_top_terms", n=8, kinds=("words", "phrases"))
    ).extract(TEXT)
    kinds = {t["kind"] for t in r.metadata["top_terms"]}
    assert "word" in kinds and "phrase" in kinds, f"only {kinds} present"


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_hard_hint_biases_terms():
    hinted = LedeTopTermsExtractor(
        Cfg(type="lede_top_terms", n=8, hints=["taxes"], hint_mode="hard")
    ).extract(TEXT)
    assert any("tax" in t.lower() for t in hinted.tags)


def _has_spacy_model(name="en_core_web_sm") -> bool:
    if not _has("spacy"):
        return False
    import spacy
    try:
        spacy.load(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
@pytest.mark.skipif(not _has_spacy_model(), reason="no spaCy model installed")
def test_expand_enriches_tags():
    # I-5: `expand` must enrich the OUTPUT tag set, not just input hints.
    # On text with pluralized terms, expansion (lemma) should add new tags
    # marked kind="expanded", scored below originals.
    base = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=8)).extract(TEXT)
    base_terms = {t.lower() for t in base.tags}

    expanded = LedeTopTermsExtractor(
        Cfg(type="lede_top_terms", n=8, expand={"kinds": ["lemma"]})
    ).extract(TEXT)
    exp_terms = {t.lower() for t in expanded.tags}

    # At least one tag in the expanded run that wasn't in the no-expand run.
    assert exp_terms - base_terms, f"no new tags from expansion: {exp_terms}"
    # The extra entries are marked kind="expanded".
    expanded_entries = [
        e for e in expanded.metadata["top_terms"] if e["kind"] == "expanded"
    ]
    assert expanded_entries, "expected at least one kind='expanded' entry"
    # tags <-> top_terms order invariant still holds.
    assert list(expanded.tags) == [e["term"] for e in expanded.metadata["top_terms"]]


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_expand_none_unchanged():
    # expand=None must produce tags identical to a plain run, no "expanded" kind.
    plain = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=8)).extract(TEXT)
    none = LedeTopTermsExtractor(
        Cfg(type="lede_top_terms", n=8, expand=None)
    ).extract(TEXT)
    assert list(plain.tags) == list(none.tags)
    assert all(e["kind"] != "expanded" for e in none.metadata["top_terms"])


def test_missing_lede_raises_actionable(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("lede"):
            raise ImportError("no lede")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError) as exc:
        LedeTopTermsExtractor(Cfg(type="lede_top_terms")).extract(TEXT)
    assert "lede" in str(exc.value).lower()
