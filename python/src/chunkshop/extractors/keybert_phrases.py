"""Embedding-based keyphrase extractor via KeyBERT.

KeyBERT scores candidate n-grams by cosine similarity to the document embedding
produced by a sentence-transformers model (default: ``all-MiniLM-L6-v2``, ~90 MB,
downloaded on first use and cached under ``~/.cache/huggingface/``).

Returns ``tags = [top_k phrases]`` and empty ``metadata``. Deterministic given
the same model + input (no sampling on top of the similarity scores).

Install: ``uv sync --extra keybert`` (``pip install "chunkshop[keybert]"``).
"""
from __future__ import annotations

from chunkshop.config import KeyBertPhrasesExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class KeyBertPhrasesExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        from keybert import KeyBERT

        # First call downloads the sentence-transformers model. sentence-transformers
        # emits its own progress to stderr; we don't suppress it (same UX as NLTK
        # auto-download in rake_keywords).
        self._kb = KeyBERT(model=cfg.model_name)

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={})

        pairs = self._kb.extract_keywords(
            text,
            keyphrase_ngram_range=tuple(self.cfg.keyphrase_ngram_range),
            top_n=self.cfg.top_k,
        )
        tags = [phrase for phrase, _score in pairs]
        return ExtractResult(tags=tags, metadata={})
