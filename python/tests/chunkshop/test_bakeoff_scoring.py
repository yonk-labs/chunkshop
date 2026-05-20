"""Pure scoring math for the bakeoff (SC-005).

recall@k = 1 if gold doc_id is in top-k; MRR = 1/rank of first gold hit in
top-5, 0 if absent. Aggregates mean across queries.
"""
from __future__ import annotations

from chunkshop.bakeoff.score import aggregate_scores, score_query


def test_score_gold_at_rank_1():
    s = score_query(ranked_doc_ids=["d1", "d2", "d3"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["recall_at_1"] == 1
    assert s["recall_at_3"] == 1
    assert s["recall_at_5"] == 1
    assert s["mrr"] == 1.0


def test_score_gold_at_rank_3():
    s = score_query(ranked_doc_ids=["d2", "d3", "d1", "d4", "d5"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["recall_at_1"] == 0
    assert s["recall_at_3"] == 1
    assert s["recall_at_5"] == 1
    assert abs(s["mrr"] - 1 / 3) < 1e-9


def test_score_gold_absent():
    s = score_query(ranked_doc_ids=["d9", "d8", "d7"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert all(s[f"recall_at_{k}"] == 0 for k in [1, 3, 5])
    assert s["mrr"] == 0.0


def test_score_empty_top_k():
    s = score_query(ranked_doc_ids=[], gold_doc_id="d1", k_values=[1, 3, 5])
    assert all(s[f"recall_at_{k}"] == 0 for k in [1, 3, 5])
    assert s["mrr"] == 0.0


def test_aggregate_mean_across_queries():
    per_query = [
        {"recall_at_1": 1, "recall_at_3": 1, "mrr": 1.0},
        {"recall_at_1": 0, "recall_at_3": 1, "mrr": 0.5},
        {"recall_at_1": 0, "recall_at_3": 0, "mrr": 0.0},
    ]
    agg = aggregate_scores(per_query)
    assert abs(agg["recall_at_1"] - 1 / 3) < 1e-9
    assert abs(agg["recall_at_3"] - 2 / 3) < 1e-9
    assert abs(agg["mrr"] - 0.5) < 1e-9


# --- NDCG@k (issue #8) --------------------------------------------------------
# Single-relevant-item NDCG: IDCG = 1/log2(2) = 1, so NDCG@k = 1/log2(rank+1)
# for gold at rank r (1-indexed) when r <= k, else 0.
import math


def test_ndcg_gold_at_rank_1_is_one():
    s = score_query(ranked_doc_ids=["d1", "d2", "d3"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["ndcg_at_1"] == 1.0
    assert s["ndcg_at_3"] == 1.0
    assert s["ndcg_at_5"] == 1.0


def test_ndcg_gold_at_rank_3():
    s = score_query(ranked_doc_ids=["d2", "d3", "d1", "d4", "d5"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["ndcg_at_1"] == 0.0                                 # gold not in top-1
    assert abs(s["ndcg_at_3"] - 1 / math.log2(4)) < 1e-9         # = 0.5
    assert abs(s["ndcg_at_5"] - 1 / math.log2(4)) < 1e-9


def test_ndcg_gold_at_rank_5():
    s = score_query(ranked_doc_ids=["a", "b", "c", "d", "d1"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["ndcg_at_1"] == 0.0
    assert s["ndcg_at_3"] == 0.0
    assert abs(s["ndcg_at_5"] - 1 / math.log2(6)) < 1e-9         # ~= 0.3869


def test_ndcg_gold_absent():
    s = score_query(ranked_doc_ids=["d9", "d8", "d7"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["ndcg_at_1"] == 0.0
    assert s["ndcg_at_3"] == 0.0
    assert s["ndcg_at_5"] == 0.0


def test_ndcg_empty_top_k():
    s = score_query(ranked_doc_ids=[], gold_doc_id="d1", k_values=[1, 3, 5])
    assert all(s[f"ndcg_at_{k}"] == 0.0 for k in [1, 3, 5])


def test_aggregate_includes_ndcg():
    per_query = [
        {"recall_at_1": 1, "mrr": 1.0, "ndcg_at_1": 1.0, "ndcg_at_3": 1.0},
        {"recall_at_1": 0, "mrr": 0.5, "ndcg_at_1": 0.0, "ndcg_at_3": 1 / math.log2(3)},
    ]
    agg = aggregate_scores(per_query)
    assert abs(agg["ndcg_at_1"] - 0.5) < 1e-9
    assert abs(agg["ndcg_at_3"] - (1.0 + 1 / math.log2(3)) / 2) < 1e-9
