"""lede adapter — returns a plain string from lede.summarize.

lede's native signature is ``summarize(text, max_length=500, *, mode='default',
attach=None) -> SummaryResult``. The SummaryResult's ``__str__`` returns the
summary text, but chunkshop's callable contract is explicit: ``(text, **kwargs)
-> str``. This shim extracts ``.summary`` explicitly so the contract is obvious
at the call site rather than relying on implicit ``__str__`` coercion.

(``lede`` is the published name of what was prototyped under the working name
``skimr``; the chunkshop summarizer module follows the published name.)
"""
from __future__ import annotations


def summarize(text: str, **kwargs) -> str:
    """Extractive summary via lede, returning a plain string.

    Forwards all kwargs (``max_length``, ``mode``, ``attach``) to lede.summarize.
    Returns the empty string for empty input.
    """
    if not text or not text.strip():
        return ""
    from lede import summarize as _lede_summarize
    result = _lede_summarize(text, **kwargs)
    # lede.summarize returns SummaryResult; extract .summary explicitly.
    return result.summary


__all__ = ["summarize"]
