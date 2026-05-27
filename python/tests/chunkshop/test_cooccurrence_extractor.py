"""Tier-1 spaCy-free co-occurrence edge extractor.

Pairing logic is tested deterministically by stubbing the rake/lede helpers;
one integration test exercises the real rake+lede path (gated on extras).
"""
from __future__ import annotations

import pytest

from chunkshop.extractors import load_extractor


def _make(monkeypatch, *, phrases, sentences, **cfg_over):
    """Build a CooccurrenceExtractor with the rake/lede helpers stubbed."""
    pytest.importorskip("rake_nltk")
    from chunkshop.config import CooccurrenceExtractor as Cfg
    cfg = Cfg.model_validate({"type": "cooccurrence", **cfg_over})
    ext = load_extractor(cfg)
    monkeypatch.setattr(ext, "_keyphrases", lambda text: phrases)
    monkeypatch.setattr(ext, "_salient_sentences", lambda text: sentences)
    return ext


def test_cooccurrence_pairs_within_salient_sentence(monkeypatch):
    ext = _make(
        monkeypatch,
        phrases=["alpha", "beta", "gamma"],
        sentences=["alpha and beta appear together.", "gamma is alone."],
    )
    res = ext.extract("ignored — helpers stubbed")
    # alpha+beta co-occur in sentence 1; gamma pairs with nothing.
    assert res.metadata["cooccur"] == [{"a": "alpha", "b": "beta", "weight": 1}]
    # keyphrases surface as tags (nodes).
    assert set(res.tags) == {"alpha", "beta", "gamma"}


def test_weight_counts_repeated_cooccurrence(monkeypatch):
    ext = _make(
        monkeypatch,
        phrases=["alpha", "beta"],
        sentences=["alpha beta.", "beta then alpha again."],
    )
    res = ext.extract("x")
    assert res.metadata["cooccur"] == [{"a": "alpha", "b": "beta", "weight": 2}]


def test_min_pair_count_filters_weak_edges(monkeypatch):
    ext = _make(
        monkeypatch,
        phrases=["alpha", "beta", "gamma"],
        sentences=["alpha beta.", "alpha gamma."],
        min_pair_count=2,
    )
    res = ext.extract("x")
    # alpha-beta and alpha-gamma each have weight 1 → both dropped at floor 2.
    assert res.metadata["cooccur"] == []


def test_empty_text_yields_no_edges(monkeypatch):
    ext = _make(monkeypatch, phrases=[], sentences=[])
    assert ext.extract("") == ext.extract("   ")
    assert ext.extract("").metadata["cooccur"] == []


def test_edges_sorted_by_weight_then_name(monkeypatch):
    ext = _make(
        monkeypatch,
        phrases=["a", "b", "c"],
        sentences=["a b c.", "a b."],  # a-b weight 2; a-c, b-c weight 1
    )
    res = ext.extract("x")
    weights = [e["weight"] for e in res.metadata["cooccur"]]
    assert weights == sorted(weights, reverse=True)
    assert res.metadata["cooccur"][0] == {"a": "a", "b": "b", "weight": 2}


def test_word_boundary_matching_avoids_substring_false_positives(monkeypatch):
    # "data" must NOT match inside "database"; with "platform" absent there is
    # no real co-occurrence, so no edge.
    ext = _make(
        monkeypatch,
        phrases=["data", "platform"],
        sentences=["the database is fast."],
    )
    assert ext.extract("x").metadata["cooccur"] == []


def test_word_boundary_matching_keeps_real_multiword_cooccurrence(monkeypatch):
    ext = _make(
        monkeypatch,
        phrases=["data platform", "ingest pipeline"],
        sentences=["the data platform ships the ingest pipeline."],
    )
    assert ext.extract("x").metadata["cooccur"] == [
        {"a": "data platform", "b": "ingest pipeline", "weight": 1}
    ]


def test_real_rake_lede_path_emits_pair_edges():
    """Integration: real rake + lede, no stubs. Gated on extras."""
    pytest.importorskip("rake_nltk")
    pytest.importorskip("lede")
    from chunkshop.config import CooccurrenceExtractor as Cfg
    ext = load_extractor(Cfg.model_validate({"type": "cooccurrence", "top_k": 10}))
    text = (
        "Alice manages the data platform team. "
        "Alice and the data platform team shipped the ingest pipeline. "
        "The ingest pipeline writes vectors to Postgres."
    )
    res = ext.extract(text)
    # Edges (if any) must be unordered pairs drawn from the emitted tags.
    tagset = set(res.tags)
    for e in res.metadata["cooccur"]:
        assert {"a", "b", "weight"} <= set(e)
        assert e["a"] in tagset and e["b"] in tagset
        assert e["a"] < e["b"]  # canonical unordered ordering
        assert e["weight"] >= 1
