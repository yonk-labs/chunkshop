"""Co-occurrence edge extractor — spaCy-free Tier-1 prose edges.

rake extracts salient keyphrases (the **nodes**); lede selects the salient
sentences (the co-occurrence **windows**). Two keyphrases appearing in the same
salient sentence emit a weak, UNDIRECTED, UNTYPED ``co_occurs`` candidate.

Edges are emitted as ``metadata['cooccur']`` (a list of ``{a, b, weight}``,
``a < b`` canonical ordering) because the ExtractResult contract has no edge
surface — the consumer (e.g. pg-raggraph) materializes graph edges from that
metadata. Keyphrases also surface as ``tags`` so the node set is queryable.

Deterministic, CPU-only, no LLM, no spaCy. Needs the ``[extractors]``
(rake-nltk) + ``[lede]`` extras; both are imported lazily.
"""
from __future__ import annotations

import re
from collections import Counter

from chunkshop.config import CooccurrenceExtractor as Cfg
from chunkshop.extractors.result import ExtractResult

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


class CooccurrenceExtractor:
    def __init__(self, cfg: Cfg):
        # Mirror RakeKeywordsExtractor's nltk bootstrap (idempotent, cached).
        import nltk
        for resource in ("corpora/stopwords", "tokenizers/punkt", "tokenizers/punkt_tab"):
            try:
                nltk.data.find(resource)
            except LookupError:
                nltk.download(resource.split("/")[-1], quiet=True)
        from rake_nltk import Rake
        self._rake_cls = Rake
        self.cfg = cfg

    def _keyphrases(self, text: str) -> list[str]:
        r = self._rake_cls(min_length=1)
        r.extract_keywords_from_text(text)
        ranked = r.get_ranked_phrases()
        return [p for p in ranked if len(p) >= self.cfg.min_chars][: self.cfg.top_k]

    def _salient_sentences(self, text: str) -> list[str]:
        from chunkshop.summarizers.lede import summarize
        summary = summarize(text, max_length=self.cfg.max_summary_chars)
        return [s.strip() for s in _SENT_SPLIT.split(summary) if s.strip()]

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={"cooccur": []})

        phrases = self._keyphrases(text)
        sentences = self._salient_sentences(text)

        # Word-boundary matchers (compiled once per phrase), not raw substring:
        # substring would false-positive (e.g. "data" inside "database"),
        # inflating co-occurrence noise. \b keeps multi-word phrases working.
        matchers = [
            (p, re.compile(rf"\b{re.escape(p.lower())}\b")) for p in phrases
        ]
        pair_counts: Counter = Counter()
        for sent in sentences:
            sl = sent.lower()
            present = sorted({p for (p, pat) in matchers if pat.search(sl)})
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    pair_counts[(present[i], present[j])] += 1

        edges = [
            {"a": a, "b": b, "weight": w}
            for (a, b), w in pair_counts.items()
            if w >= self.cfg.min_pair_count
        ]
        # Stable, useful order: strongest first, then alphabetical.
        edges.sort(key=lambda e: (-e["weight"], e["a"], e["b"]))
        return ExtractResult(tags=phrases, metadata={"cooccur": edges})
