"""lede fact extractor — salient sentences as propositions.

lede selects the most salient sentences; each becomes one fact whose
``support_span`` is the sentence and whose ``subject/predicate/object`` are left
None (sparse/proposition-style, matching SP-A's extractive degrade path).
``confidence`` is a rank-decay score in [0,1] (first sentence most confident).
This is the documented per-extractor meaning of confidence for lede — NOT
calibrated against the spaCy or LLM extractors.
"""
from __future__ import annotations
import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _lede_summary(text: str, **kwargs) -> str:
    """Indirection point so tests can stub lede without the extra installed."""
    from chunkshop.summarizers.lede import summarize
    return summarize(text, **kwargs)


def extract_facts(text: str, *, max_facts: int = 10, **kwargs) -> list[dict]:
    if not text or not text.strip():
        return []
    summary = _lede_summary(text, **kwargs)
    sentences = [s.strip() for s in _SENT_SPLIT.split(summary) if s.strip()]
    sentences = sentences[:max_facts]
    n = len(sentences)
    facts: list[dict] = []
    for i, sent in enumerate(sentences):
        facts.append({
            "subject": None, "predicate": None, "object": None,
            "support_span": sent,
            "confidence": round(1.0 - (i / n if n else 0.0), 3),
        })
    return facts


__all__ = ["extract_facts"]
