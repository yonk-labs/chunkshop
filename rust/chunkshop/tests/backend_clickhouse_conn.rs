//! Inherent connection-layer integration tests for ClickhouseBackend.
//! Skips if `CHUNKSHOP_TEST_DSN_CH` is unset (mirrors backend_postgres_conn.rs).

use chunkshop::backends::ClickhouseBackend;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_client_init() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    backend.connect().await?; // idempotent
    Ok(())
}

#[tokio::test]
async fn table_exists_and_embedding_dim_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;

    // Clean slate
    let db = "chunkshop_r4_intro_test";
    client
        .query(&format!("CREATE DATABASE IF NOT EXISTS `{db}`"))
        .execute()
        .await?;
    client
        .query(&format!("DROP TABLE IF EXISTS `{db}`.synthetic SYNC"))
        .execute()
        .await?;

    // Pre-create check
    let exists = backend.table_exists(&client, db, "synthetic").await?;
    assert!(!exists);

    // Create with an embedding column
    client
        .query(&format!(
            "CREATE TABLE `{db}`.synthetic (id String, embedding Array(Float32)) ENGINE = MergeTree() ORDER BY id"
        ))
        .execute()
        .await?;

    // Post-create check
    assert!(backend.table_exists(&client, db, "synthetic").await?);

    // Empty table -> None
    assert_eq!(backend.embedding_dim(&client, db, "synthetic").await?, None);

    // Insert a row of dim 8
    client
        .query(&format!(
            "INSERT INTO `{db}`.synthetic VALUES ('a', [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])"
        ))
        .execute()
        .await?;
    assert_eq!(
        backend.embedding_dim(&client, db, "synthetic").await?,
        Some(8)
    );

    // Cleanup
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}

#[tokio::test]
async fn with_create_lock_is_noop() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    backend.with_create_lock(&client, "any_key").await?;
    Ok(())
}
