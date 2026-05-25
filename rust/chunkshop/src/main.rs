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
use chunkshop::config::{
    ChunkerConfig, EmbedderConfig, ExtractorConfig, FramerConfig, SourceConfig, TargetConfig,
};
use chunkshop::{load_config, run_bakeoff_with_base, run_cell};

fn source_type_label(s: &SourceConfig) -> &'static str {
    match s {
        SourceConfig::Files(_) => "files",
        SourceConfig::JsonCorpus(_) => "json_corpus",
        SourceConfig::PgTable(_) => "pg_table",
        SourceConfig::MariadbTable(_) => "mariadb_table",
        SourceConfig::SqliteTable(_) => "sqlite_table",
        SourceConfig::Http(_) => "http",
        SourceConfig::S3(_) => "s3",
        SourceConfig::ClickhouseTable(_) => "clickhouse_table",
        SourceConfig::SessionStaging(_) => "session_staging",
        SourceConfig::Inline(_) => "inline",
    }
}

fn framer_type_label(f: &FramerConfig) -> &'static str {
    match f {
        FramerConfig::Identity(_) => "identity",
        FramerConfig::HeadingBoundary(_) => "heading_boundary",
        FramerConfig::RegexBoundary(_) => "regex_boundary",
        FramerConfig::Jsonpath(_) => "jsonpath",
        FramerConfig::SessionEpisode(_) => "session_episode",
    }
}

fn chunker_type_label(c: &ChunkerConfig) -> &'static str {
    match c {
        ChunkerConfig::SentenceAware(_) => "sentence_aware",
        ChunkerConfig::Hierarchy(_) => "hierarchy",
        ChunkerConfig::FixedOverlap(_) => "fixed_overlap",
        ChunkerConfig::NeighborExpand(_) => "neighbor_expand",
        ChunkerConfig::Semantic(_) => "semantic",
        ChunkerConfig::SummaryEmbed(_) => "summary_embed",
        ChunkerConfig::HierarchicalSummary(_) => "hierarchical_summary",
        ChunkerConfig::Consolidation(_) => "consolidation",
    }
}

fn embedder_type_label(e: &EmbedderConfig) -> String {
    match e {
        EmbedderConfig::Fastembed(c) => format!("fastembed: {} (dim={})", c.model_name, c.dim),
    }
}

fn extractor_type_label(e: &ExtractorConfig) -> &'static str {
    match e {
        ExtractorConfig::None(_) => "none",
        ExtractorConfig::Composite(_) => "composite",
        ExtractorConfig::RakeKeywords(_) => "rake_keywords",
        ExtractorConfig::LangDetect(_) => "lang_detect",
        ExtractorConfig::KeybertPhrases(_) => "keybert_phrases",
        ExtractorConfig::SpacyEntities(_) => "spacy_entities",
    }
}

fn target_type_label(t: &TargetConfig) -> String {
    match t {
        TargetConfig::Postgres(c) => format!(
            "postgres -> {}.{} (mode={})",
            c.database_name, c.table, c.mode
        ),
        TargetConfig::Mariadb(c) => format!(
            "mariadb -> {}.{} (mode={})",
            c.database_name, c.table, c.mode
        ),
        TargetConfig::Sqlite(c) => format!(
            "sqlite -> {}.{} (mode={})",
            c.database_name, c.table, c.mode
        ),
        TargetConfig::Clickhouse(c) => format!(
            "clickhouse -> {}.{} (mode={})",
            c.database_name, c.table, c.mode
        ),
    }
}

#[derive(Parser)]
#[command(
    name = "chunkshop-rs",
    version,
    about = "Rust chunkshop ingest + bakeoff"
)]
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
    /// Validate a YAML config without running it. Exits 0 if valid, non-zero otherwise.
    ///
    /// Loads + runs serde + custom validation (identifier regex, mode/source_tag
    /// coupling, BYO-embedder field pairing). Does NOT open DB connections or
    /// create tables.
    Validate {
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
        /// Optional Postgres DSN (legacy single-target mode). Sets
        /// `target.dsn_env` before running. For multi-target bakeoffs
        /// (YAML uses `targets:` list), DSN env vars must be exported
        /// before invocation — this flag is ignored.
        #[arg(long)]
        dsn: Option<String>,
        /// Bypass the >50-cell matrix confirmation prompt.
        #[arg(long)]
        yes: bool,
        /// Keep the bakeoff schema after run (default: drop CASCADE on exit).
        /// Only applies to legacy single-PG bakeoffs.
        #[arg(long)]
        keep_schema: bool,
    },
}

async fn run_bakeoff_command(
    config: PathBuf,
    dsn: Option<String>,
    yes: bool,
    keep_schema: bool,
) -> Result<()> {
    let text = std::fs::read_to_string(&config)?;
    let cfg: BakeoffConfig =
        serde_yaml_ng::from_str(&text).map_err(|e| anyhow!("parse {}: {e}", config.display()))?;

    let combos_per_target = cfg.matrix.chunkers.len() * cfg.matrix.embedders.len();
    let targets = cfg.effective_targets()?;
    let n_combos = combos_per_target * targets.len();

    if n_combos > 50 && !yes {
        eprintln!(
            "WARNING: {n_combos} combos is large ({} embedders × {} chunkers × {} targets).",
            cfg.matrix.embedders.len(),
            cfg.matrix.chunkers.len(),
            targets.len(),
        );
        eprintln!("Each combo ingests the full corpus into its own table.");
        eprintln!("Pass --yes to proceed without this prompt.");
        return Err(anyhow!("aborted (matrix size > 50; pass --yes to confirm)"));
    }

    // Legacy single-target mode: --dsn populates the env var for the single
    // Postgres target the YAML references via target.dsn_env. Multi-target
    // mode: env vars must already be set; --dsn ignored.
    if let Some(d) = &dsn {
        if let Some(legacy) = &cfg.target {
            std::env::set_var(&legacy.dsn_env, d);
        } else {
            eprintln!(
                "NOTE: --dsn ignored for multi-target bakeoff; \
                 export DSN env vars before running."
            );
        }
    }

    eprintln!(
        "Running bakeoff '{}' — {n_combos} combos across {} target(s): {}",
        cfg.name,
        targets.len(),
        targets
            .iter()
            .map(|t| t.backend_name())
            .collect::<Vec<_>>()
            .join(", "),
    );
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
        winner.aggregate.get("recall_at_1").copied().unwrap_or(0.0),
    );
    eprintln!("Results: {}", json_path.display());
    eprintln!("Report:  {}", report_path.display());
    eprintln!("Recommended cell: {}", rec_path.display());

    if !keep_schema && cfg.target.is_some() {
        // Legacy single-PG cleanup only. Multi-target bakeoffs don't auto-drop
        // (caller's responsibility — many backends, many concerns, easy to mess up).
        use sqlx::postgres::PgPoolOptions;
        let legacy = cfg.target.as_ref().unwrap();
        let dsn_str = dsn
            .as_ref()
            .ok_or_else(|| anyhow!("--dsn required for legacy single-PG cleanup"))?;
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(dsn_str)
            .await?;
        let stmt = format!(r#"DROP SCHEMA IF EXISTS "{}" CASCADE"#, legacy.schema_name);
        sqlx::query(&stmt).execute(&pool).await?;
        eprintln!(
            "Dropped schema {} (use --keep-schema to preserve)",
            legacy.schema_name
        );
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    // CHUNKSHOP_LOG_FORMAT=json switches the tracing subscriber to JSON output
    // (one structured event per line — for log aggregators). Default: text.
    // Mirror of runtime.log_format in YAML; env var takes precedence here
    // because tracing-subscriber initializes once at process start, before the
    // subcommand has loaded the YAML.
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| "chunkshop=info".into());
    if std::env::var("CHUNKSHOP_LOG_FORMAT").as_deref() == Ok("json") {
        tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .init();
    } else {
        tracing_subscriber::fmt().with_env_filter(filter).init();
    }

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
        Command::Validate { config } => match load_config(&config) {
            Ok(cfg) => {
                println!("[validate] OK — cell {:?}", cfg.cell_name);
                println!("  source:   {}", source_type_label(&cfg.source));
                println!("  framer:   {}", framer_type_label(&cfg.framer));
                println!("  chunker:  {}", chunker_type_label(&cfg.chunker));
                let emb_label = embedder_type_label(&cfg.embedder);
                println!("  embedder: {emb_label}");
                println!("  extractor:{}", extractor_type_label(&cfg.extractor));
                let tgt = target_type_label(&cfg.target);
                println!("  target:   {tgt}");
                Ok(())
            }
            Err(e) => {
                eprintln!("[validate] FAIL: {e:#}");
                std::process::exit(1);
            }
        },
        Command::Bakeoff {
            config,
            dsn,
            yes,
            keep_schema,
        } => run_bakeoff_command(config, dsn, yes, keep_schema).await,
    }
}
