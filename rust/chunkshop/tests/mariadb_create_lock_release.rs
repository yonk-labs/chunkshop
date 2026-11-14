//! PR-015 / RSD-04: the create-table GET_LOCK must be released, so a second
//! sink (separate pool) against the same database does not wait 30s.

use chunkshop::backends::MariadbBackend;
use chunkshop::config::MariadbTargetConfig;
use chunkshop::sinks::{MariadbSink, Sink};
use std::time::{Duration, Instant};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";
const DB: &str = "chunkshop_review_p1r_lock";

fn sink(table: &str) -> MariadbSink {
    let cfg = MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: DB.to_string(),
        table: table.to_string(),
        overwrite: false,
        hnsw: false,
        mode: "create_if_missing".to_string(),
        source_tag: Some("p1r".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    };
    MariadbSink::new(cfg, MariadbBackend::new(DSN_ENV.to_string()), 8)
}

#[tokio::test]
async fn second_pool_acquires_create_lock_promptly() -> anyhow::Result<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return Ok(());
    }
    let a = sink("t_a");
    let b = sink("t_b");
    a.create_table().await?;
    let start = Instant::now();
    let res = b.create_table().await;
    let elapsed = start.elapsed();

    let pool = a.pool().await?;
    sqlx::query(&format!("DROP DATABASE IF EXISTS `{DB}`"))
        .execute(pool)
        .await?;

    res?;
    assert!(
        elapsed < Duration::from_secs(10),
        "second create_table took {elapsed:?}; GET_LOCK was not released"
    );
    Ok(())
}
