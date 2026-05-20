//! Pure scoring math. Mirrors `python/src/chunkshop/bakeoff/score.py`.
//! recall@k + NDCG@k for each k in `k_values` + MRR. Single-relevant-item
//! binary-relevance NDCG: IDCG = 1/log2(2) = 1, so NDCG@k = 1/log2(rank+1)
//! when gold appears at 1-indexed rank <= k, else 0.

use std::collections::BTreeMap;

/// Score one query against one gold doc_id. Returns
/// `{"recall_at_<k>": 0.0|1.0, "ndcg_at_<k>": float, ..., "mrr": float}`.
pub fn score_query(
    ranked_doc_ids: &[String],
    gold_doc_id: &str,
    k_values: &[usize],
) -> BTreeMap<String, f64> {
    let mut out: BTreeMap<String, f64> = BTreeMap::new();
    // Pre-compute 1-indexed rank of gold once; reuse for recall/ndcg/mrr.
    let gold_rank: Option<usize> = ranked_doc_ids
        .iter()
        .position(|d| d == gold_doc_id)
        .map(|i| i + 1);
    for &k in k_values {
        let hit = gold_rank.map(|r| r <= k).unwrap_or(false);
        out.insert(format!("recall_at_{k}"), if hit { 1.0 } else { 0.0 });
        let ndcg = if hit {
            1.0 / ((gold_rank.unwrap() + 1) as f64).log2()
        } else {
            0.0
        };
        out.insert(format!("ndcg_at_{k}"), ndcg);
    }
    let mrr = gold_rank.map_or(0.0, |r| 1.0 / (r as f64));
    out.insert("mrr".to_string(), mrr);
    out
}

/// Arithmetic mean of each metric across all queries.
/// Empty input → empty map (matches Python).
pub fn aggregate_scores(per_query: &[BTreeMap<String, f64>]) -> BTreeMap<String, f64> {
    let mut out: BTreeMap<String, f64> = BTreeMap::new();
    if per_query.is_empty() {
        return out;
    }
    let n = per_query.len() as f64;
    let keys: Vec<String> = per_query[0].keys().cloned().collect();
    for k in keys {
        let sum: f64 = per_query.iter().map(|q| *q.get(&k).unwrap_or(&0.0)).sum();
        out.insert(k, sum / n);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &str) -> String {
        v.to_string()
    }

    #[test]
    fn score_query_perfect_top1() {
        let ranked = vec![s("d1"), s("d2"), s("d3")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["recall_at_1"], 1.0);
        assert_eq!(scores["recall_at_3"], 1.0);
        assert_eq!(scores["recall_at_5"], 1.0);
        assert_eq!(scores["mrr"], 1.0);
    }

    #[test]
    fn score_query_hit_at_rank_2() {
        let ranked = vec![s("d99"), s("d1"), s("d2")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["recall_at_1"], 0.0);
        assert_eq!(scores["recall_at_3"], 1.0);
        assert_eq!(scores["mrr"], 0.5);
    }

    #[test]
    fn score_query_miss_returns_zero_mrr() {
        let ranked = vec![s("a"), s("b"), s("c")];
        let scores = score_query(&ranked, "z", &[1, 3]);
        assert_eq!(scores["recall_at_1"], 0.0);
        assert_eq!(scores["recall_at_3"], 0.0);
        assert_eq!(scores["mrr"], 0.0);
    }

    #[test]
    fn aggregate_averages_each_metric() {
        let q1 = score_query(&[s("d1")], "d1", &[1]);
        let q2 = score_query(&[s("d99")], "d1", &[1]);
        let agg = aggregate_scores(&[q1, q2]);
        assert_eq!(agg["recall_at_1"], 0.5);
        assert_eq!(agg["mrr"], 0.5);
    }

    #[test]
    fn aggregate_empty_returns_empty() {
        let agg = aggregate_scores(&[]);
        assert!(agg.is_empty());
    }

    // --- NDCG@k (issue #8 parity with Python) -------------------------------
    // IDCG=1 (single relevant), so NDCG@k = 1/log2(rank+1) when hit, else 0.

    #[test]
    fn ndcg_gold_at_rank_1_is_one() {
        let ranked = vec![s("d1"), s("d2"), s("d3")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["ndcg_at_1"], 1.0);
        assert_eq!(scores["ndcg_at_3"], 1.0);
        assert_eq!(scores["ndcg_at_5"], 1.0);
    }

    #[test]
    fn ndcg_gold_at_rank_3() {
        let ranked = vec![s("d2"), s("d3"), s("d1"), s("d4"), s("d5")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["ndcg_at_1"], 0.0);                       // out of top-1
        let expected = 1.0_f64 / 4.0_f64.log2();                    // = 0.5
        assert!((scores["ndcg_at_3"] - expected).abs() < 1e-9);
        assert!((scores["ndcg_at_5"] - expected).abs() < 1e-9);
    }

    #[test]
    fn ndcg_gold_at_rank_5() {
        let ranked = vec![s("a"), s("b"), s("c"), s("d"), s("d1")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["ndcg_at_1"], 0.0);
        assert_eq!(scores["ndcg_at_3"], 0.0);
        let expected = 1.0_f64 / 6.0_f64.log2();                    // ≈ 0.3869
        assert!((scores["ndcg_at_5"] - expected).abs() < 1e-9);
    }

    #[test]
    fn ndcg_gold_absent_is_zero() {
        let ranked = vec![s("a"), s("b"), s("c")];
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["ndcg_at_1"], 0.0);
        assert_eq!(scores["ndcg_at_3"], 0.0);
        assert_eq!(scores["ndcg_at_5"], 0.0);
    }

    #[test]
    fn ndcg_empty_ranked_is_zero() {
        let ranked: Vec<String> = Vec::new();
        let scores = score_query(&ranked, "d1", &[1, 3, 5]);
        assert_eq!(scores["ndcg_at_1"], 0.0);
        assert_eq!(scores["ndcg_at_3"], 0.0);
        assert_eq!(scores["ndcg_at_5"], 0.0);
    }
}
