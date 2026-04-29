//! Pure scoring math. Mirrors `python/src/chunkshop/bakeoff/score.py`.
//! recall@k for each k in `k_values` (1.0 if hit, 0.0 if miss) + MRR
//! (1/rank of first gold hit, 0.0 if absent).

use std::collections::BTreeMap;

/// Score one query against one gold doc_id. Returns
/// `{"recall_at_<k>": 0.0|1.0, ..., "mrr": float}`.
pub fn score_query(
    ranked_doc_ids: &[String],
    gold_doc_id: &str,
    k_values: &[usize],
) -> BTreeMap<String, f64> {
    let mut out: BTreeMap<String, f64> = BTreeMap::new();
    for &k in k_values {
        let hit = ranked_doc_ids
            .iter()
            .take(k)
            .any(|d| d == gold_doc_id);
        out.insert(format!("recall_at_{k}"), if hit { 1.0 } else { 0.0 });
    }
    let mut mrr = 0.0_f64;
    for (rank, did) in ranked_doc_ids.iter().enumerate() {
        if did == gold_doc_id {
            mrr = 1.0 / ((rank + 1) as f64);
            break;
        }
    }
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
}
