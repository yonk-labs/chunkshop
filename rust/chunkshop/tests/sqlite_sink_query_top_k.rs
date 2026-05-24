//! R3-SC-004: query_top_k runs vec0 MATCH joined back to chunks, returns
//! (doc_id, seq_num, distance) ordered by ascending distance.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::{Sink, SqliteSink};
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
    }
}

#[tokio::test]
async fn query_top_k_returns_ordered_distance_tuples() {
    let dir = tempdir().unwrap();
    let env = format!("R3_QTK_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("q.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env), b, 4);
    sink.create_table().await.unwrap();

    let chunks: Vec<Chunk> = (0..5usize)
        .map(|i| Chunk {
            doc_id: "d1".into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect();
    let embs: Vec<Vec<f32>> = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.9, 0.1, 0.0, 0.0],
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
        vec![0.0, 0.0, 0.0, 1.0],
    ];
    sink.write_document("d1", &chunks, &embs, &vec![vec![]; 5])
        .await
        .unwrap();

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let results = sink.query_top_k(&q, 3).await.unwrap();
    assert_eq!(results.len(), 3);
    let dists: Vec<f64> = results.iter().map(|r| r.2).collect();
    let mut sorted = dists.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(dists, sorted, "non-decreasing distance");
    // Top-1 must be chunk 0 (exact vector match).
    assert_eq!(results[0].1, 0);
    assert_eq!(results[0].0, "d1");
}
