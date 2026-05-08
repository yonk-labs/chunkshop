//! SQLite sink (placeholder — Tasks 8-12 fill in real two-table-dance behavior).

use std::future::Future;
use anyhow::{anyhow, Result};
use crate::backends::sqlite::SQLiteBackend;
use crate::chunker::Chunk;
use crate::config::SqliteTargetConfig;
use crate::sinks::base::Sink;

#[derive(Clone)]
pub struct SqliteSink {
    pub(crate) cfg: SqliteTargetConfig,
    pub(crate) backend: SQLiteBackend,
    pub(crate) embed_dim: usize,
}

impl SqliteSink {
    pub fn new(cfg: SqliteTargetConfig, backend: SQLiteBackend, embed_dim: usize) -> Self {
        Self { cfg, backend, embed_dim }
    }
}

impl Sink for SqliteSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow!("SqliteSink::create_table not yet implemented")) }
    }
    fn write_document(
        &self, _doc_id: &str, _chunks: &[Chunk],
        _embeddings: &[Vec<f32>], _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow!("SqliteSink::write_document not yet implemented")) }
    }
    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("SqliteSink::delete_document not yet implemented")) }
    }
    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("SqliteSink::count_docs not yet implemented")) }
    }
    fn query_top_k(
        &self, _query_vec: &[f32], _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { Err(anyhow!("SqliteSink::query_top_k not yet implemented")) }
    }
}
