"""Pure scoring functions for bakeoff evaluation (SC-005).

Given a ranked list of retrieved doc_ids for a query and a single gold doc_id,
compute recall@k, MRR, and NDCG@k (binary-relevance, single-gold) for each k
in `k_values`. Aggregation across queries is a simple arithmetic mean. No
external deps — kept here for easy unit test.
"""
from __future__ import annotations

import math
from typing import Iterable


def score_query(
    ranked_doc_ids: list[str],
    gold_doc_id: str,
    k_values: Iterable[int],
) -> dict[str, float]:
    """Score one query against one gold doc_id.

    Returns `{recall_at_K: 0|1, ndcg_at_K: float, ..., mrr: float}`. MRR uses
    1/rank of the first gold hit in the ranked list (unbounded — callers
    should slice to top-K before scoring if they want bounded MRR). NDCG@K
    is the single-relevant-item case: IDCG = 1/log2(2) = 1, so
    NDCG@K = 1/log2(rank+1) when gold appears at 1-indexed `rank <= K`,
    else 0. Closes #8.
    """
    result: dict[str, float] = {}
    # Find 1-indexed rank of gold once; reuse for recall, ndcg, mrr.
    gold_rank: int | None = None
    for rank, did in enumerate(ranked_doc_ids, start=1):
        if did == gold_doc_id:
            gold_rank = rank
            break
    for k in k_values:
        hit = gold_rank is not None and gold_rank <= k
        result[f"recall_at_{k}"] = 1 if hit else 0
        result[f"ndcg_at_{k}"] = (1.0 / math.log2(gold_rank + 1)) if hit else 0.0
    result["mrr"] = (1.0 / gold_rank) if gold_rank is not None else 0.0
    return result


def aggregate_scores(per_query: list[dict[str, float]]) -> dict[str, float]:
    """Arithmetic mean of each metric across all queries. Empty input -> {}."""
    if not per_query:
        return {}
    n = len(per_query)
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / n for k in keys}
