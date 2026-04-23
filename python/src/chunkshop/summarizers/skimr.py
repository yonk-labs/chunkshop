"""skimr adapter — returns a plain string from skimr.summarize.

skimr's native signature is ``summarize(text, max_length=500, *, mode='default',
attach=None) -> SummaryResult``. The SummaryResult's ``__str__`` returns the
summary text, but chunkshop's callable contract is explicit: ``(text, **kwargs)
-> str``. This shim extracts ``.summary`` explicitly so the contract is obvious
at the call site rather than relying on implicit ``__str__`` coercion.
"""
from __future__ import annotations


def summarize(text: str, **kwargs) -> str:
    """Extractive summary via skimr, returning a plain string.

    Forwards all kwargs (``max_length``, ``mode``, ``attach``) to skimr.summarize.
    Returns the empty string for empty input.
    """
    if not text or not text.strip():
        return ""
    from skimr import summarize as _skimr_summarize
    result = _skimr_summarize(text, **kwargs)
    # skimr.summarize returns SummaryResult; extract .summary explicitly.
    return result.summary


__all__ = ["summarize"]
