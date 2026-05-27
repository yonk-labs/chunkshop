"""caveman reducer — strip fluff/stopwords so an LLM sees fewer tokens.

Implements the summarizer contract ``summarize(text, **kwargs) -> str`` so it is
swappable anywhere lede is (CallableSummarizer ``module:`` path, the read-time
``compress_fn`` in ``summarize_hits``). This is a REDUCTION strategy, NOT a fact
extractor: it shrinks any text by dropping low-information tokens while keeping
meaning-bearing words. Pure Python, no deps, deterministic, idempotent.
"""
from __future__ import annotations

_STOPWORDS = frozenset(
    """a an and are as at be been being but by for from had has have he her him
    his i in into is it its of on or our she that the their them they this to
    was we were will with you your over it's""".split()
)


def summarize(text: str, **kwargs) -> str:
    if not text or not text.strip():
        return ""
    kept = [
        tok
        for tok in text.split()
        if tok.strip(".,;:!?\"'()[]").casefold() not in _STOPWORDS
    ]
    return " ".join(kept)


__all__ = ["summarize"]
