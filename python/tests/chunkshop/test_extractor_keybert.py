"""Unit tests for keybert_phrases extractor.

Skips cleanly if `[keybert]` extra isn't installed. SC-001: ≥3 fixture inputs
producing expected phrase output (non-empty tags, salient topic present).
"""
from __future__ import annotations

import pytest

pytest.importorskip("keybert")

from chunkshop.config import KeyBertPhrasesExtractor
from chunkshop.extractors import load_extractor


LEGAL_TEXT = (
    "The Supreme Court ruled on civil rights in Bostock v. Clayton County. "
    "Justice Neil Gorsuch wrote the majority opinion interpreting Title VII "
    "to include sexual orientation and gender identity."
)

TECH_TEXT = (
    "Vector databases index high-dimensional embeddings for approximate nearest "
    "neighbor search. pgvector brings HNSW indexes and cosine distance operators "
    "to Postgres, enabling retrieval-augmented generation pipelines."
)

BIO_TEXT = (
    "Mitochondria are double-membraned organelles found in eukaryotic cells. "
    "They generate adenosine triphosphate through oxidative phosphorylation, "
    "which powers most cellular biochemical reactions."
)


@pytest.fixture(scope="module")
def extractor():
    # Scope module so the MiniLM model loads once — avoids ~90MB repeat downloads
    # in CI and ~1s repeat init locally.
    return load_extractor(KeyBertPhrasesExtractor(type="keybert_phrases", top_k=5))


def test_keybert_returns_phrases_legal(extractor):
    r = extractor.extract(LEGAL_TEXT)
    assert 1 <= len(r.tags) <= 5
    assert all(isinstance(t, str) and t for t in r.tags)
    assert r.metadata == {}
    lowered = " ".join(r.tags).lower()
    assert any(
        term in lowered
        for term in ("bostock", "gorsuch", "civil rights", "title vii", "supreme court")
    )


def test_keybert_returns_phrases_tech(extractor):
    r = extractor.extract(TECH_TEXT)
    assert 1 <= len(r.tags) <= 5
    lowered = " ".join(r.tags).lower()
    assert any(
        term in lowered
        for term in ("vector", "pgvector", "hnsw", "embeddings", "postgres")
    )


def test_keybert_returns_phrases_bio(extractor):
    r = extractor.extract(BIO_TEXT)
    assert 1 <= len(r.tags) <= 5
    lowered = " ".join(r.tags).lower()
    assert any(
        term in lowered for term in ("mitochondria", "cellular", "eukaryotic", "adenosine")
    )


def test_keybert_empty_input_returns_empty_tags(extractor):
    r = extractor.extract("")
    assert r.tags == []
    assert r.metadata == {}


def test_keybert_whitespace_input_returns_empty_tags(extractor):
    r = extractor.extract("   \n\t  ")
    assert r.tags == []
    assert r.metadata == {}
