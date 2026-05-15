//! R3-SC-010: delete_document removes from BOTH tables, scoped to source_tag
//! when set. Mirrors Python's test_sc017_*.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::SqliteSink;
use chunkshop::sinks::Sink;
use serde_json::json;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str, source_tag: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false, hnsw: false,
        mode: mode.into(),
        source_tag: Some(source_tag.into()),
        promote_metadata: vec![], force_overwrite: false, delete_orphans: false,
    }
}

fn chunks(doc_id: &str, n: usize) -> Vec<Chunk> {
    (0..n).map(|i| Chunk {
        doc_id: doc_id.into(), seq_num: i,
        original_content: format!("c{i}"), embedded_content: format!("c{i}"),
        metadata: json!({}),
    }).collect()
}

fn embs(n: usize, dim: usize) -> Vec<Vec<f32>> {
    (0..n).map(|_| vec![0.5_f32; dim]).collect()
}

#[tokio::test]
async fn delete_document_removes_from_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DD_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("dd.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();

    sink.write_document("d1", &chunks("d1", 3), &embs(3, 4), &vec![vec![]; 3]).await.unwrap();
    sink.write_document("d2", &chunks("d2", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();

    let n = sink.delete_document("d1").await.unwrap();
    assert_eq!(n, 3);

    let b2 = SQLiteBackend::new(env);
    let conn = b2.connect().await.unwrap();
    let g = conn.lock().await;
    let n_main: i64 = g.query_row("SELECT COUNT(*) FROM chunks", [], |r: &rusqlite::Row| r.get(0)).unwrap();
    let n_vec: i64 = g.query_row("SELECT COUNT(*) FROM chunks_vec", [], |r: &rusqlite::Row| r.get(0)).unwrap();
    assert_eq!(n_main, 2);
    assert_eq!(n_vec, 2);
}

#[tokio::test]
async fn delete_document_respects_source_tag_scope() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DDS_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("dds.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink1 = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink1.create_table().await.unwrap();
    sink1.write_document("d1", &chunks("d1", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();

    // Different source_tag — must not delete t1's rows
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "create_if_missing", "t2"), b2, 4);
    sink2.create_table().await.unwrap();
    let n = sink2.delete_document("d1").await.unwrap();
    assert_eq!(n, 0);
}

#[tokio::test]
async fn count_docs_distinct() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CD_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("cd.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();
    sink.write_document("d1", &chunks("d1", 3), &embs(3, 4), &vec![vec![]; 3]).await.unwrap();
    sink.write_document("d2", &chunks("d2", 1), &embs(1, 4), &vec![vec![]; 1]).await.unwrap();
    sink.write_document("d1", &chunks("d1", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();
    assert_eq!(sink.count_docs().await.unwrap(), 2);
}
