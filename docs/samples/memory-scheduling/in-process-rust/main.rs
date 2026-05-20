//! In-process memory scheduler for a Rust agent runtime.
//!
//! Drive the two memory cells from the same tokio runtime your agent
//! server uses. Mirror of the Python `in-process-python/run.py` sample.
//!
//! Run with:
//!     export CHUNKSHOP_MEMORY_DSN=postgresql://app:secret@localhost:5432/agent_memory
//!     cargo run --bin memory-scheduler-demo

use std::path::PathBuf;
use std::time::Duration;

use anyhow::Result;
use chunkshop::config::load_config;
use chunkshop::memory::{ensure_staging_table, stage_event, StagedEvent};
use chunkshop::run_cell;
use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use tokio::time;
use tracing::{error, info};

/// Adjust these paths for your deployment. The defaults work when the
/// demo runs from inside the chunkshop repo.
const REALTIME_CFG: &str = "rust/chunkshop/configs/memory/realtime.yaml";
const CONSOLIDATE_CFG: &str = "rust/chunkshop/configs/memory/consolidate.yaml";

/// Call this from your agent's message handler. Idempotent on
/// `event_id` (sha1 of session_id/seq/content). Synchronous wrt
/// the staging table — returns after the row is in.
pub async fn on_agent_turn(
    pool: &PgPool,
    session_id: &str,
    role: &str,
    content: &str,
    seq: i64,
) -> Result<()> {
    stage_event(
        pool,
        "public",
        "chunkshop_staging",
        &StagedEvent {
            session_id: session_id.to_string(),
            seq: Some(seq),
            role: Some(role.to_string()),
            content: content.to_string(),
            ..Default::default()
        },
    )
    .await?;
    Ok(())
}

/// Start the scheduler. Spawns two tokio tasks; returns immediately.
/// Bootstrap DDL is idempotent and runs every start.
pub async fn start(pool: &PgPool) -> Result<()> {
    ensure_staging_table(pool, "public", "chunkshop_staging").await?;
    tokio::spawn(run_cell_periodically(
        "realtime",
        REALTIME_CFG.into(),
        Duration::from_secs(60),
    ));
    tokio::spawn(run_cell_periodically(
        "consolidate",
        CONSOLIDATE_CFG.into(),
        Duration::from_secs(3600),
    ));
    Ok(())
}

async fn run_cell_periodically(name: &'static str, cfg_path: PathBuf, interval: Duration) {
    let mut ticker = time::interval(interval);
    // If the runtime stalls (debugger pause, GC pause, etc.) we skip
    // missed ticks rather than firing in a burst. The realtime cell
    // is idempotent — bursting wouldn't break it, just waste work.
    ticker.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
    loop {
        ticker.tick().await;
        match load_config(std::path::Path::new(&cfg_path)) {
            Ok(cfg) => match run_cell(cfg).await {
                Ok(r) => info!(
                    cell = name,
                    docs = r.docs_processed,
                    chunks = r.chunks_written,
                    wall = r.wall_seconds,
                    "cell done"
                ),
                Err(e) => error!(cell = name, error = ?e, "cell failed"),
            },
            Err(e) => error!(cell = name, error = ?e, "config load failed"),
        }
    }
}

// --- demo / smoke test ------------------------------------------------------

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    let dsn = std::env::var("CHUNKSHOP_MEMORY_DSN")?;
    let pool = PgPoolOptions::new()
        .max_connections(4)
        .connect(&dsn)
        .await?;

    // Stage a 5-turn demo session.
    for (seq, (role, content)) in [
        ("user", "We use Redis for the job queue."),
        ("assistant", "Understood — Redis backs the queue."),
        ("user", "Actually we migrated to Postgres last week."),
        ("assistant", "Noted. Switching mental model to Postgres LISTEN/NOTIFY."),
        ("user", "And we use pg_partman for the audit table partitions."),
    ]
    .iter()
    .enumerate()
    {
        on_agent_turn(&pool, "demo-session-1", role, content, (seq + 1) as i64).await?;
    }
    info!("staged 5 turns");

    // Start the scheduler. In a real app you'd let it run forever; for
    // the demo we wait long enough for one realtime tick to fire, then
    // print the result.
    start(&pool).await?;
    info!("waiting 65s for one realtime tick...");
    tokio::time::sleep(Duration::from_secs(65)).await;

    // Show the result.
    let rows: Vec<(String, String, i64)> = sqlx::query_as(
        "SELECT tier, kind, count(*) FROM agent_memory.memory \
         GROUP BY tier, kind ORDER BY tier, kind",
    )
    .fetch_all(&pool)
    .await?;
    for (tier, kind, n) in rows {
        info!(tier, kind, count = n, "agent_memory.memory");
    }
    Ok(())
}
