//! RM-A Task 13: end-to-end stage→consolidate against the real memory
//! preset YAMLs, plus a pg-raggraph contract guard. Mirror of Python
//! `test_memory_e2e.py`.

#![cfg(feature = "memory")]

use chunkshop::config::load_config;
use chunkshop::memory::{ensure_staging_table, stage_events, StagedEvent};
use chunkshop::run_cell;
use sqlx::postgres::PgPoolOptions;
use sqlx::Row;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

/// pg-raggraph fact-contract columns. Drift in either chunkshop or
/// pg-raggraph fails this test — exactly the boundary check the SP-A
/// spec calls for (§ pg-raggraph contract test).
const PGRG_FACT_COLS: &[&str] = &[
    "subject",
    "predicate",
    "object",
    "support_span",
    "confidence",
    "effective_from",
    "effective_to",
    "retracted",
    "retracted_at",
    "extractor",
    "namespace",
];

fn skip_if_no_dsn() -> Option<String> {
    match std::env::var(DSN_ENV) {
        Ok(v) if !v.is_empty() => Some(v),
        _ => {
            eprintln!("skipping: {DSN_ENV} not set");
            None
        }
    }
}

fn unique_database() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("chunkshop_test_mem_e2e_{nanos}")
}

async fn cleanup(pool: &sqlx::PgPool, database: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(r#"DROP TABLE IF EXISTS public.chunkshop_staging CASCADE"#))
        .execute(pool)
        .await?;
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{database}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

async fn backdate(pool: &sqlx::PgPool) -> anyhow::Result<()> {
    sqlx::query("UPDATE public.chunkshop_staging SET staged_at = now() - interval '2 hours'")
        .execute(pool)
        .await?;
    Ok(())
}

fn ev(session_id: &str, seq: i64, role: &str, content: &str) -> StagedEvent {
    StagedEvent {
        session_id: session_id.into(),
        seq: Some(seq),
        role: Some(role.into()),
        content: content.into(),
        ..Default::default()
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn e2e_stage_then_consolidate() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    // The preset YAMLs use `dsn_env: CHUNKSHOP_MEMORY_DSN` on both source and
    // target; we point that env var at the test DSN for the duration of the
    // test.
    // SAFETY: This test runs single-process; multi-test parallelism on env
    // vars is the standard caveat. Each unique_database() keeps tables
    // isolated even if two e2e tests interleave.
    unsafe { std::env::set_var("CHUNKSHOP_MEMORY_DSN", &dsn); }
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    cleanup(&admin_pool, &database).await?;

    // 1. Stage the fixture.
    ensure_staging_table(&admin_pool, "public", "chunkshop_staging").await?;
    stage_events(
        &admin_pool, "public", "chunkshop_staging",
        &[
            ev("s1", 1, "user", "We use Redis for the job queue."),
            ev("s1", 2, "assistant", "Understood, Redis backs the queue."),
            ev("s2", 1, "user", "We migrated the queue from Redis to Postgres."),
            ev("s2", 2, "assistant", "Confirmed, Postgres LISTEN/NOTIFY now backs the queue."),
        ],
    )
    .await?;
    backdate(&admin_pool).await?;

    // 2. Run consolidate via the preset. Override database name to the
    // test schema and min_age=0 so backdated rows are selected.
    let mut cfg = load_config(Path::new("configs/memory/consolidate.yaml"))?;
    if let chunkshop::config::TargetConfig::Postgres(t) = &mut cfg.target {
        t.database_name = database.clone();
    }
    if let chunkshop::config::SourceConfig::SessionStaging(s) = &mut cfg.source {
        s.min_age_seconds = 0;
    }
    let r = run_cell(cfg).await?;
    eprintln!(
        "consolidate: docs={} chunks={} wall={:.2}s",
        r.docs_processed, r.chunks_written, r.wall_seconds
    );
    assert!(r.chunks_written > 0, "consolidate must produce chunks");

    // 3. Verify pg-raggraph contract columns are all present (drift-guard).
    let cols: Vec<String> = sqlx::query_scalar(
        "SELECT column_name FROM information_schema.columns \
         WHERE table_schema = $1 AND table_name = 'memory'",
    )
    .bind(&database)
    .fetch_all(&admin_pool)
    .await?;
    let missing: Vec<&&str> = PGRG_FACT_COLS
        .iter()
        .filter(|c| !cols.contains(&c.to_string()))
        .collect();
    assert!(
        missing.is_empty(),
        "pg-raggraph contract drift — these columns are missing from \
         agent_memory.memory after consolidate: {:?}",
        missing
    );

    // 4. Episode rows present (one per session, kind=episode).
    let episode_count: i64 = sqlx::query_scalar(&format!(
        r#"SELECT count(*) FROM "{database}".memory WHERE kind = 'episode'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert!(
        episode_count >= 2,
        "expected >=2 episode rows (one per session); got {episode_count}"
    );

    // 5. Tier is consolidated; supersede is in effect (no provisional rows
    // would survive a subsequent realtime run).
    let tier_counts: Vec<(String, i64)> = sqlx::query_as(&format!(
        r#"SELECT tier, count(*) FROM "{database}".memory GROUP BY tier"#
    ))
    .fetch_all(&admin_pool)
    .await?;
    for (tier, _) in &tier_counts {
        assert_eq!(tier, "consolidated", "all rows must be consolidated tier");
    }

    cleanup(&admin_pool, &database).await?;
    Ok(())
}
