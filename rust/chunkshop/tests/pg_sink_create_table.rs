//! Sanity integration test for PgSink::create_table.

use chunkshop::backends::PostgresBackend;
use chunkshop::config::PostgresTargetConfig;
use chunkshop::sinks::{PgSink, Sink};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn create_table_overwrite_mode() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }

    let cfg = PostgresTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_r1_pgsink".to_string(),
        table: "ct".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("r1-test".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        memory: None,
        documents: None,
    };
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = PgSink::new(cfg, backend, 8);
    sink.create_table().await?;

    // Cleanup
    let pool = sink.pool().await?;
    sqlx::query(r#"DROP SCHEMA "chunkshop_r1_pgsink" CASCADE"#)
        .execute(pool)
        .await?;
    Ok(())
}
