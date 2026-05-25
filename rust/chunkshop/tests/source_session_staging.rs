//! RM-A Task 5: SessionStagingSource integration tests against real PG.
//! The O1 test is the headline — proves the consolidate-mode WHERE is
//! session-level, not row-level (a late event triggers full-staging
//! rebuild, not erasure). Mirror of Python's `test_o1_*` resilience tests.

#![cfg(feature = "memory")]

use chunkshop::config::{SessionStagingMode, SessionStagingSourceConfig};
use chunkshop::memory::{ensure_staging_table, stage_event, stage_events, StagedEvent};
use chunkshop::sources::SessionStagingSource;
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
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("chunkshop_test_memory_src_{nanos}")
}

async fn cleanup(pool: &sqlx::PgPool, schema: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

async fn backdate(pool: &sqlx::PgPool, schema: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(
        r#"UPDATE "{schema}".staging SET staged_at = now() - interval '2 hours'"#
    ))
    .execute(pool)
    .await?;
    Ok(())
}

fn ev(session_id: &str, seq: i64, content: &str) -> StagedEvent {
    StagedEvent {
        session_id: session_id.into(),
        seq: Some(seq),
        role: Some("user".into()),
        content: content.into(),
        ..Default::default()
    }
}

fn make_cfg(dsn: &str, schema: &str, mode: SessionStagingMode) -> SessionStagingSourceConfig {
    SessionStagingSourceConfig {
        dsn: Some(dsn.to_string()),
        dsn_env: None,
        staging_table: "staging".into(),
        staging_schema: schema.into(),
        mode,
        min_age_seconds: 0,
        max_sessions: None,
    }
}

#[tokio::test]
async fn yields_one_document_per_session() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else {
        return Ok(());
    };
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    let schema = unique_schema();
    ensure_staging_table(&pool, &schema, "staging").await?;
    stage_events(
        &pool,
        &schema,
        "staging",
        &[
            ev("s1", 1, "s1 first"),
            ev("s1", 2, "s1 second"),
            ev("s2", 1, "s2 only"),
        ],
    )
    .await?;

    let src = SessionStagingSource::new(make_cfg(&dsn, &schema, SessionStagingMode::Realtime));
    let docs = src.iter_documents().await?;
    assert_eq!(docs.len(), 2, "expected one Document per session");

    let ids: Vec<&str> = docs.iter().map(|d| d.id.as_str()).collect();
    assert!(ids.contains(&"s1"));
    assert!(ids.contains(&"s2"));

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn events_ordered_by_seq() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else {
        return Ok(());
    };
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    let schema = unique_schema();
    ensure_staging_table(&pool, &schema, "staging").await?;
    // Stage out-of-order; source must sort by seq.
    stage_events(
        &pool,
        &schema,
        "staging",
        &[
            ev("s1", 3, "third"),
            ev("s1", 1, "first"),
            ev("s1", 2, "second"),
        ],
    )
    .await?;

    let src = SessionStagingSource::new(make_cfg(&dsn, &schema, SessionStagingMode::Realtime));
    let docs = src.iter_documents().await?;
    let s1 = docs.iter().find(|d| d.id == "s1").expect("s1 missing");
    // Content reconstruction is `[role] content` lines joined by `\n`,
    // ordered by seq.
    let lines: Vec<&str> = s1.content.lines().collect();
    assert_eq!(lines[0], "[user] first");
    assert_eq!(lines[1], "[user] second");
    assert_eq!(lines[2], "[user] third");

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn realtime_mode_advances_consumed_realtime() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else {
        return Ok(());
    };
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    let schema = unique_schema();
    ensure_staging_table(&pool, &schema, "staging").await?;
    stage_event(&pool, &schema, "staging", &ev("s1", 1, "hi")).await?;

    let src = SessionStagingSource::new(make_cfg(&dsn, &schema, SessionStagingMode::Realtime));
    let docs = src.iter_documents().await?;
    assert_eq!(docs.len(), 1);
    // O3: commit_processed() is what actually advances the watermark.
    // Without it (mid-loop crash equivalent), the watermark stays at the
    // prior value and the next run reselects everything. This is the
    // contract the runner relies on for crash-safety.
    src.commit_processed().await?;

    // After commit, the single row should now have `consumed.realtime` set.
    let row = sqlx::query(&format!(
        r#"SELECT consumed->>'realtime' AS wm FROM "{schema}".staging"#
    ))
    .fetch_one(&pool)
    .await?;
    let wm: Option<String> = row.try_get("wm")?;
    assert!(wm.is_some(), "consumed.realtime watermark must be advanced");

    // Re-running yields zero docs (watermark filters everything out).
    let docs2 = src.iter_documents().await?;
    src.commit_processed().await?;
    assert_eq!(
        docs2.len(),
        0,
        "re-run after watermark advance must yield nothing"
    );

    cleanup(&pool, &schema).await?;
    Ok(())
}

/// **O1 critical test**: a late event arriving after the session was
/// consolidated must trigger a FULL-staging rebuild (the source emits
/// ALL the session's events, not just the late one). This is the
/// non-negotiable invariant that prevents the data-loss bug fixed in
/// Python `49861dc`.
#[tokio::test]
async fn consolidate_mode_uses_session_level_where_for_late_events() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else {
        return Ok(());
    };
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    let schema = unique_schema();
    ensure_staging_table(&pool, &schema, "staging").await?;

    // First, stage the initial conversation.
    stage_events(
        &pool,
        &schema,
        "staging",
        &[ev("s1", 1, "Redis"), ev("s1", 2, "still Redis")],
    )
    .await?;
    backdate(&pool, &schema).await?;

    let src = SessionStagingSource::new(make_cfg(&dsn, &schema, SessionStagingMode::Consolidate));

    // Round 1: consolidate selects s1 with both events.
    let docs = src.iter_documents().await?;
    assert_eq!(docs.len(), 1);
    let s1 = &docs[0];
    assert!(s1.content.contains("Redis"));
    let evs1 = s1.metadata["_session_events"]
        .as_array()
        .expect("_session_events array");
    assert_eq!(evs1.len(), 2, "round-1 should emit both events");
    // Commit the round-1 watermark — simulates the runner's post-write
    // success path.
    src.commit_processed().await?;

    // Late event arrives AFTER consolidation. It has consumed='{}' but the
    // older events have consumed.consolidated set.
    stage_event(
        &pool,
        &schema,
        "staging",
        &ev("s1", 3, "switched to RabbitMQ"),
    )
    .await?;
    backdate(&pool, &schema).await?;

    // Round 2: O1 — session-level WHERE re-selects s1 because it has a
    // new event AND its newest age is now older than min_age (backdated).
    // The source MUST emit ALL 3 events, not just the late one.
    let docs2 = src.iter_documents().await?;
    assert_eq!(docs2.len(), 1, "s1 must be re-selected after late event");
    let s1b = &docs2[0];
    let evs2 = s1b.metadata["_session_events"]
        .as_array()
        .expect("_session_events array");
    assert_eq!(
        evs2.len(),
        3,
        "O1 violation: full-staging rebuild must emit all 3 events; got {} — \
         session-level WHERE is NOT in effect",
        evs2.len()
    );
    // Content must include the late event AND the earlier ones.
    assert!(s1b.content.contains("RabbitMQ"), "late event missing");
    assert!(
        s1b.content.contains("Redis"),
        "earlier events lost — O1 violation"
    );

    cleanup(&pool, &schema).await?;
    Ok(())
}

#[tokio::test]
async fn max_sessions_caps_yielded_sessions() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else {
        return Ok(());
    };
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    let schema = unique_schema();
    ensure_staging_table(&pool, &schema, "staging").await?;
    stage_events(
        &pool,
        &schema,
        "staging",
        &[ev("a", 1, "1"), ev("b", 1, "2"), ev("c", 1, "3")],
    )
    .await?;

    let mut cfg = make_cfg(&dsn, &schema, SessionStagingMode::Realtime);
    cfg.max_sessions = Some(2);
    let src = SessionStagingSource::new(cfg);
    let docs = src.iter_documents().await?;
    assert_eq!(docs.len(), 2, "max_sessions=2 caps output");

    cleanup(&pool, &schema).await?;
    Ok(())
}
