//! Sanity integration test for MariadbSink::create_table.

use chunkshop::backends::MariadbBackend;
use chunkshop::config::MariadbTargetConfig;
use chunkshop::sinks::{MariadbSink, Sink};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

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

    let cfg = MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_r2_sink".to_string(),
        table: "ct".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("r2-test".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    };
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    let sink = MariadbSink::new(cfg, backend, 8);
    sink.create_table().await?;

    let pool = sink.pool().await?;
    sqlx::query("DROP DATABASE IF EXISTS `chunkshop_r2_sink`")
        .execute(pool)
        .await?;
    Ok(())
}
