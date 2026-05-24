//! Integration tests for SQLiteBackend's connection methods.

use chunkshop::backends::SQLiteBackend;
use std::sync::Arc;
use tempfile::tempdir;

fn unique_env(name: &str) -> String {
    format!("CHUNKSHOP_R3_TEST_{name}_{}", std::process::id())
}

#[tokio::test]
async fn connect_opens_writable_db_with_wal() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("conn.db");
    let env = unique_env("connect");
    std::env::set_var(&env, path.to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let conn = b.connect().await.expect("connect");
    let g = conn.lock().await;
    let mode: String = g
        .query_row("PRAGMA journal_mode", [], |r| r.get(0))
        .expect("query journal_mode");
    assert_eq!(mode.to_lowercase(), "wal");
}

#[tokio::test]
async fn table_exists_distinguishes_present_absent() {
    let env = unique_env("texists");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch("CREATE TABLE present (x INT)").unwrap();
    }
    assert!(b.table_exists(&conn, "ignored", "present").await.unwrap());
    assert!(!b.table_exists(&conn, "ignored", "missing").await.unwrap());
}

#[tokio::test]
async fn table_exists_finds_virtual_tables() {
    let env = unique_env("vexists");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch(
            "CREATE VIRTUAL TABLE v USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[4])",
        )
        .unwrap();
    }
    assert!(b.table_exists(&conn, "ignored", "v").await.unwrap());
}

#[tokio::test]
async fn embedding_dim_reads_dim_from_vec_partner() {
    let env = unique_env("dim");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch(
            "CREATE TABLE chunks (id TEXT PRIMARY KEY); \
             CREATE VIRTUAL TABLE chunks_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[768])",
        )
        .unwrap();
    }
    let d = b.embedding_dim(&conn, "ignored", "chunks").await.unwrap();
    assert_eq!(d, Some(768));
    let d = b.embedding_dim(&conn, "ignored", "missing").await.unwrap();
    assert_eq!(d, None);
}

#[tokio::test]
async fn with_create_lock_is_a_noop_returning_ok() {
    let env = unique_env("lock");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    b.with_create_lock(&conn, "anykey").await.expect("noop");
    // Idempotent
    b.with_create_lock(&conn, "anykey").await.expect("noop");
}

#[tokio::test]
async fn arc_mutex_connection_is_shareable_across_tasks() {
    // Sanity check: Arc<Mutex<...>> wrapping is correct for tokio.
    let env = unique_env("share");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    let conn2: Arc<_> = conn.clone();
    let h = tokio::spawn(async move {
        let g = conn2.lock().await;
        g.execute_batch("CREATE TABLE t (x INT)").unwrap();
    });
    h.await.unwrap();
    let g = conn.lock().await;
    let n: i64 = g
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='t'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(n, 1);
}
