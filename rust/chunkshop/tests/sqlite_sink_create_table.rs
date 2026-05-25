//! Integration tests: SqliteSink::create_table per mode. Asserts that BOTH
//! the chunks table AND the {table}_vec virtual table are created (R3-SC-002).

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::Sink;
use chunkshop::sinks::SqliteSink;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: mode.into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    }
}

async fn assert_both_tables_exist(b: &SQLiteBackend) {
    let conn = b.connect().await.unwrap();
    assert!(
        b.table_exists(&conn, "ignored", "chunks").await.unwrap(),
        "chunks"
    );
    assert!(
        b.table_exists(&conn, "ignored", "chunks_vec")
            .await
            .unwrap(),
        "chunks_vec"
    );
}

#[tokio::test]
async fn overwrite_creates_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_OWT_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("ow.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite"), b, 4);
    sink.create_table().await.expect("create_table");
    let b2 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b2).await;
}

#[tokio::test]
async fn overwrite_drops_existing_table_and_recreates() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DROP_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("d.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite"), b, 4);
    sink.create_table().await.expect("first");
    // Re-create — should not error, drop+recreate is the contract.
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "overwrite"), b2, 4);
    sink2.create_table().await.expect("second");
    let b3 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b3).await;
}

#[tokio::test]
async fn create_if_missing_creates_when_absent() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CIM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("c.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "create_if_missing"), b, 4);
    sink.create_table().await.expect("create");
    let b2 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b2).await;
}

#[tokio::test]
async fn create_if_missing_is_idempotent() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CIM2_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("c.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "create_if_missing"), b, 4);
    sink.create_table().await.expect("first");
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "create_if_missing"), b2, 4);
    sink2.create_table().await.expect("second");
}
