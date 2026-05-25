//! RM-A Task 14: O1 + O3 operational invariants — integration guards.
//!
//! O1 (late-event rebuild) is verified by
//! `tests/source_session_staging.rs::consolidate_mode_uses_session_level_where_for_late_events`
//! — the session-level WHERE keeps that invariant from day 1.
//!
//! O3 (crash-safe per-doc commit + watermark deferral): test here. Mirror
//! of Python `test_o3_crash_mid_run_resumes_cleanly`.

#![cfg(feature = "memory")]

use chunkshop::config::{SessionStagingMode, SessionStagingSourceConfig};
use chunkshop::memory::{ensure_staging_table, stage_events, StagedEvent};
use chunkshop::sources::SessionStagingSource;
use sqlx::postgres::PgPoolOptions;
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
    format!("chunkshop_test_mem_resilience_{nanos}")
}

async fn cleanup(pool: &sqlx::PgPool, schema: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

fn ev(sid: &str, seq: i64, content: &str) -> StagedEvent {
    StagedEvent {
        session_id: sid.into(),
        seq: Some(seq),
        role: Some("user".into()),
        content: content.into(),
        ..Default::default()
    }
}

/// **O3 test**: simulates a "crash" by calling `iter_documents()` (which
/// stages a pending watermark advance internally) but **never** calling
/// `commit_processed()` — the moral equivalent of a mid-loop write
/// failure. The watermark must stay unadvanced, so the next run
/// reselects everything. Mirror of Python's generator-semantics
/// crash-resume invariant.
#[tokio::test]
async fn o3_uncommitted_run_does_not_advance_watermark() -> anyhow::Result<()> {
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
            ev("s1", 1, "first"),
            ev("s2", 1, "second"),
            ev("s3", 1, "third"),
        ],
    )
    .await?;

    let cfg = SessionStagingSourceConfig {
        dsn: Some(dsn.clone()),
        dsn_env: None,
        staging_table: "staging".into(),
        staging_schema: schema.clone(),
        mode: SessionStagingMode::Realtime,
        min_age_seconds: 0,
        max_sessions: None,
    };
    let src = SessionStagingSource::new(cfg.clone());

    // First "run" — iterate but DON'T commit (simulates the consumer
    // crashing mid-loop on the 3rd doc).
    let docs = src.iter_documents().await?;
    assert_eq!(docs.len(), 3, "first run should see all 3 sessions");
    // Imagine: the runner wrote docs 0 and 1, then write_document errored
    // on doc 2 → the loop exits with `?` → commit_processed() never runs.

    // Critical assertion: NO row's consumed.realtime should be set yet.
    let n_advanced: i64 = sqlx::query_scalar(&format!(
        r#"SELECT count(*) FROM "{schema}".staging
           WHERE consumed ? 'realtime'"#
    ))
    .fetch_one(&pool)
    .await?;
    assert_eq!(
        n_advanced, 0,
        "O3 violation: watermark advanced before commit_processed — \
         crashed mid-loop sessions would be lost on next run"
    );

    // Second "run" with a fresh source — must reselect ALL 3 sessions
    // because the watermark stayed unadvanced.
    let src2 = SessionStagingSource::new(cfg);
    let docs2 = src2.iter_documents().await?;
    assert_eq!(
        docs2.len(),
        3,
        "O3 resume: next run must reselect all sessions when prior run \
         didn't commit"
    );

    // After we DO call commit_processed on the second run, the watermark
    // advances and a third run yields zero.
    src2.commit_processed().await?;
    let src3 = SessionStagingSource::new(SessionStagingSourceConfig {
        dsn: Some(dsn.clone()),
        dsn_env: None,
        staging_table: "staging".into(),
        staging_schema: schema.clone(),
        mode: SessionStagingMode::Realtime,
        min_age_seconds: 0,
        max_sessions: None,
    });
    let docs3 = src3.iter_documents().await?;
    assert_eq!(docs3.len(), 0, "post-commit, next run sees nothing new");

    cleanup(&pool, &schema).await?;
    Ok(())
}

/// **O1 spot-check** (full proof lives in source_session_staging.rs).
/// Stages a late event after a committed consolidation and verifies the
/// session-level WHERE re-selects the entire session.
#[tokio::test]
async fn o1_late_event_after_commit_rebuilds_full_session() -> anyhow::Result<()> {
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
        &[ev("s1", 1, "v1"), ev("s1", 2, "v2")],
    )
    .await?;
    sqlx::query(&format!(
        r#"UPDATE "{schema}".staging SET staged_at = now() - interval '2 hours'"#
    ))
    .execute(&pool)
    .await?;

    let cfg = SessionStagingSourceConfig {
        dsn: Some(dsn.clone()),
        dsn_env: None,
        staging_table: "staging".into(),
        staging_schema: schema.clone(),
        mode: SessionStagingMode::Consolidate,
        min_age_seconds: 0,
        max_sessions: None,
    };
    let src = SessionStagingSource::new(cfg.clone());

    let r1 = src.iter_documents().await?;
    assert_eq!(r1.len(), 1, "first consolidate sees s1");
    src.commit_processed().await?;

    // Late event after consolidation completes.
    stage_events(&pool, &schema, "staging", &[ev("s1", 3, "late")]).await?;
    sqlx::query(&format!(
        r#"UPDATE "{schema}".staging SET staged_at = now() - interval '2 hours'"#
    ))
    .execute(&pool)
    .await?;

    // Round 2 — session-level WHERE must re-emit s1 with ALL 3 events.
    let src2 = SessionStagingSource::new(cfg);
    let r2 = src2.iter_documents().await?;
    assert_eq!(r2.len(), 1);
    let evs = r2[0].metadata["_session_events"].as_array().unwrap();
    assert_eq!(evs.len(), 3, "O1: full-staging rebuild emits all 3 events");

    cleanup(&pool, &schema).await?;
    Ok(())
}
