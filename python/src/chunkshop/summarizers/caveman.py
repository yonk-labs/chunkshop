"""caveman reducer — strip fluff/stopwords so an LLM sees fewer tokens.

Implements the summarizer contract ``summarize(text, **kwargs) -> str`` so it is
swappable anywhere lede is (CallableSummarizer ``module:`` path, the read-time
``compress_fn`` in ``summarize_hits``). This is a REDUCTION strategy, NOT a fact
extractor: it shrinks any text by dropping low-information tokens while keeping
meaning-bearing words. Pure Python, no deps, deterministic, idempotent.

Stopword matching is casefolded, so short acronyms that collide with a stopword
(IT, US, ...) are stripped too. That is an accepted loss for a fluff reducer.
"""
from __future__ import annotations

_STOPWORDS = frozenset(
    """a an and are as at be been being but by for from had has have he her him
    his i in into is it its of on or our she that the their them they this to
    was we were will with you your over it's""".split()
)

_PUNCT = ".,;:!?\"'()[]"


def summarize(text: str, **kwargs) -> str:
    if not text or not text.strip():
        return ""
    kept = []
    for tok in text.split():
        core = tok.strip(_PUNCT)
        # Drop stopwords and tokens that are punctuation-only (no fluff signal).
        if not core or core.casefold() in _STOPWORDS:
            continue
        kept.append(tok)
    return " ".join(kept)


__all__ = ["summarize"]
