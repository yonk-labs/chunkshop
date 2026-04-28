//! Single-cell runner: wires source -> chunker -> embedder -> sink.

use std::time::Instant;

use anyhow::Result;
use tracing::info;

use crate::chunker::{Chunk, HierarchyChunker, SentenceAwareChunker};
use crate::config::{CellConfig, ChunkerConfig, EmbedderConfig, SourceConfig};
use crate::source::Document;
use crate::embedder::FastembedEmbedder;
use crate::sink::PgVectorSink;
use crate::source::{FilesSource, JsonCorpusSource};

/// Runtime dispatch over the supported chunkers. Mirrors the
/// discriminated-union `ChunkerConfig` so each YAML branch maps to a single
/// concrete implementation. Adding a new chunker = one variant + one match arm
/// in the `match cfg.chunker` block above + one match arm in `chunk()` below.
enum AnyChunker {
    SentenceAware(SentenceAwareChunker),
    Hierarchy(HierarchyChunker),
}

impl AnyChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        match self {
            AnyChunker::SentenceAware(c) => c.chunk(doc),
            AnyChunker::Hierarchy(c) => c.chunk(doc),
        }
    }
}

/// Same dispatch pattern for sources. `iter_documents` returns owned `Document`s
/// because both backends materialize their corpus eagerly today; if a streaming
/// source ever lands, change this to a boxed iterator.
enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
}

impl AnySource {
    fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
        }
    }
}

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

    let source: AnySource = match cfg.source {
        SourceConfig::Files(fc) => AnySource::Files(FilesSource::new(fc)),
        SourceConfig::JsonCorpus(jc) => AnySource::JsonCorpus(JsonCorpusSource::new(jc)),
    };
    let chunker: AnyChunker = match cfg.chunker {
        ChunkerConfig::SentenceAware(cc) => AnyChunker::SentenceAware(SentenceAwareChunker::new(cc)),
        ChunkerConfig::Hierarchy(cc) => AnyChunker::Hierarchy(HierarchyChunker::new(cc)),
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
