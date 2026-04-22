"""Unit tests for the lang_detect extractor.

Skips cleanly if the `[lang]` extra isn't installed. SC-003 requires detection
across at least three language fixtures (English, French, German) with >0.5
confidence.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langdetect")

from chunkshop.config import LangDetectExtractor
from chunkshop.extractors import load_extractor


ENGLISH = (
    "The quick brown fox jumps over the lazy dog in a meadow filled with "
    "wildflowers as the afternoon sun begins to dip below the tall pine trees."
)
FRENCH = (
    "Le chat noir dort paisiblement sur le canape pendant que la pluie tombe "
    "doucement dehors, creant une ambiance tranquille dans le salon cet apres-midi."
)
GERMAN = (
    "Der schnelle braune Fuchs springt mit Leichtigkeit uber den faulen Hund, "
    "wahrend im Garten die Vogel zwitschern und die Sonne hell am Himmel scheint."
)


def test_lang_detect_english_returns_en_with_confidence():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(ENGLISH)
    assert r.tags == []
    assert r.metadata["language"] == "en"
    assert r.metadata["language_confidence"] > 0.5


def test_lang_detect_french_returns_fr():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(FRENCH)
    assert r.metadata["language"] == "fr"
    assert r.metadata["language_confidence"] > 0.5


def test_lang_detect_german_returns_de():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(GERMAN)
    assert r.metadata["language"] == "de"
    assert r.metadata["language_confidence"] > 0.5


def test_lang_detect_empty_input_returns_null():
    """Empty/whitespace input must not crash. Returns None language."""
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract("")
    assert r.tags == []
    assert r.metadata["language"] is None
    assert r.metadata["language_confidence"] == 0.0
