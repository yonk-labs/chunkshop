//! Sink trait — chunkshop's data-model semantics on a backend.
//!
//! Mirrors `python/src/chunkshop/sinks/base.py` Sink Protocol: 5 methods,
//! one per concern. Per-backend impls (PgSink, MariadbSink, etc.) own mode
//! dispatch (overwrite/append/create_if_missing), foreign-tag safety,
//! append preflight, source write-once on UPDATE, delete_orphans behavior,
//! and the canonical chunks-table column list.

use std::future::Future;

use anyhow::Result;

use crate::chunker::Chunk;

pub trait Sink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send;

    /// `chunks`, `embeddings`, and `tags_per_chunk` MUST all be the same length.
    /// Implementations may panic (not return `Err`) on divergence — that's a
    /// programming error, not a runtime condition.
    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send;

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send;

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send;

    /// Returns `(doc_id, seq_num, distance)` tuples for the `k` nearest chunks
    /// to `query_vec`, ordered by ascending cosine distance.
    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send;
}
