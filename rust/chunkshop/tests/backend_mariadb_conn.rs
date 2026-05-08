//! BackendConn integration tests for MariadbBackend.
//!
//! Skips if `CHUNKSHOP_TEST_DSN_MARIADB` is unset (matches the rest of the
//! integration test suite's skip-if-no-DSN pattern).

use chunkshop::backends::{BackendConn, MariadbBackend};
use sqlx::mysql::MySqlPoolOptions;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_pool_init_and_min_version() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    // Calling connect a second time is idempotent.
    backend.connect().await?;
    Ok(())
}

#[tokio::test]
async fn acquire_create_lock_and_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    let pool = MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&std::env::var(DSN_ENV).unwrap())
        .await?;

    let mut tx = pool.begin().await?;
    backend
        .acquire_create_lock(&mut tx, "chunkshop_r2_test")
        .await?;

    sqlx::query("CREATE DATABASE IF NOT EXISTS `chunkshop_r2_test`")
        .execute(&mut *tx)
        .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert!(!exists, "synthetic should not exist yet");

    sqlx::query(
        "CREATE TABLE `chunkshop_r2_test`.`synthetic` \
         (id VARCHAR(255) PRIMARY KEY, embedding VECTOR(8))",
    )
    .execute(&mut *tx)
    .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert!(exists, "synthetic should exist after CREATE TABLE");

    let dim = backend
        .embedding_dim(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert_eq!(dim, Some(8));

    sqlx::query("DROP DATABASE IF EXISTS `chunkshop_r2_test`")
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
