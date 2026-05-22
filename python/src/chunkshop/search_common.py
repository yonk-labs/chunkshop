"""Shared hybrid-search primitives reused by every backend's search module.

This module holds the backend-agnostic pieces of chunkshop's read API so the
four backend search modules (`search.py` for Postgres, plus `search_sqlite.py`,
`search_mariadb.py`, `search_clickhouse.py`) don't reinvent them:

  - `Hit`        : the result contract returned by every search function.
  - `_fuse_rrf`  : Reciprocal Rank Fusion across legs.
  - `_fuse_weighted` : min-max-normalized weighted fusion across legs.
  - `fuse`       : run the requested fusion + truncate to k (the part every
                   `hybrid_search` shares verbatim).
  - `validate_hybrid_args` : the leg/fusion/argument validation `hybrid_search`
                   does up front.

Why factor it out: the fusion math (RRF / weighted, dedup by (doc_id, seq_num),
`legs` provenance) is identical across backends. Only the per-leg SQL differs.
Keeping fusion in one place means a fusion fix lands once, not four times.

No backend imports here — pure Python + numpy-free. The `Hit` contract is
deliberately identical to what the Postgres module historically returned.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    doc_id: str
    seq_num: int
    text: str  # original_content
    score: float  # fused score (higher = better)
    metadata: dict
    legs: tuple[str, ...]  # which legs matched, e.g. ("semantic", "fts")
    embedded_text: str = ""  # embedded_content (I-14): the text that was
    # actually embedded. For the `hierarchy` chunker this PREPENDS the section
    # heading / case caption to the chunk body, so it carries framing context
    # (e.g. "Apple v. Pepper") that `text` (original_content) drops. Default ""
    # keeps existing callers/back-compat intact; populated by every backend's
    # semantic_search / keyword_search and carried through fusion.


def _fuse_rrf(
    leg_results: dict[str, list[Hit]], rrf_k: int
) -> dict[tuple[str, int], tuple[float, Hit, list[str]]]:
    """Reciprocal Rank Fusion: each leg contributes 1/(rrf_k + rank), rank 1-based."""
    fused: dict[tuple[str, int], tuple[float, Hit, list[str]]] = {}
    for leg, hits in leg_results.items():
        for rank, hit in enumerate(hits, start=1):
            key = (hit.doc_id, hit.seq_num)
            contrib = 1.0 / (rrf_k + rank)
            if key in fused:
                prev_score, prev_hit, prev_legs = fused[key]
                fused[key] = (prev_score + contrib, prev_hit, prev_legs + [leg])
            else:
                fused[key] = (contrib, hit, [leg])
    return fused


def _fuse_weighted(
    leg_results: dict[str, list[Hit]], weights: dict[str, float]
) -> dict[tuple[str, int], tuple[float, Hit, list[str]]]:
    """Weighted fusion: min-max normalize each leg to [0,1], sum weight*norm."""
    fused: dict[tuple[str, int], tuple[float, Hit, list[str]]] = {}
    for leg, hits in leg_results.items():
        if not hits:
            continue
        scores = [h.score for h in hits]
        lo, hi = min(scores), max(scores)
        span = hi - lo
        w = weights.get(leg, 1.0)
        for hit in hits:
            norm = 1.0 if span == 0 else (hit.score - lo) / span
            contrib = w * norm
            key = (hit.doc_id, hit.seq_num)
            if key in fused:
                prev_score, prev_hit, prev_legs = fused[key]
                fused[key] = (prev_score + contrib, prev_hit, prev_legs + [leg])
            else:
                fused[key] = (contrib, hit, [leg])
    return fused


def validate_hybrid_args(
    *,
    legs: tuple[str, ...],
    query,
    query_vec,
    fusion: str,
) -> None:
    """Shared up-front validation for every backend's hybrid_search.

    Raises ValueError on bad legs / missing query inputs / unknown fusion.
    """
    if not legs:
        raise ValueError("legs must be non-empty")
    unknown = set(legs) - {"semantic", "fts"}
    if unknown:
        raise ValueError(f"unknown legs: {sorted(unknown)}")
    if "semantic" in legs and query_vec is None:
        raise ValueError("legs includes 'semantic' but query_vec is None")
    if "fts" in legs and query is None:
        raise ValueError("legs includes 'fts' but query is None")
    if fusion not in {"rrf", "weighted"}:
        raise ValueError(f"fusion must be 'rrf' or 'weighted', got {fusion!r}")


def fuse(
    leg_results: dict[str, list[Hit]],
    *,
    k: int,
    fusion: str,
    weights: dict[str, float] | None,
    rrf_k: int,
) -> list[Hit]:
    """Fuse per-leg Hit lists into a single ranked top-k list.

    Dedups by (doc_id, seq_num); the surviving Hit records every leg that
    matched it in `legs`. Returns top-k by fused score (higher = better).
    """
    if fusion == "rrf":
        fused = _fuse_rrf(leg_results, rrf_k)
    else:
        fused = _fuse_weighted(leg_results, weights or {})

    out = [
        Hit(
            doc_id=hit.doc_id,
            seq_num=hit.seq_num,
            text=hit.text,
            score=score,
            metadata=hit.metadata,
            legs=tuple(sorted(set(matched_legs))),
            embedded_text=hit.embedded_text,
        )
        for (score, hit, matched_legs) in fused.values()
    ]
    out.sort(key=lambda h: h.score, reverse=True)
    return out[:k]


# Cap on how many distinct headings get prepended to a summary. On many-case
# retrievals (e.g. a broad SCOTUS query touching a dozen opinions) prepending
# every caption would drown the summary in caption noise and blow the token
# budget; the first few distinct headings carry the structural facts that
# extractive compression otherwise drops, so we keep those and stop.
_MAX_PREPENDED_HEADINGS = 5


def summarize_hits(
    hits: list[Hit],
    summarize_fn: Callable[..., str],
    *,
    max_length: int = 1200,
    hints: Sequence[str] | Mapping[str, float] | None = None,
    hint_focus: float = 0.7,
    hint_mode: str = "soft",
    prepend_headings: bool = True,
    use_embedded: bool = True,
) -> str:
    """Fast-mode RAG summary: concatenate retrieved chunks, summarize biased
    toward query hints, and (default) prepend the deduped chunk headings to the
    summary so structural facts (titles/captions) survive extractive compression.

    ``summarize_fn`` is INJECTED so chunkshop core never imports lede — the
    caller passes ``chunkshop.summarizers.lede.summarize`` or any callable with
    the ``(text, **kwargs) -> str`` contract.

    Why prepend headings to the OUTPUT rather than feed heading-bearing input:
    extractive summarizers (lede) compress the heading right back out of the
    body, so the case caption / section title — a structural fact a RAG answer
    needs — vanishes. Prepending the deduped headings to the produced summary
    re-attaches that framing for ~tens of tokens, lifting required-facts and
    caption retention far more than bulking up ``max_length`` would.

    Args:
        hits: ranked search results. Empty -> "".
        summarize_fn: injected ``(text, **kwargs) -> str`` summarizer.
        max_length: forwarded to ``summarize_fn``.
        hints: query keywords (list or {term: weight}). When ``None`` the hint
            kwargs are NOT forwarded, so summarizers that don't accept them
            stay on their own defaults (mirrors the lede extractor's gating).
        hint_focus / hint_mode: forwarded only when ``hints`` is provided.
        prepend_headings: prepend deduped ``metadata["heading"]`` values (in hit
            order, case-insensitive dedupe, capped at the first
            ``_MAX_PREPENDED_HEADINGS`` distinct headings) before the summary.
        use_embedded: build the body from ``h.embedded_text`` (heading-bearing,
            falling back to ``h.text`` when empty) rather than ``h.text``.
    """
    if not hits:
        return ""

    if use_embedded:
        parts = [(h.embedded_text or h.text) for h in hits]
    else:
        parts = [h.text for h in hits]
    body = "\n\n".join(parts)

    call_kwargs: dict = {"max_length": max_length}
    # Only forward hint controls when hints are given — keeps the no-hint call
    # on the summarizer's own defaults and avoids forcing hint kwargs on
    # summarizers that don't accept them.
    if hints is not None:
        call_kwargs["hints"] = hints
        call_kwargs["hint_focus"] = hint_focus
        call_kwargs["hint_mode"] = hint_mode

    summary = summarize_fn(body, **call_kwargs)

    if not prepend_headings:
        return summary

    seen: set[str] = set()
    headings: list[str] = []
    for h in hits:
        heading = h.metadata.get("heading")
        if not heading:
            continue
        key = heading.casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(heading)
        if len(headings) >= _MAX_PREPENDED_HEADINGS:
            break

    if not headings:
        return summary

    return "\n".join(headings) + "\n\n" + summary
