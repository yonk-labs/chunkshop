//! Single-cell runner: wires source -> chunker -> embedder -> sink.

use std::time::Instant;

use anyhow::Result;
use tracing::info;

use crate::chunker::{
    ChunkerImpl, FixedOverlapChunker, HierarchyChunker, NeighborExpandChunker, SemanticChunker,
    SentenceAwareChunker,
};
use crate::config::{CellConfig, ChunkerConfig, EmbedderConfig, FramerConfig, SourceConfig};
use crate::embedder::FastembedEmbedder;
use crate::framer::{
    FramerImpl, HeadingBoundaryFramer, IdentityFramer, JsonPathFramer, RegexBoundaryFramer,
};
use crate::sink::PgVectorSink;
use crate::source::{Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource};

/// Recursively materialize a `ChunkerConfig` into a boxed trait object.
/// `NeighborExpand` calls back into this fn to construct its `base`. Adding a
/// new chunker = one new arm here + one new variant on `ChunkerConfig`.
fn build_chunker(cfg: ChunkerConfig) -> Result<Box<dyn ChunkerImpl + Send + Sync>> {
    Ok(match cfg {
        ChunkerConfig::SentenceAware(c) => Box::new(SentenceAwareChunker::new(c)),
        ChunkerConfig::Hierarchy(c) => Box::new(HierarchyChunker::new(c)),
        ChunkerConfig::FixedOverlap(c) => Box::new(FixedOverlapChunker::new(c)?),
        ChunkerConfig::NeighborExpand(c) => {
            let window = c.window;
            let base = build_chunker(*c.base)?;
            Box::new(NeighborExpandChunker::new(window, base))
        }
        ChunkerConfig::Semantic(c) => Box::new(SemanticChunker::new(c)?),
    })
}

/// Materialize a `FramerConfig` into a boxed trait object. Mirrors
/// `build_chunker`. New framer = one new arm here + one new variant.
fn build_framer(cfg: FramerConfig) -> Result<Box<dyn FramerImpl + Send + Sync>> {
    Ok(match cfg {
        FramerConfig::Identity(c) => Box::new(IdentityFramer::new(c)),
        FramerConfig::HeadingBoundary(c) => Box::new(HeadingBoundaryFramer::new(c)?),
        FramerConfig::RegexBoundary(c) => Box::new(RegexBoundaryFramer::new(c)?),
        FramerConfig::Jsonpath(c) => Box::new(JsonPathFramer::new(c)),
    })
}

/// Same dispatch pattern for sources. `iter_documents` returns owned `Document`s
/// because the file/json/pg backends all materialize their corpus eagerly today;
/// if a streaming source ever lands, change this to a boxed async iterator.
enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    Http(HttpSource),
}

impl AnySource {
    /// Async because PgTable + Http do network I/O; the file/JSON variants
    /// run sync work inside the async fn (no actual await). Caller is already
    /// in an async context (`run_cell` is async).
    async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
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
        SourceConfig::PgTable(pc) => AnySource::PgTable(PgTableSource::new(pc)),
        SourceConfig::Http(hc) => AnySource::Http(HttpSource::new(hc)),
    };
    let framer = build_framer(cfg.framer)?;
    let chunker = build_chunker(cfg.chunker)?;
    let mut embedder = match cfg.embedder {
        EmbedderConfig::Fastembed(ec) => FastembedEmbedder::new(ec)?,
    };
    let sink = PgVectorSink::connect(cfg.target, embedder.dim()).await?;

    info!("creating target table");
    sink.create_table().await?;

    let docs = source.iter_documents().await?;
    let limit = cfg.runtime.doc_limit.unwrap_or(usize::MAX);
    let heartbeat = cfg.runtime.heartbeat_every.unwrap_or(25);

    let mut docs_processed = 0usize;
    let mut chunks_written = 0usize;

    // Source emits raw documents; framer expands each raw into one or more
    // framed documents; chunker chunks each framed doc independently. The
    // doc limit applies at the raw level (matches Python).
    for raw in docs.into_iter().take(limit) {
        let framed = framer.frame(&raw)?;
        for doc in framed {
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
