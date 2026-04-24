//! chunkshop-rs CLI entry point.
//!
//! `chunkshop-rs ingest --config PATH` runs a single cell end-to-end.

use std::path::PathBuf;

use anyhow::Result;
use clap::{Parser, Subcommand};

use chunkshop::{load_config, run_cell};

#[derive(Parser)]
#[command(name = "chunkshop-rs", version, about = "Minimal Rust chunkshop ingest")]
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
    }
}
