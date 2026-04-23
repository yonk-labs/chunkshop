"""Pure scoring functions for bakeoff evaluation (SC-005).

Given a ranked list of retrieved doc_ids for a query and a single gold doc_id,
compute recall@k for each k in `k_values` and MRR. Aggregation across queries
is a simple arithmetic mean. No external deps — kept here for easy unit test.
"""
from __future__ import annotations

from typing import Iterable


def score_query(
    ranked_doc_ids: list[str],
    gold_doc_id: str,
    k_values: Iterable[int],
) -> dict[str, float]:
    """Score one query against one gold doc_id.

    Returns `{recall_at_K: 0|1, ..., mrr: float}`. MRR uses 1/rank of the first
    gold hit in the ranked list (unbounded — callers should slice to top-K
    before scoring if they want bounded MRR).
    """
    result: dict[str, float] = {}
    for k in k_values:
        result[f"recall_at_{k}"] = 1 if gold_doc_id in ranked_doc_ids[:k] else 0
    mrr = 0.0
    for rank, did in enumerate(ranked_doc_ids, start=1):
        if did == gold_doc_id:
            mrr = 1.0 / rank
            break
    result["mrr"] = mrr
    return result


def aggregate_scores(per_query: list[dict[str, float]]) -> dict[str, float]:
    """Arithmetic mean of each metric across all queries. Empty input -> {}."""
    if not per_query:
        return {}
    n = len(per_query)
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / n for k in keys}
