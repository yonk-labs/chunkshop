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

``SearchResult`` and ``search()`` are the user-facing entry point. ``search()``
wraps ``hybrid_search`` (pg default) and optionally ``summarize_hits``, exposing
three return modes:

  - ``"chunks"``        — returns the fused hit list; no summarization, no lede
                          import.  (SC-010: chunks mode must be lede-free.)
  - ``"summary"``       — summarizes the hits; chunks list is discarded (empty).
  - ``"summary+chunks"``— summarizes AND returns the hit list.

All lede-related imports (``lede.extract.top_terms`` via the shim, lede_spacy
``expand_hints``) are deferred to the summary path so the chunks-only path stays
import-boundary-clean.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Optional


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
        # RRF's constant must be positive: each leg contributes 1/(rrf_k + rank)
        # with rank >= 1, so rrf_k < 1 can zero the denominator (e.g. rrf_k=-1
        # at rank 1) and divide by zero. Reject up front with a clear error
        # rather than leaking a ZeroDivisionError.
        if rrf_k < 1:
            raise ValueError(f"rrf_k must be a positive integer (>= 1), got {rrf_k}")
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
    compress_fn: Optional[Callable[[str], str]] = None,
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
    if compress_fn is not None:
        summary = compress_fn(summary)

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


# ---------------------------------------------------------------------------
# SearchResult + search() — user-facing entry point (SC-008, SC-009, SC-010)
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """Container returned by ``search()``.

    Attributes:
        chunks:  fused ``Hit`` list (empty when ``return_mode="summary"``).
        summary: extractive summary string (``None`` when ``return_mode="chunks"``).
        query:   the original query string, preserved for logging / display.
    """
    chunks: list  # list[Hit]
    summary: Optional[str]
    query: str


def _derive_query_hints(query: str) -> list[str]:
    """Return salient terms from *query* via the lede shim (SC-016 compliant).

    Routes through ``chunkshop.extractors.lede_top_terms.query_hints`` — the
    designated shim file that is permitted to import lede.  This function itself
    contains NO ``from lede`` / ``import lede`` statement; the import happens
    inside the shim, lazily, only when a summary mode is requested.
    """
    from chunkshop.extractors.lede_top_terms import query_hints  # lazy — summary path only
    return query_hints(query, n=6)


def _summarize_for_query(
    hits: list[Hit],
    query: str,
    *,
    summarize_fn: Optional[Callable[..., str]],
    summary_hints: Optional[list[str]],
    summary_expand,
    max_length: int,
    compress_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """Run extractive summarization biased toward the query.

    ``summarize_fn`` is required for any summary mode; raises ``ValueError``
    when absent so the caller gets an actionable message rather than a confusing
    ``TypeError`` inside ``summarize_hits``.

    Hint resolution order:
      1. Explicit ``summary_hints`` (caller wins).
      2. Auto-derived from *query* via lede ``top_terms`` shim (SC-016).
      3. If expansion config provided, route through ``chunkshop.hints.expand_hints``.
    """
    if summarize_fn is None:
        raise ValueError(
            "summary return modes require summarize_fn "
            "(e.g. chunkshop.summarizers.lede.summarize)"
        )
    hints: Optional[Sequence] = (
        summary_hints if summary_hints is not None else _derive_query_hints(query)
    )
    if summary_expand is not None and hints:
        from chunkshop.hints import expand_hints  # lazy — summary path only
        hints = expand_hints(
            hints,
            kinds=tuple(summary_expand.kinds),
            top_k=summary_expand.top_k,
            expand_weight=summary_expand.expand_weight,
        )
    return summarize_hits(
        hits, summarize_fn, max_length=max_length, hints=hints, compress_fn=compress_fn
    )


def search(
    dsn: str,
    *,
    schema: str,
    table: str,
    query: Optional[str] = None,
    query_vec=None,
    k: int = 10,
    legs=("semantic", "fts"),
    where: Optional[dict] = None,
    fusion: str = "rrf",
    return_mode: Literal["chunks", "summary+chunks", "summary"] = "chunks",
    summarize_fn: Optional[Callable[..., str]] = None,
    summary_hints: Optional[list[str]] = None,
    summary_expand=None,
    summary_max_length: int = 1200,
    language: str = "english",
    vector_metric: str = "cosine",
    compress_fn: Optional[Callable[[str], str]] = None,
) -> SearchResult:
    """Hybrid search with optional summarization — the chunkshop read entry point.

    Wraps :func:`chunkshop.search.hybrid_search` (Postgres default) and
    :func:`summarize_hits` into a single call with three return modes.

    SC-010: ``return_mode="chunks"`` performs NO summarization and triggers NO
    lede import — ``hybrid_search`` is imported lazily below, but lede / top_terms
    are never touched on this path.

    Args:
        dsn: Postgres DSN.
        schema / table: target table coordinates.
        query: free-text query (required when legs includes "fts").
        query_vec: embedding vector (required when legs includes "semantic").
        k: number of results to return.
        legs: retrieval legs; subset of ``("semantic", "fts")``.
        where: structured filter dict forwarded to ``hybrid_search``.
        fusion: ``"rrf"`` or ``"weighted"``.
        return_mode: controls what the ``SearchResult`` carries.
        summarize_fn: injectable ``(text, **kwargs) -> str`` summarizer.
            Required for any non-chunks mode.
        summary_hints: explicit hint terms for summarization; overrides
            auto-derivation from *query*.
        summary_expand: optional ``HintExpansion`` config for synonym / lemma
            expansion of hints via lede_spacy.
        summary_max_length: forwarded as ``max_length`` to ``summarize_hits``.
        language: FTS language, forwarded to ``hybrid_search``.
        vector_metric: Postgres/pgvector semantic metric (``"cosine"``,
            ``"inner_product"``, or ``"l2"``), forwarded to ``hybrid_search``.

    Returns:
        ``SearchResult(chunks, summary, query)``.
    """
    from chunkshop.search import hybrid_search  # lazy: pg backend default
    hits = hybrid_search(
        dsn,
        schema=schema,
        table=table,
        query=query,
        query_vec=query_vec,
        k=k,
        legs=tuple(legs),
        where=where,
        fusion=fusion,
        language=language,
        vector_metric=vector_metric,
    )

    if return_mode == "chunks":
        return SearchResult(chunks=hits, summary=None, query=query or "")

    summary = _summarize_for_query(
        hits,
        query or "",
        summarize_fn=summarize_fn,
        summary_hints=summary_hints,
        summary_expand=summary_expand,
        max_length=summary_max_length,
        compress_fn=compress_fn,
    )
    chunks = [] if return_mode == "summary" else hits
    return SearchResult(chunks=chunks, summary=summary, query=query or "")
