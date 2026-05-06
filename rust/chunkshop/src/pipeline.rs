//! Pipeline — chunkshop as a library. The host application drives ingestion.
//!
//! Mirrors `python/src/chunkshop/pipeline.py`. Skips the YAML-defined source.
//! The YAML still pins chunker / embedder / extractor / framer / target so
//! chunks land identically across services, but no auto-iteration happens.
//!
//! ```ignore
//! let mut shop = chunkshop::Pipeline::from_yaml("inline.yaml").await?;
//! shop.ingest_text("note-001", "...", serde_json::json!({"author": "alice"})).await?;
//! shop.delete_document("note-002").await?;
//! ```
//!
//! `delete_document` is scoped to this pipeline's `source_tag` so a Pipeline
//! configured for `source_tag = "X"` cannot delete rows owned by source_tag
//! "Y" — same write-once provenance contract the upsert path enforces.

use anyhow::{anyhow, Context, Result};
use serde_json::Value;
use sqlx::Row;

use crate::chunker::{Chunk, ChunkerImpl};
use crate::config::{CellConfig, EmbedderConfig, SourceConfig, TargetConfig};
use crate::embedder::FastembedEmbedder;
use crate::extractor::{build_extractor, ExtractorImpl};
use crate::framer::FramerImpl;
use crate::runner::{build_chunker, build_framer};
use crate::sinks::{AnySink, Sink};
use crate::sources::Document;

pub struct Pipeline {
    cfg: CellConfig,
    framer: Box<dyn FramerImpl + Send + Sync>,
    chunker: Box<dyn ChunkerImpl + Send + Sync>,
    extractor: Box<dyn ExtractorImpl>,
    embedder: FastembedEmbedder,
    sink: AnySink,
}

impl Pipeline {
    /// Build a pipeline from an in-memory `CellConfig`. Requires
    /// `source.type = inline` — any other source type errors immediately.
    pub async fn new(cfg: CellConfig) -> Result<Self> {
        match &cfg.source {
            SourceConfig::Inline(_) => {}
            other => {
                return Err(anyhow!(
                    "Pipeline requires source.type='inline', got {:?}. \
                     Use chunkshop::run_cell(cfg) for source-driven configs.",
                    std::mem::discriminant(other),
                ));
            }
        }
        let framer = build_framer(cfg.framer.clone())?;
        let chunker = build_chunker(cfg.chunker.clone())?;
        let extractor = build_extractor(cfg.extractor.clone())?;
        let embedder = match cfg.embedder.clone() {
            EmbedderConfig::Fastembed(ec) => FastembedEmbedder::new(ec)?,
        };
        let backend = crate::backends::load_backend(&cfg.target).context("load backend")?;
        let sink = crate::sinks::load_sink(&cfg.target, backend, embedder.dim())
            .context("load sink")?;
        sink.create_table().await?;
        Ok(Self { cfg, framer, chunker, extractor, embedder, sink })
    }

    /// Convenience: load YAML from disk, validate, and construct.
    pub async fn from_yaml<P: AsRef<std::path::Path>>(path: P) -> Result<Self> {
        let cfg = crate::config::load_config(path.as_ref())?;
        Self::new(cfg).await
    }

    /// Run one document through framer → chunker → embedder → extractor → sink.
    /// Returns the number of chunks written.
    pub async fn ingest_text(
        &mut self,
        doc_id: &str,
        text: &str,
        metadata: Value,
    ) -> Result<usize> {
        let doc = Document {
            id: doc_id.to_string(),
            content: text.to_string(),
            title: None,
            metadata,
        };
        self.ingest_document(&doc).await
    }

    pub async fn ingest_document(&mut self, doc: &Document) -> Result<usize> {
        let mut chunks_written = 0usize;
        let framed = self.framer.frame(doc)?;
        for fdoc in framed {
            let chunks = self.chunker.chunk(&fdoc);
            if chunks.is_empty() {
                continue;
            }
            let texts: Vec<String> =
                chunks.iter().map(|c| c.embedded_content.clone()).collect();
            let embeddings = self.embedder.embed(texts)?;

            let doc_meta = fdoc.metadata.as_object().cloned().unwrap_or_default();
            let mut tags_per_chunk: Vec<Vec<String>> = Vec::with_capacity(chunks.len());
            let mut chunks_with_meta: Vec<Chunk> = Vec::with_capacity(chunks.len());
            for c in &chunks {
                let r = self.extractor.extract(&c.original_content)?;
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
                chunks_with_meta.push(Chunk {
                    doc_id: c.doc_id.clone(),
                    seq_num: c.seq_num,
                    original_content: c.original_content.clone(),
                    embedded_content: c.embedded_content.clone(),
                    metadata: Value::Object(merged),
                });
            }

            self.sink
                .write_document(&fdoc.id, &chunks_with_meta, &embeddings, &tags_per_chunk)
                .await
                .context("write_document")?;
            chunks_written += chunks_with_meta.len();
        }
        Ok(chunks_written)
    }

    /// Remove every chunk for a doc_id, scoped to this pipeline's source_tag.
    /// Returns the number of rows deleted. Mirrors Python's
    /// `Pipeline.delete_document`.
    pub async fn delete_document(&self, doc_id: &str) -> Result<u64> {
        Ok(self.sink.delete_document(doc_id).await? as u64)
    }

    /// Live-progress count. Wraps the sink's COUNT(DISTINCT doc_id).
    pub async fn count_docs(&self) -> Result<i64> {
        self.sink.count_docs().await
    }

    /// Used by the demo — return one row's text preview for stdout. PG-only
    /// (uses raw SQL via the underlying pool); other backends will need their
    /// own paths once R2/R3/R4 add variants.
    pub async fn sample_row(&self, doc_id: &str) -> Result<Option<(i32, String)>> {
        // Both let-bindings are irrefutable in R1 (single-variant enums). R2
        // introduces additional AnySink + TargetConfig variants — these
        // become refutable then and the compiler tells us where to add
        // match arms. Compile-fail is the right signal vs runtime panic.
        let AnySink::Pg(pg_sink) = &self.sink;
        let TargetConfig::Postgres(target) = &self.cfg.target;
        let fq = format!("\"{}\".\"{}\"", target.database_name, target.table);
        let stmt = format!(
            "SELECT seq_num, left(original_content, 80) FROM {tbl} \
             WHERE doc_id = $1 ORDER BY seq_num LIMIT 1",
            tbl = fq
        );
        let pool = pg_sink.pool().await?;
        let row = sqlx::query(&stmt)
            .bind(doc_id)
            .fetch_optional(pool)
            .await?;
        Ok(row.map(|r| (r.get::<i32, _>(0), r.get::<String, _>(1))))
    }
}
