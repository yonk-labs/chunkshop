//! RM-A Task 4: integration tests for the agent-memory staging API.
//! Skips if `CHUNKSHOP_TEST_DSN` is unset; self-cleaning via per-test
//! schema name. Mirrors Python `test_memory_staging.py`.

#![cfg(feature = "memory")]

use chunkshop::memory::{
    derive_event_id, ensure_staging_table, prune_staging, stage_event, stage_events, StagedEvent,
};
use sqlx::postgres::PgPoolOptions;
use sqlx::Row;
use std::time::{SystemTime, UNIX_EPOCH};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<String> {
    match std::env::var(DSN_ENV) {
        Ok(v) if !v.is_empty() => Some(v),
        _ => {
            eprintln!("skipping: {DSN_ENV} not set");
            None
        }
    }
}

fn unique_schema() -> String {
    // Per-test schema name keeps tests isolated and self-cleaning.
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("chunkshop_test_memory_stage_{nanos}")
}

async fn cleanup(pool: &sqlx::PgPool, schema: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

#[tokio::test]
async fn ensure_creates_table_with_indices() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let schema = unique_schema();
    let table = "staging";
    ensure_staging_table(&pool, &schema, table).await?;

    // Idempotent: a second call must not error.
    ensure_staging_table(&pool, &schema, table).await?;

    // Two indices land on the table.
    let row = sqlx::query(
        "SELECT count(*) AS n FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
    )
    .bind(&schema)
    .bind(table)
    .fetch_one(&pool)
    .await?;
    let n: i64 = row.try_get("n")?;
    // Postgres creates one index for the PRIMARY KEY plus our two explicit
    // CREATE INDEX statements → 3.
    assert_eq!(n, 3, "expected 1 PK index + 2 explicit indices");

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn stage_event_idempotent_on_event_id() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let schema = unique_schema();
    let table = "staging";
    ensure_staging_table(&pool, &schema, table).await?;

    let ev = StagedEvent {
        session_id: "s1".into(),
        seq: Some(1),
        role: Some("user".into()),
        content: "hello".into(),
        ..Default::default()
    };
    let id1 = stage_event(&pool, &schema, table, &ev).await?;
    // Re-stage identical event: ON CONFLICT DO NOTHING — still one row,
    // same id returned.
    let id2 = stage_event(&pool, &schema, table, &ev).await?;
    assert_eq!(id1, id2);
    let n: i64 = sqlx::query(&format!(r#"SELECT count(*) FROM "{schema}".staging"#))
        .fetch_one(&pool)
        .await?
        .try_get(0)?;
    assert_eq!(n, 1, "double-stage same event must yield exactly one row");

    // Derived id matches the cross-language hash exactly.
    assert_eq!(id1, derive_event_id("s1", Some(1), None, "hello"));

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn stage_events_bulk_inserts_distinct_ids() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let schema = unique_schema();
    let table = "staging";
    ensure_staging_table(&pool, &schema, table).await?;

    let events: Vec<StagedEvent> = (0..3)
        .map(|i| StagedEvent {
            session_id: "s1".into(),
            seq: Some(i as i64),
            role: Some("user".into()),
            content: format!("turn-{i}"),
            ..Default::default()
        })
        .collect();
    let n = stage_events(&pool, &schema, table, &events).await?;
    assert_eq!(n, 3);

    let row = sqlx::query(&format!(r#"SELECT count(*) FROM "{schema}".staging"#))
        .fetch_one(&pool)
        .await?;
    let stored: i64 = row.try_get(0)?;
    assert_eq!(stored, 3, "3 distinct events must produce 3 rows");

    // Re-staging the same events is a no-op (still 3 rows).
    stage_events(&pool, &schema, table, &events).await?;
    let row2 = sqlx::query(&format!(r#"SELECT count(*) FROM "{schema}".staging"#))
        .fetch_one(&pool)
        .await?;
    let stored2: i64 = row2.try_get(0)?;
    assert_eq!(stored2, 3, "re-staging duplicates must be a no-op");

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn prune_only_drops_consolidated_older_than() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let schema = unique_schema();
    let table = "staging";
    ensure_staging_table(&pool, &schema, table).await?;

    // Stage two events; mark one as consolidated.
    let ev_a = StagedEvent {
        session_id: "s1".into(),
        seq: Some(1),
        role: Some("user".into()),
        content: "consolidated event".into(),
        ..Default::default()
    };
    let ev_b = StagedEvent {
        session_id: "s2".into(),
        seq: Some(1),
        role: Some("user".into()),
        content: "still-realtime event".into(),
        ..Default::default()
    };
    let id_a = stage_event(&pool, &schema, table, &ev_a).await?;
    stage_event(&pool, &schema, table, &ev_b).await?;

    // Backdate both rows so prune's strict-LT predicate selects them, and
    // mark only ev_a as consolidated.
    sqlx::query(&format!(
        r#"UPDATE "{schema}".staging SET staged_at = now() - interval '2 hours'"#
    ))
    .execute(&pool)
    .await?;
    sqlx::query(&format!(
        r#"UPDATE "{schema}".staging
           SET consumed = consumed || '{{"consolidated": "2026-01-01T00:00:00Z"}}'::jsonb
           WHERE event_id = $1"#
    ))
    .bind(&id_a)
    .execute(&pool)
    .await?;

    // Default behaviour: only_consolidated=true → drops ev_a only.
    let dropped = prune_staging(&pool, &schema, table, "2026-12-31T00:00:00Z", true).await?;
    assert_eq!(dropped, 1, "only_consolidated=true must drop only consolidated rows");

    let remaining: i64 = sqlx::query(&format!(r#"SELECT count(*) FROM "{schema}".staging"#))
        .fetch_one(&pool)
        .await?
        .try_get(0)?;
    assert_eq!(remaining, 1, "ev_b (not yet consolidated) must survive prune");

    // only_consolidated=false drops the rest unconditionally.
    let dropped_all =
        prune_staging(&pool, &schema, table, "2026-12-31T00:00:00Z", false).await?;
    assert_eq!(dropped_all, 1);
    let n: i64 = sqlx::query(&format!(r#"SELECT count(*) FROM "{schema}".staging"#))
        .fetch_one(&pool)
        .await?
        .try_get(0)?;
    assert_eq!(n, 0);

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn ensure_staging_table_rejects_unsafe_identifiers() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let pool = PgPoolOptions::new().max_connections(1).connect(&dsn).await?;
    // Don't even need a real schema for the rejection — validation runs first.
    let bad_table = "a\"; DROP TABLE other; --";
    let res = ensure_staging_table(&pool, "public", bad_table).await;
    assert!(res.is_err(), "unsafe identifier must be rejected pre-SQL");
    Ok(())
}
