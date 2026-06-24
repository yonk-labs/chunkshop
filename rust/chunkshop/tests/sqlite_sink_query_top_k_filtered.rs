//! #75: query_top_k_filtered restricts top-k to rows matching ALL filter
//! predicates. SQLite over-fetches then post-filters (sqlite-vec MATCH can't
//! take a WHERE inside the vec scan). Covers the json_extract metadata path
//! and the json_each tags-overlap path.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::{Filters, Sink, SqliteSink};
use serde_json::json;
use tempfile::tempdir;

fn cfg(env: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    }
}

/// Two tenants (doc "a" / doc "b") in one table. A metadata-scoped query must
/// return only the requested tenant's rows — even though a "b" chunk is nearer
/// to the query than two of the "a" chunks (so an unfiltered/over-fetch query
/// would surface it).
#[tokio::test]
async fn metadata_filter_returns_only_matching_scope() {
    let dir = tempdir().unwrap();
    let env = format!("F_QTK_META_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("q.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env), b, 4);
    sink.create_table().await.unwrap();

    let mk = |doc: &str, seq: usize, tenant: &str| Chunk {
        doc_id: doc.into(),
        seq_num: seq,
        original_content: format!("{doc}{seq}"),
        embedded_content: format!("{doc}{seq}"),
        metadata: json!({ "tenant": tenant }),
    };
    let a: Vec<Chunk> = (0..3).map(|i| mk("a", i, "acme")).collect();
    let bdocs: Vec<Chunk> = (0..3).map(|i| mk("b", i, "globex")).collect();
    let a_embs = vec![
        vec![1.0, 0.0, 0.0, 0.0], // exact match
        vec![0.0, 0.0, 1.0, 0.0], // far
        vec![0.0, 0.0, 0.0, 1.0], // far
    ];
    let b_embs = vec![
        vec![0.95, 0.05, 0.0, 0.0], // nearer than a1/a2 — must be excluded
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
    ];
    sink.write_document("a", &a, &a_embs, &vec![vec![]; 3])
        .await
        .unwrap();
    sink.write_document("b", &bdocs, &b_embs, &vec![vec![]; 3])
        .await
        .unwrap();

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.metadata.insert("tenant".into(), json!("acme"));

    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await.unwrap();

    assert_eq!(results.len(), 3, "all three acme rows, no globex rows");
    assert!(
        results.iter().all(|r| r.0 == "a"),
        "every hit must be tenant=acme (doc a), got {results:?}"
    );
    assert_eq!(results[0].1, 0, "nearest acme chunk is seq 0 (exact match)");
    let dists: Vec<f64> = results.iter().map(|r| r.2).collect();
    let mut sorted = dists.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(dists, sorted, "non-decreasing distance");
}

/// tags-overlap: a query tagged ["secret"] matches a row carrying that tag and
/// excludes rows without it. Exercises the json_each overlap clause.
#[tokio::test]
async fn tags_filter_returns_only_tagged_rows() {
    let dir = tempdir().unwrap();
    let env = format!("F_QTK_TAGS_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("q.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env), b, 4);
    sink.create_table().await.unwrap();

    let chunks: Vec<Chunk> = (0..2)
        .map(|i| Chunk {
            doc_id: "d".into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect();
    let embs = vec![vec![1.0, 0.0, 0.0, 0.0], vec![0.99, 0.01, 0.0, 0.0]];
    // seq 0 tagged ["public"], seq 1 tagged ["secret"]. seq 0 is nearer.
    let tags = vec![vec!["public".to_string()], vec!["secret".to_string()]];
    sink.write_document("d", &chunks, &embs, &tags)
        .await
        .unwrap();

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.tags.push("SECRET".into()); // case-insensitive overlap

    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await.unwrap();

    assert_eq!(results.len(), 1, "only the secret-tagged row");
    assert_eq!(results[0].1, 1);
}

/// None / empty filter is identical to the unfiltered query_top_k.
#[tokio::test]
async fn none_filter_matches_unfiltered() {
    let dir = tempdir().unwrap();
    let env = format!("F_QTK_NONE_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("q.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env), b, 4);
    sink.create_table().await.unwrap();

    let chunks: Vec<Chunk> = (0..3)
        .map(|i| Chunk {
            doc_id: "d".into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect();
    let embs = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
    ];
    sink.write_document("d", &chunks, &embs, &vec![vec![]; 3])
        .await
        .unwrap();

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let plain = sink.query_top_k(&q, 2).await.unwrap();
    let filtered = sink.query_top_k_filtered(&q, 2, None).await.unwrap();
    assert_eq!(plain, filtered);
}
