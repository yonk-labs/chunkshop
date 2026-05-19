"""Zero-network default consolidator.

summary  = first N non-trivial sentences (stand-in for an abstractive summary;
           users wire an LLM callable for better summaries).
facts    = one proposition per sentence that looks declarative; triple fields
           (subject/predicate/object) are left None under the extractive
           default — by design the soft-invalidate path then no-ops (spec O8 /
           §5). support_span carries the proposition text (the embedded body).
"""
from __future__ import annotations
import re

_SENT = re.compile(r"(?<=[.!?])\s+")
_TAG = re.compile(r"^\[[^\]]+\]\s*")


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = _TAG.sub("", line).strip()
        if not line:
            continue
        out.extend(s.strip() for s in _SENT.split(line) if s.strip())
    return out


def consolidate(text: str, *, max_sentences: int = 4,
                 min_words: int = 4, **_kw) -> dict:
    if not text or not text.strip():
        return {"summary": "", "facts": []}
    sents = _sentences(text)
    summary = " ".join(sents[:max_sentences])
    facts = []
    for s in sents:
        if len(s.split()) < min_words:
            continue
        facts.append({"subject": None, "predicate": None, "object": None,
                      "support_span": s, "confidence": None})
    return {"summary": summary, "facts": facts}


__all__ = ["consolidate"]
