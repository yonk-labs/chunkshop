//! chunkshop-rs CLI entry point.
//!
//! Subcommands:
//!   `chunkshop-rs ingest`  — run a single cell end-to-end
//!   `chunkshop-rs bakeoff` — run a chunker × embedder matrix and emit a
//!                            leaderboard + recommended.yaml

use std::path::PathBuf;

use anyhow::{anyhow, Result};
use clap::{Parser, Subcommand};

use chunkshop::bakeoff::output::{write_recommended_yaml, write_report_md, write_results_json};
use chunkshop::bakeoff::BakeoffConfig;
use chunkshop::{load_config, run_bakeoff_with_base, run_cell};

#[derive(Parser)]
#[command(name = "chunkshop-rs", version, about = "Rust chunkshop ingest + bakeoff")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Run a single ingest cell from a YAML config.
    Ingest {
        #[arg(long)]
        config: PathBuf,
    },
    /// Run a chunker × embedder matrix bakeoff against a corpus.
    ///
    /// Same YAML schema as Python's `chunkshop bakeoff`. Outputs land under
    /// `output_dir` (default `skill-output/bakeoff/{name}/`):
    ///   results.json — raw scored data
    ///   report.md    — leaderboard + per-query detail + statistical-power note
    ///   recommended.yaml — runnable cell for the top combo
    Bakeoff {
        /// Path to the bakeoff YAML config.
        #[arg(long)]
        config: PathBuf,
        /// Postgres DSN (sets `target.dsn_env` before running).
        #[arg(long)]
        dsn: String,
        /// Bypass the >50-cell matrix confirmation prompt.
        #[arg(long)]
        yes: bool,
        /// Keep the bakeoff schema after run (default: drop CASCADE on exit).
        #[arg(long)]
        keep_schema: bool,
    },
}

async fn run_bakeoff_command(
    config: PathBuf,
    dsn: String,
    yes: bool,
    keep_schema: bool,
) -> Result<()> {
    let text = std::fs::read_to_string(&config)?;
    let cfg: BakeoffConfig = serde_yml::from_str(&text)
        .map_err(|e| anyhow!("parse {}: {e}", config.display()))?;

    let n_combos = cfg.matrix.chunkers.len() * cfg.matrix.embedders.len();
    if n_combos > 50 && !yes {
        eprintln!(
            "WARNING: {n_combos} combos is large ({} embedders × {} chunkers).",
            cfg.matrix.embedders.len(),
            cfg.matrix.chunkers.len()
        );
        eprintln!("Each combo ingests the full corpus into its own table.");
        eprintln!("Pass --yes to proceed without this prompt.");
        return Err(anyhow!("aborted (matrix size > 50; pass --yes to confirm)"));
    }

    std::env::set_var(&cfg.target.dsn_env, &dsn);

    eprintln!("Running bakeoff '{}' — {n_combos} combos", cfg.name);
    let base_dir = config.parent().map(|p| p.to_path_buf());
    let results = run_bakeoff_with_base(&cfg, base_dir.as_deref()).await?;

    let out_dir = match &cfg.output_dir {
        Some(d) => PathBuf::from(d),
        None => PathBuf::from("skill-output/bakeoff").join(&cfg.name),
    };
    std::fs::create_dir_all(&out_dir)?;

    let json_path = write_results_json(&results, &out_dir)?;
    let report_path = write_report_md(&cfg, &results, &out_dir)?;
    let rec_path = write_recommended_yaml(&cfg, &results, &out_dir)?;

    // Find the winner for stdout summary.
    let winner = results
        .combos
        .iter()
        .max_by(|a, b| {
            let am = a.aggregate.get("mrr").copied().unwrap_or(0.0);
            let bm = b.aggregate.get("mrr").copied().unwrap_or(0.0);
            am.partial_cmp(&bm).unwrap_or(std::cmp::Ordering::Equal)
        })
        .ok_or_else(|| anyhow!("no combos ran"))?;
    eprintln!(
        "\nWinner: {} + {} (MRR={:.3}, r@1={:.3})",
        winner.chunker_label,
        winner.embedder_label,
        winner.aggregate.get("mrr").copied().unwrap_or(0.0),
        winner
            .aggregate
            .get("recall_at_1")
            .copied()
            .unwrap_or(0.0),
    );
    eprintln!("Results: {}", json_path.display());
    eprintln!("Report:  {}", report_path.display());
    eprintln!("Recommended cell: {}", rec_path.display());

    if !keep_schema {
        // Drop the per-combo schema. Identifier safety: cfg.target.schema_name
        // already passed the [a-z_][a-z0-9_]* allowlist on YAML load.
        use sqlx::postgres::PgPoolOptions;
        let pool = PgPoolOptions::new().max_connections(1).connect(&dsn).await?;
        let stmt = format!(
            r#"DROP SCHEMA IF EXISTS "{}" CASCADE"#,
            cfg.target.schema_name
        );
        sqlx::query(&stmt).execute(&pool).await?;
        eprintln!("Dropped schema {} (use --keep-schema to preserve)", cfg.target.schema_name);
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "chunkshop=info".into()),
        )
        .init();

    let cli = Cli::parse();
    match cli.command {
        Command::Ingest { config } => {
            let cfg = load_config(&config)?;
            let result = run_cell(cfg).await?;
            println!(
                "cell {} DONE docs={} chunks={} wall={:.1}s",
                result.cell_name, result.docs_processed, result.chunks_written, result.wall_seconds
            );
            Ok(())
        }
        Command::Bakeoff {
            config,
            dsn,
            yes,
            keep_schema,
        } => run_bakeoff_command(config, dsn, yes, keep_schema).await,
    }
}
