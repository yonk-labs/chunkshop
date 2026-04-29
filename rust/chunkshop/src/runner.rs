//! Single-cell runner: wires source -> chunker -> embedder -> sink.

use std::time::Instant;

use anyhow::{anyhow, Result};
use tracing::info;

use crate::chunker::{
    ChunkerImpl, FixedOverlapChunker, HierarchicalGrouping, HierarchicalSummaryChunker,
    HierarchyChunker, NeighborExpandChunker, SemanticChunker, SentenceAwareChunker,
    SummaryEmbedChunker,
};
use crate::config::{
    CellConfig, ChunkerConfig, EmbedderConfig, FramerConfig, GroupingConfig, SourceConfig,
};
use crate::embedder::FastembedEmbedder;
use crate::extractor::build_extractor;
use crate::framer::{
    FramerImpl, HeadingBoundaryFramer, IdentityFramer, JsonPathFramer, RegexBoundaryFramer,
};
use crate::sink::PgVectorSink;
use crate::source::{
    Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source,
};
use crate::summarizer::build_summarizer;

/// Recursively materialize a `ChunkerConfig` into a boxed trait object.
/// `NeighborExpand` calls back into this fn to construct its `base`. Adding a
/// new chunker = one new arm here + one new variant on `ChunkerConfig`.
pub fn build_chunker(cfg: ChunkerConfig) -> Result<Box<dyn ChunkerImpl + Send + Sync>> {
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
        ChunkerConfig::SummaryEmbed(c) => {
            let mode = c.summarizer.mode_str();
            let base = build_chunker(*c.base)?;
            let summarizer = build_summarizer(&c.summarizer)?;
            Box::new(SummaryEmbedChunker::new(base, summarizer, mode))
        }
        ChunkerConfig::HierarchicalSummary(c) => {
            let mode = c.summarizer.mode_str();
            let base = build_chunker(*c.base)?;
            let summarizer = build_summarizer(&c.summarizer)?;
            let grouping = match c.grouping {
                GroupingConfig::FixedN(g) => HierarchicalGrouping::FixedN(g.n),
                GroupingConfig::WordBudget(g) => HierarchicalGrouping::WordBudget(g.max_words),
                GroupingConfig::SectionAware(_) => HierarchicalGrouping::SectionAware,
            };
            Box::new(HierarchicalSummaryChunker::new(
                base, summarizer, mode, grouping,
            ))
        }
    })
}

/// Materialize a `FramerConfig` into a boxed trait object. Mirrors
/// `build_chunker`. New framer = one new arm here + one new variant.
pub fn build_framer(cfg: FramerConfig) -> Result<Box<dyn FramerImpl + Send + Sync>> {
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
    S3(S3Source),
}

impl AnySource {
    /// Async because PgTable + Http + S3 do network I/O; the file/JSON
    /// variants run sync work inside the async fn (no actual await). Caller
    /// is already in an async context (`run_cell` is async).
    async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
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
        SourceConfig::S3(sc) => AnySource::S3(S3Source::new(sc)),
        SourceConfig::Inline(_) => {
            return Err(anyhow!(
                "inline source has no auto-iterator: drive ingest from your app \
                 with chunkshop::Pipeline::from_yaml(...).ingest_text(doc_id, text, metadata). \
                 See docs/incremental.md (Pattern F) and docs/samples/inline-mode/."
            ));
        }
    };
    let framer = build_framer(cfg.framer)?;
    let chunker = build_chunker(cfg.chunker)?;
    let extractor = build_extractor(cfg.extractor)?;
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

            // Per-chunk extractor pass. Tags collected for the sink; metadata
            // merged into each chunk with chunker-wins semantics:
            //   {**doc.metadata, **r.metadata, **c.metadata}
            // matching python/src/chunkshop/runner.py.
            let mut tags_per_chunk: Vec<Vec<String>> = Vec::with_capacity(chunks.len());
            let mut chunks_with_meta: Vec<crate::chunker::Chunk> =
                Vec::with_capacity(chunks.len());
            let doc_meta = doc.metadata.as_object().cloned().unwrap_or_default();
            for c in &chunks {
                let r = extractor.extract(&c.original_content)?;
                let mut merged = serde_json::Map::new();
                for (k, v) in &doc_meta {
                    merged.insert(k.clone(), v.clone());
                }
                for (k, v) in &r.metadata {
                    merged.insert(k.clone(), v.clone());
                }
                if let Some(c_meta) = c.metadata.as_object() {
                    for (k, v) in c_meta {
                        merged.insert(k.clone(), v.clone());
                    }
                }
                tags_per_chunk.push(r.tags);
                chunks_with_meta.push(crate::chunker::Chunk {
                    doc_id: c.doc_id.clone(),
                    seq_num: c.seq_num,
                    original_content: c.original_content.clone(),
                    embedded_content: c.embedded_content.clone(),
                    metadata: serde_json::Value::Object(merged),
                });
            }

            sink.write_document(&chunks_with_meta, &embeddings, &tags_per_chunk)
                .await?;
            chunks_written += chunks_with_meta.len();
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
