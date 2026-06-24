//! Sink trait — chunkshop's data-model semantics on a backend.
//!
//! Mirrors `python/src/chunkshop/sinks/base.py` Sink Protocol: 5 methods,
//! one per concern. Per-backend impls (PgSink, MariadbSink, etc.) own mode
//! dispatch (overwrite/append/create_if_missing), foreign-tag safety,
//! append preflight, source write-once on UPDATE, delete_orphans behavior,
//! and the canonical chunks-table column list.

use std::future::Future;

use anyhow::{anyhow, Result};
use serde_json::{Map, Value};

use crate::chunker::Chunk;

/// AND-equality filter set for scoped top-k retrieval (#75).
///
/// Every present predicate is ANDed; an all-empty `Filters` (the `Default`)
/// matches every row — a filtered query then behaves exactly like
/// [`Sink::query_top_k`]. Equality match on a small key set covers the common
/// per-tenant / per-thread / per-source scoping case; a full predicate DSL and
/// hybrid-FTS fusion are out of scope (see #75).
#[derive(Debug, Clone, Default)]
pub struct Filters {
    /// Exact match on the `source` provenance column.
    pub source: Option<String>,
    /// Case-insensitive overlap on the `tags` array — a row matches if it
    /// carries ANY of these tags. (I-6: prefer structured fields for
    /// recall-critical scoping; free-text tags diverge between query and doc
    /// vocabularies and a hard overlap filter can silently drop the answer.)
    pub tags: Vec<String>,
    /// AND-containment on `metadata`: every (key, value) must match. Postgres
    /// uses jsonb `@>`; SQLite approximates with per-key `json_extract` on
    /// top-level keys (nested containment unsupported — same gap as Python).
    pub metadata: Map<String, Value>,
    /// AND-equality on promoted columns, keyed by column name (ident-validated).
    /// Compared as text — intended for string scoping keys (tenant/thread id).
    pub columns: Map<String, Value>,
}

impl Filters {
    /// True when no predicate is set — a filtered query then equals `query_top_k`.
    pub fn is_empty(&self) -> bool {
        self.source.is_none()
            && self.tags.is_empty()
            && self.metadata.is_empty()
            && self.columns.is_empty()
    }
}

/// Render a JSON filter value to its column/text representation: strings as-is,
/// everything else via its compact JSON form. Mirrors how promoted-column
/// values are written (`promote_value_for`), so a `column` filter compares
/// against the same text that was stored.
pub(crate) fn render_filter_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

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

    /// Like [`Sink::query_top_k`] but restricted to rows matching ALL of
    /// `filter`'s predicates (AND). `None` or an empty [`Filters`] is identical
    /// to `query_top_k`. Additive — `query_top_k` is unchanged (#75).
    ///
    /// The default delegates to `query_top_k` when there is nothing to filter,
    /// and errors otherwise. Backends that support scoped retrieval (pgvector,
    /// sqlite-vec) override this; backends that don't inherit the honest error.
    fn query_top_k_filtered(
        &self,
        query_vec: &[f32],
        k: usize,
        filter: Option<&Filters>,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send
    where
        Self: Sync,
    {
        async move {
            match filter {
                Some(f) if !f.is_empty() => Err(anyhow!(
                    "query_top_k_filtered: metadata-filtered retrieval is not implemented \
                     for this sink"
                )),
                _ => self.query_top_k(query_vec, k).await,
            }
        }
    }
}
