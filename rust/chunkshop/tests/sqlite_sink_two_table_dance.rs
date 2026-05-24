//! R3-SC-003: write_document upserts main table AND DELETE+INSERTs into vec0
//! in the SAME transaction. Re-writing the same doc_id replaces vec rows,
//! does NOT duplicate them or fail with UNIQUE-constraint errors.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::Sink;
use chunkshop::sinks::SqliteSink;
use serde_json::json;
use tempfile::tempdir;

fn cfg(dsn_env: &str, delete_orphans: bool) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans,
    }
}

fn chunk(doc_id: &str, n: usize) -> Vec<Chunk> {
    (0..n)
        .map(|i| Chunk {
            doc_id: doc_id.into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({"k": i}),
        })
        .collect()
}

fn embeddings(n: usize, dim: usize) -> Vec<Vec<f32>> {
    (0..n)
        .map(|i| {
            let mut v = vec![0.0_f32; dim];
            v[i % dim] = 1.0;
            v
        })
        .collect()
}

async fn count(b: &SQLiteBackend, sql: &str) -> i64 {
    let conn = b.connect().await.unwrap();
    let g = conn.lock().await;
    g.query_row(sql, [], |r| r.get(0)).unwrap()
}

#[tokio::test]
async fn write_creates_3_rows_in_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_W3_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("w.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, false), b, 4);
    sink.create_table().await.unwrap();

    let chunks = chunk("d1", 3);
    let embs = embeddings(3, 4);
    let tags = vec![vec![]; 3];
    sink.write_document("d1", &chunks, &embs, &tags)
        .await
        .unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks").await, 3);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks_vec").await, 3);
}

#[tokio::test]
async fn rewriting_same_doc_replaces_vec_rows_no_duplicates() {
    let dir = tempdir().unwrap();
    let env = format!("R3_RW_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("rw.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, false), b, 4);
    sink.create_table().await.unwrap();

    let chunks = chunk("d1", 3);
    let embs = embeddings(3, 4);
    let tags = vec![vec![]; 3];
    sink.write_document("d1", &chunks, &embs, &tags)
        .await
        .unwrap();
    sink.write_document("d1", &chunks, &embs, &tags)
        .await
        .unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks").await, 3, "main");
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks_vec").await, 3, "vec");
}

#[tokio::test]
async fn delete_orphans_shrinks_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DO_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("do.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, true), b, 4);
    sink.create_table().await.unwrap();

    // First: 5 chunks
    let chunks = chunk("d1", 5);
    let embs = embeddings(5, 4);
    let tags = vec![vec![]; 5];
    sink.write_document("d1", &chunks, &embs, &tags)
        .await
        .unwrap();

    // Re-write with only 2 chunks — orphans 2..4 should be deleted from both
    let chunks2 = chunk("d1", 2);
    let embs2 = embeddings(2, 4);
    let tags2 = vec![vec![]; 2];
    sink.write_document("d1", &chunks2, &embs2, &tags2)
        .await
        .unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(
        count(&b, "SELECT COUNT(*) FROM chunks").await,
        2,
        "main shrunk"
    );
    assert_eq!(
        count(&b, "SELECT COUNT(*) FROM chunks_vec").await,
        2,
        "vec shrunk"
    );
}
