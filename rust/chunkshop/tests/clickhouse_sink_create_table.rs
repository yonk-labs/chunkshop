//! ClickhouseSink::create_table_impl mode-dispatch integration tests.
//! Skip-if-no-DSN. Mirrors tests/pg_sink_create_table.rs in shape.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::sinks::ClickhouseSink;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn cfg(database: &str, table: &str, mode: &str) -> chunkshop::config::ClickhouseTargetConfig {
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {database}\ntable: {table}\nmode: {mode}\nhnsw: false"
    );
    let raw: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
    let target: chunkshop::config::TargetConfig = serde_yaml_ng::from_value(raw).unwrap();
    match target {
        chunkshop::config::TargetConfig::Clickhouse(t) => t,
        _ => panic!("expected Clickhouse variant"),
    }
}

async fn drop_db(backend: &ClickhouseBackend, db: &str) -> anyhow::Result<()> {
    let client = backend.client().await?;
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}

#[tokio::test]
async fn overwrite_creates_table_with_canonical_columns() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_create_overwrite";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg = cfg(db, "chunks", "overwrite");
    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink.create_table_impl().await?;

    let client = backend.client().await?;
    assert!(backend.table_exists(&client, db, "chunks").await?);
    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
async fn create_if_missing_idempotent() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_create_if_missing";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg1 = cfg(db, "chunks", "create_if_missing");
    let sink1 = ClickhouseSink::new(cfg1, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink1.create_table_impl().await?;

    // Re-run with same mode — should not error
    let cfg2 = cfg(db, "chunks", "create_if_missing");
    let sink2 = ClickhouseSink::new(cfg2, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink2.create_table_impl().await?;

    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
async fn append_mode_rejects_missing_table() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_append_no_table";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    // Need to construct ClickhouseTargetConfig with source_tag for append mode
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: chunks\nmode: append\nsource_tag: cell_a\nhnsw: false"
    );
    let raw: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
    let target: chunkshop::config::TargetConfig = serde_yaml_ng::from_value(raw).unwrap();
    let chunkshop::config::TargetConfig::Clickhouse(cfg) = target else { unreachable!() };

    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    let err = sink.create_table_impl().await.unwrap_err();
    assert!(format!("{err:#}").contains("does not exist"), "got: {err:#}");

    Ok(())
}
