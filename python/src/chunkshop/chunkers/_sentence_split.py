"""Sentence splitting helpers for the semantic chunker.

Two splitters:
- `naive` (default) — regex on `.`, `!`, `?` + whitespace. Zero dependencies, fast,
  not ML-aware. Misfires on abbreviations ("Dr.", "e.g.") but the semantic chunker
  only needs approximate sentence boundaries — exact identity doesn't affect the
  boundary-detection signal much.
- `nltk` (opt-in) — soft-imports NLTK's `sent_tokenize` for users who already have
  the `[extractors]` extra installed. Downloads `punkt_tab` on first use.
"""
from __future__ import annotations

import re
from typing import Callable


_TERMINATOR = re.compile(r"(?<=[.!?])\s+")


def naive_sentences(text: str) -> list[str]:
    """Split on sentence terminators (., !, ?) followed by whitespace.

    Strips the result and drops empty items. Not ML-aware — won't handle
    abbreviations correctly. Good enough for embedding-similarity boundary
    detection where exact sentence identity doesn't matter.
    """
    if not text or not text.strip():
        return []
    parts = _TERMINATOR.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _nltk_sentences(text: str) -> list[str]:
    """NLTK-based sentence splitter. Soft-imports nltk; falls back to naive on
    import failure so the chunker still works when the extra isn't installed."""
    try:
        import nltk
    except ImportError:
        return naive_sentences(text)
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)
    from nltk.tokenize import sent_tokenize
    return [s.strip() for s in sent_tokenize(text) if s.strip()]


def load_sentence_splitter(kind: str) -> Callable[[str], list[str]]:
    if kind == "naive":
        return naive_sentences
    if kind == "nltk":
        return _nltk_sentences
    raise ValueError(f"unknown sentence_splitter: {kind!r}")


__all__ = ["naive_sentences", "load_sentence_splitter"]
