"""lede_top_terms extractor — ranked salient words/phrases via lede.extract.top_terms.

lede is imported lazily inside extract() so chunkshop core never imports lede
at module load time (SC-016). top_terms is Python-only in lede v0.4.

Uses lede 0.4.1's ``with_scores=True``: a single call returns a unified,
score-descending ``tuple[TermScore(term, score, kind)]`` where ``score`` is
lede's real composite TF-IDF/phrase-frequency value and ``kind`` is the
singular ``"word"``/``"phrase"`` label. lede owns the cross-kind ranking and
the top-N cut, so chunkshop is a thin map — no per-kind calls, no rank-derived
scores, no merge heuristic.

When ``expand`` is configured it enriches the *produced* tag set (lemma /
synonyms / similar of the top terms), not just the input hints. Expanded terms
are appended marked ``kind="expanded"`` and scored below the originals (they are
derived, so they rank lowest). The ``hints``/``hint_focus``/``hint_mode``
controls still bias lede's own ranking independently of this output expansion.
"""
from __future__ import annotations

from chunkshop.config import LedeTopTermsExtractor as Cfg
from chunkshop.extractors.result import ExtractResult
from chunkshop.hints import expand_hints


class LedeTopTermsExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def extract(self, text: str) -> ExtractResult:
        try:
            from lede.extract import top_terms
        except ImportError as exc:
            raise RuntimeError(
                "lede_top_terms extractor requires lede>=0.4.2. "
                "Install with: pip install 'lede>=0.4.2' (or the chunkshop [lede] extra)."
            ) from exc

        # Resolve effective hints, applying expansion if configured.
        hints = self.cfg.hints
        if self.cfg.expand is not None and hints:
            hints = expand_hints(
                hints,
                kinds=tuple(self.cfg.expand.kinds),
                top_k=self.cfg.expand.top_k,
                expand_weight=self.cfg.expand.expand_weight,
            )

        kwargs: dict = dict(n=self.cfg.n, kinds=tuple(self.cfg.kinds), with_scores=True)
        # Only forward hint controls when there are hints — keeps the no-hint
        # call on lede's own defaults.
        if hints is not None:
            kwargs["hints"] = hints
            kwargs["hint_focus"] = self.cfg.hint_focus
            kwargs["hint_mode"] = self.cfg.hint_mode

        results = top_terms(text, **kwargs)  # tuple[TermScore(term, score, kind)]
        entries = [
            {"term": ts.term, "score": float(ts.score), "kind": ts.kind}
            for ts in results
        ]

        # I-5: enrich the OUTPUT tag set when expand is configured. Expansion of
        # the produced terms (not just the input hints) — derived terms are
        # appended with kind="expanded", scored below the originals.
        if self.cfg.expand is not None:
            base_terms = [e["term"] for e in entries]
            expanded = expand_hints(
                base_terms,
                kinds=tuple(self.cfg.expand.kinds),
                top_k=self.cfg.expand.top_k,
                expand_weight=self.cfg.expand.expand_weight,
            )
            existing_lower = {e["term"].lower() for e in entries}
            existing_scores = [e["score"] for e in entries]
            # Expanded terms are derived, so rank them below originals.
            expanded_score = (
                min(existing_scores) * self.cfg.expand.expand_weight
                if existing_scores
                else self.cfg.expand.expand_weight
            )
            for term in expanded:
                if term.lower() not in existing_lower:
                    entries.append(
                        {"term": term, "score": expanded_score, "kind": "expanded"}
                    )
                    existing_lower.add(term.lower())

        tags = [e["term"] for e in entries]
        return ExtractResult(tags=tags, metadata={"top_terms": entries})


def query_hints(query: str, n: int = 6) -> list[str]:
    """Return the top-n salient terms from *query* as a plain string list.

    Thin shim so search_common.py can derive query hints without importing lede
    directly (SC-016: only shim files may import lede). Lazy import — lede is
    not loaded unless a summary return mode is requested.
    """
    try:
        from lede.extract import top_terms
    except ImportError as exc:
        raise RuntimeError(
            "query hint derivation requires lede>=0.4.2. "
            "Install with: pip install 'lede>=0.4.2' (or the chunkshop [lede] extra)."
        ) from exc
    results = top_terms(query, n=n, with_scores=True)
    return [ts.term for ts in results]
