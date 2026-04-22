"""Language-detection extractor.

Uses ``langdetect`` — pure Python, no model download, deterministic with a seed.
Returns ``metadata = {"language": ISO-639-1 code, "language_confidence": float}``
and empty ``tags``. On empty or undetectable input, language is ``None`` and
confidence is ``0.0`` (never raises).

Install: ``uv sync --extra lang`` (``pip install "chunkshop[lang]"``).
"""
from __future__ import annotations

from chunkshop.config import LangDetectExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class LangDetectExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        from langdetect import DetectorFactory

        # Deterministic across runs — the library's default is non-deterministic
        # due to a probabilistic classifier; seed=0 fixes it for reproducible ingest.
        DetectorFactory.seed = 0

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(
                tags=[], metadata={"language": None, "language_confidence": 0.0}
            )

        from langdetect import detect_langs
        from langdetect.lang_detect_exception import LangDetectException

        try:
            candidates = detect_langs(text)
        except LangDetectException:
            # Input has no linguistic features (numbers, symbols, etc.) — return null.
            return ExtractResult(
                tags=[], metadata={"language": None, "language_confidence": 0.0}
            )

        top = candidates[0] if candidates else None
        if top is None:
            return ExtractResult(
                tags=[], metadata={"language": None, "language_confidence": 0.0}
            )

        return ExtractResult(
            tags=[],
            metadata={"language": top.lang, "language_confidence": float(top.prob)},
        )
