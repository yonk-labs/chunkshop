"""L4 (SC-018, SC-022): expand_hints shim + wrapped errors."""
import importlib.util

import pytest

from chunkshop.hints import expand_hints


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _has_spacy_model(name="en_core_web_sm") -> bool:
    if not _has("spacy") or not _has("lede_spacy"):
        return False
    import spacy
    try:
        spacy.load(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_spacy_model(), reason="no spaCy model installed")
def test_lemma_expansion_adds_lemmatized_form():
    out = expand_hints(["counties"], kinds=("lemma",))
    assert "counties" in out
    assert any(t.lower() == "county" for t in out)


@pytest.mark.skipif(
    not _has_spacy_model("en_core_web_md"),
    reason="similar expansion needs a vector model (en_core_web_md); requires lede-spacy>=0.4.2",
)
def test_similar_expansion_adds_vector_neighbors():
    # lede-spacy>=0.4.2 fixed _nlp() to load a vector model for the 'similar'
    # kind (was hardcoded to en_core_web_sm, which has no vectors). It should
    # now return the original plus vector neighbors instead of raising.
    out = expand_hints(["king"], kinds=("similar",), top_k=5)
    assert "king" in out
    assert len(out) > 1


def test_synonyms_without_extra_raises_actionable(monkeypatch):
    import chunkshop.hints as H

    def boom(*a, **k):
        raise ImportError("nltk missing")

    monkeypatch.setattr(H, "_load_expand", lambda: boom)
    with pytest.raises(RuntimeError) as exc:
        expand_hints(["car"], kinds=("synonyms",))
    msg = str(exc.value).lower()
    assert "synonyms" in msg or "nltk" in msg or "lede-spacy" in msg


# ---------------------------------------------------------------------------
# Task 13 — wire expand: into summarizer dispatch + lede_top_terms extractor
# SC-020, SC-021, SC-022
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_expand(monkeypatch):
    import chunkshop.chunkers._summarizer as M
    monkeypatch.setattr(M, "expand_hints", lambda h, **k: list(h) + ["__expanded__"], raising=True)
    yield


_ECHO = {}


def _echo(text, **kwargs):
    _ECHO.clear()
    _ECHO["kwargs"] = dict(kwargs)
    return text


def test_summarizer_expand_runs_on_resolved_hints(_stub_expand):
    import sys, types
    from chunkshop.chunkers._summarizer import build_summarizer
    from chunkshop.config import CallableSummarizer
    mod = types.ModuleType("_t13_spy")
    mod._echo = _echo
    sys.modules["_t13_spy"] = mod
    cfg = CallableSummarizer(
        mode="callable", module="_t13_spy", function="_echo",
        kwargs={"hints": ["a"]},
        expand={"kinds": ["lemma"]},
        hints_from_meta="lede_hints",
    )
    fn = build_summarizer(cfg)
    fn("body", {"lede_hints": ["counties"]})
    # The per-doc hint ["counties"] is what gets expanded (resolve-then-expand).
    assert _ECHO["kwargs"]["hints"] == ["counties", "__expanded__"]


def test_summarizer_expand_noop_without_hints(_stub_expand):
    import sys, types
    from chunkshop.chunkers._summarizer import build_summarizer
    from chunkshop.config import CallableSummarizer
    mod = types.ModuleType("_t13_spy2")
    mod._echo = _echo
    sys.modules["_t13_spy2"] = mod
    cfg = CallableSummarizer(
        mode="callable", module="_t13_spy2", function="_echo",
        kwargs={}, expand={"kinds": ["lemma"]},
    )
    fn = build_summarizer(cfg)
    fn("body", {})
    assert "hints" not in _ECHO["kwargs"]


def test_extractor_expand_runs(monkeypatch):
    import importlib.util
    if importlib.util.find_spec("lede") is None:
        import pytest as _p; _p.skip("lede not installed")
    import chunkshop.extractors.lede_top_terms as E
    captured = {}
    real = E.top_terms if hasattr(E, "top_terms") else None
    # Stub expand_hints to mark expansion happened.
    monkeypatch.setattr(E, "expand_hints", lambda h, **k: list(h) + ["taxes"], raising=True)
    from chunkshop.config import LedeTopTermsExtractor as Cfg
    ext = E.LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=8, hints=["zoning"], hint_mode="hard", expand={"kinds": ["lemma"]}))
    r = ext.extract("The council discussed taxes and zoning. Taxes and zoning dominated.")
    # With expansion adding "taxes" and hard mode, a tax term should surface.
    assert any("tax" in t.lower() for t in r.tags)
