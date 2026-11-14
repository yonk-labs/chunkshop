//! RSD-02 regression: with `runtime.doc_limit`, only sessions the runner
//! actually wrote may get the staging watermark. Sessions skipped by the limit
//! must stay eligible for the next run (and each session is processed once).
//!
//! Skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

#![cfg(feature = "memory")]

use std::path::Path;

use chunkshop::config::{load_config, SourceConfig, TargetConfig};
use chunkshop::memory::{ensure_staging_table, stage_events, StagedEvent};
use chunkshop::run_cell;
use sqlx::postgres::PgPoolOptions;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";
const STG: &str = "chunkshop_review_p1s_stg";
const DB: &str = "chunkshop_review_p1s_dl";

fn ev(session_id: &str, content: &str) -> StagedEvent {
    StagedEvent {
        session_id: session_id.into(),
        seq: Some(1),
        role: Some("user".into()),
        content: content.into(),
        ..Default::default()
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn doc_limit_leaves_unwritten_sessions_eligible() -> anyhow::Result<()> {
    let Ok(dsn) = std::env::var(DSN_ENV) else {
        eprintln!("skipping: {DSN_ENV} not set");
        return Ok(());
    };
    unsafe {
        std::env::set_var("CHUNKSHOP_MEMORY_DSN", &dsn);
    }
    let pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    for s in [STG, DB] {
        sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{s}" CASCADE"#))
            .execute(&pool)
            .await?;
    }
    ensure_staging_table(&pool, STG, "chunkshop_staging").await?;
    stage_events(
        &pool,
        STG,
        "chunkshop_staging",
        &[
            ev("s1", "We use Redis for the job queue."),
            ev("s2", "We migrated the queue to Postgres."),
            ev("s3", "Deploys happen on Tuesdays."),
        ],
    )
    .await?;
    sqlx::query(&format!(
        r#"UPDATE "{STG}".chunkshop_staging SET staged_at = now() - interval '2 hours'"#
    ))
    .execute(&pool)
    .await?;

    let marked = || async {
        sqlx::query_scalar::<_, String>(&format!(
            r#"SELECT DISTINCT session_id FROM "{STG}".chunkshop_staging
               WHERE coalesce(consumed->>'consolidated','') <> '' ORDER BY 1"#
        ))
        .fetch_all(&pool)
        .await
    };

    let mut total_docs = 0;
    let mut marked_after = vec![];
    for _ in 0..4 {
        let mut cfg = load_config(Path::new("configs/memory/consolidate.yaml"))?;
        if let TargetConfig::Postgres(t) = &mut cfg.target {
            t.database_name = DB.into();
        }
        if let SourceConfig::SessionStaging(s) = &mut cfg.source {
            s.min_age_seconds = 0;
            s.staging_schema = STG.into();
        }
        cfg.runtime.doc_limit = Some(1);
        total_docs += run_cell(cfg).await?.docs_processed;
        marked_after.push(marked().await?);
    }
    let episodes: i64 = sqlx::query_scalar(&format!(
        r#"SELECT count(DISTINCT doc_id) FROM "{DB}".memory WHERE kind = 'episode'"#
    ))
    .fetch_one(&pool)
    .await?;
    for s in [STG, DB] {
        sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{s}" CASCADE"#))
            .execute(&pool)
            .await?;
    }
    let counts: Vec<usize> = marked_after.iter().map(|m| m.len()).collect();
    assert_eq!(counts, vec![1, 2, 3, 3], "sessions marked after each run: {marked_after:?}");
    assert_eq!(total_docs, 3, "each session processed exactly once across runs");
    assert_eq!(episodes, 3, "one episode per session written");
    Ok(())
}
