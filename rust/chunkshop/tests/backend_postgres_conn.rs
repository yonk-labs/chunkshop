//! BackendConn integration tests for PostgresBackend.
//!
//! Skips if `CHUNKSHOP_TEST_DSN` is unset (matches the rest of the integration
//! test suite's skip-if-no-DSN pattern).

use chunkshop::backends::{BackendConn, PostgresBackend};
use sqlx::postgres::PgPoolOptions;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_pool_init() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    // Calling connect a second time is idempotent (pool already initialized).
    backend.connect().await?;
    Ok(())
}

#[tokio::test]
async fn acquire_create_lock_and_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&std::env::var(DSN_ENV).unwrap())
        .await?;

    let mut tx = pool.begin().await?;
    backend
        .acquire_create_lock(&mut tx, "chunkshop_r1_test")
        .await?;

    sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
        .execute(&mut *tx)
        .await?;
    sqlx::query(r#"CREATE SCHEMA IF NOT EXISTS "chunkshop_r1_test""#)
        .execute(&mut *tx)
        .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert!(!exists);

    sqlx::query(
        r#"CREATE TABLE "chunkshop_r1_test"."synthetic" (id text PRIMARY KEY, embedding vector(8))"#,
    )
    .execute(&mut *tx)
    .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert!(exists);

    let dim = backend
        .embedding_dim(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert_eq!(dim, Some(8));

    sqlx::query(r#"DROP SCHEMA "chunkshop_r1_test" CASCADE"#)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
