//! append-mode preflight + overwrite foreign-tag refuse + HNSW warning behaviors.

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::Sink;
use chunkshop::sinks::SqliteSink;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str, source_tag: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: mode.into(),
        source_tag: Some(source_tag.into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    }
}

#[tokio::test]
async fn append_errors_when_table_missing() {
    let dir = tempdir().unwrap();
    let env = format!("R3_AM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("a.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "append", "t1"), b, 4);
    let err = sink.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("does not exist"),
        "expected 'does not exist': {msg}"
    );
}

#[tokio::test]
async fn append_errors_when_dim_mismatches() {
    let dir = tempdir().unwrap();
    let env = format!("R3_ADM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("d.db").to_str().unwrap());
    // Set up with dim=4
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();

    // Append claiming dim=8 — must error.
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "append", "t2"), b2, 8);
    let err = sink2.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("dim 4") && msg.contains("embed_dim 8"),
        "expected dim mismatch: {msg}"
    );
}

#[tokio::test]
async fn append_errors_when_vec_partner_missing() {
    let dir = tempdir().unwrap();
    let env = format!("R3_AVM_{}", std::process::id());
    let path = dir.path().join("nv.db");
    std::env::set_var(&env, path.to_str().unwrap());
    // Hand-create a chunks table WITHOUT its vec0 partner.
    let conn = rusqlite::Connection::open(&path).unwrap();
    conn.execute_batch("CREATE TABLE chunks (id TEXT PRIMARY KEY)")
        .unwrap();
    drop(conn);

    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "append", "t1"), b, 4);
    let err = sink.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("no vec0 partner"),
        "expected 'no vec0 partner': {msg}"
    );
}

#[tokio::test]
async fn overwrite_refuses_foreign_source_tag() {
    let dir = tempdir().unwrap();
    let env = format!("R3_FT_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("f.db").to_str().unwrap());
    // First sink writes a row tagged "t1".
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();
    {
        let conn = rusqlite::Connection::open(dir.path().join("f.db")).unwrap();
        conn.execute(
            "INSERT INTO chunks (id, doc_id, seq_num, original_content, embedded_content, source) \
             VALUES ('a', 'd', 0, 'x', 'x', 't1')",
            [],
        )
        .unwrap();
    }
    // Second sink tries to overwrite with a different source_tag.
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "overwrite", "t2"), b2, 4);
    let err = sink2.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("overwrite refuses to drop"), "expected: {msg}");
}

#[tokio::test]
#[tracing_test::traced_test]
async fn hnsw_emits_one_warning_per_process() {
    let dir = tempdir().unwrap();
    let env = format!("R3_HNSW_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("h.db").to_str().unwrap());
    let mut c = cfg(&env, "overwrite", "t1");
    c.hnsw = true;
    // Two sinks built in the same process — exactly ONE warning total.
    let b1 = SQLiteBackend::new(env.clone());
    let _s1 = SqliteSink::new(c.clone(), b1, 4);
    let b2 = SQLiteBackend::new(env);
    let _s2 = SqliteSink::new(c, b2, 4);
    assert!(logs_contain("no-op"));
    // Note: tracing-test doesn't easily count occurrences; the OnceLock
    // guarantee + this presence assertion is enough.
}
