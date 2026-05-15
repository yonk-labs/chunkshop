"""Named Entity Recognition via spaCy.

Returns structured ``{label: [mentions]}`` keyed by spaCy entity label (ORG,
PERSON, GPE, etc.), filtered through a ``label_whitelist``. Mentions are
deduped within a label, preserving order of first appearance.

The spaCy model (default: ``en_core_web_sm``, ~50 MB) auto-downloads on first
use. Same UX as NLTK corpora in ``rake_keywords`` — the download prints to
stderr; we do NOT suppress it, so users see progress.

Install: ``uv sync --extra spacy`` (``pip install "chunkshop[spacy]"``).
"""
from __future__ import annotations

import logging

from chunkshop.config import SpacyEntitiesExtractor as Cfg
from chunkshop.extractors.result import ExtractResult

logger = logging.getLogger(__name__)


def _ensure_spacy_model(name: str):
    """Load a spaCy model by name, downloading it on first use if missing."""
    import spacy

    try:
        return spacy.load(name)
    except OSError:
        logger.info("model %r not found; downloading...", name)
        from spacy.cli import download

        download(name)
        return spacy.load(name)


class SpacyEntitiesExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self._nlp = _ensure_spacy_model(cfg.model)
        self._whitelist = set(cfg.label_whitelist)

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={"entities": {}})

        doc = self._nlp(text)
        grouped: dict[str, list[str]] = {}
        for ent in doc.ents:
            if ent.label_ not in self._whitelist:
                continue
            grouped.setdefault(ent.label_, []).append(ent.text)

        # Dedupe within each label, preserving first-appearance order.
        deduped = {
            label: list(dict.fromkeys(mentions)) for label, mentions in grouped.items()
        }
        return ExtractResult(tags=[], metadata={"entities": deduped})
