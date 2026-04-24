//! Single-cell runner: wires source -> chunker -> embedder -> sink.

use std::time::Instant;

use anyhow::Result;
use tracing::info;

use crate::chunker::SentenceAwareChunker;
use crate::config::{CellConfig, ChunkerConfig, EmbedderConfig, SourceConfig};
use crate::embedder::FastembedEmbedder;
use crate::sink::PgVectorSink;
use crate::source::FilesSource;

#[derive(Debug, Clone)]
pub struct CellResult {
    pub cell_name: String,
    pub docs_processed: usize,
    pub chunks_written: usize,
    pub wall_seconds: f64,
}

pub async fn run_cell(cfg: CellConfig) -> Result<CellResult> {
    let start = Instant::now();
    info!(cell = %cfg.cell_name, "cell starting");

    let source = match cfg.source {
        SourceConfig::Files(fc) => FilesSource::new(fc),
    };
    let chunker = match cfg.chunker {
        ChunkerConfig::SentenceAware(cc) => SentenceAwareChunker::new(cc),
    };
    let mut embedder = match cfg.embedder {
        EmbedderConfig::Fastembed(ec) => FastembedEmbedder::new(ec)?,
    };
    let sink = PgVectorSink::connect(cfg.target, embedder.dim()).await?;

    info!("creating target table");
    sink.create_table().await?;

    let docs = source.iter_documents()?;
    let limit = cfg.runtime.doc_limit.unwrap_or(usize::MAX);
    let heartbeat = cfg.runtime.heartbeat_every.unwrap_or(25);

    let mut docs_processed = 0usize;
    let mut chunks_written = 0usize;

    for doc in docs.into_iter().take(limit) {
        let chunks = chunker.chunk(&doc);
        if chunks.is_empty() {
            docs_processed += 1;
            continue;
        }
        let texts: Vec<String> = chunks.iter().map(|c| c.embedded_content.clone()).collect();
        let embeddings = embedder.embed(texts)?;
        sink.write_document(&chunks, &embeddings).await?;
        chunks_written += chunks.len();
        docs_processed += 1;
        if docs_processed % heartbeat == 0 {
            info!(
                docs = docs_processed,
                chunks = chunks_written,
                "heartbeat"
            );
        }
    }

    let wall = start.elapsed().as_secs_f64();
    info!(cell = %cfg.cell_name, docs = docs_processed, chunks = chunks_written, wall = wall, "cell DONE");
    Ok(CellResult {
        cell_name: cfg.cell_name,
        docs_processed,
        chunks_written,
        wall_seconds: wall,
    })
}
