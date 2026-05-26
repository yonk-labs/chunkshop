//! Source-side shared types. `Document` is the unit yielded by every source.
//!
//! Mirrors `python/src/chunkshop/sources/base.py`. Per-source impls live in
//! sibling files (files.rs, json_corpus.rs, pg_table.rs, http.rs, s3.rs).
//!
//! RM-B Task 1 adds the SP-1 sync primitives that Python landed in 0.6.0:
//! `SyncMode`, `IncrementalSource`, `PrunableSource`, `StaleCursorError`, and
//! the `fingerprint: Option<String>` field on `Document`.

use serde::{Deserialize, Serialize};
use std::future::Future;
use thiserror::Error;

/// A document emitted by a Source.
///
/// `metadata` is `serde_json::Value` so it can round-trip rich nested types
/// (vs. Python's flat `dict[str, Any]`). `fingerprint` (RM-B / SP-1) is an
/// opaque per-document signature (ETag, content-hash, mtime, etc.) used by
/// consumers running `SyncMode::Fingerprint` to detect changes without a
/// per-source cursor.
#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub content: String,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
    /// Optional per-document signature (e.g., S3 ETag, content SHA, last_modified).
    /// `None` for sources that don't track fingerprints. Populated by S3/HTTP
    /// sources; left `None` by sources that use a cursor instead (pg_table).
    pub fingerprint: Option<String>,
}

/// How a Source detects changes between runs. Mirrors
/// `chunkshop.sources.base.SyncMode` exactly (snake_case wire format).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum SyncMode {
    /// Re-emit all documents on every run; consumer dedupes by content hash.
    #[default]
    FullResync,
    /// Source implements `IncrementalSource`; consumer persists an opaque cursor.
    Cursor,
    /// Source enumerates all documents with a per-doc `fingerprint`; consumer
    /// diffs against its prior set.
    Fingerprint,
}

/// Raised by `iter_changes_since` when a server-side cursor is too old to honor.
///
/// Consumers should treat this as a signal to fall back to a full resync:
/// re-call with `empty_cursor()` (or call the source's `iter_documents()` path
/// directly).
///
/// Constructed via `StaleCursorError::new(msg)`. Auto-converts into
/// `anyhow::Error`, so impls returning `anyhow::Result` can simply
/// `return Err(StaleCursorError::new("...").into())`. Consumers detect it via
/// `err.downcast_ref::<StaleCursorError>()`.
#[derive(Debug, Error)]
#[error("stale cursor: {0}")]
pub struct StaleCursorError(String);

impl StaleCursorError {
    pub fn new(msg: impl Into<String>) -> Self {
        Self(msg.into())
    }

    /// Borrow the message body without the `stale cursor:` prefix.
    pub fn message(&self) -> &str {
        &self.0
    }
}

/// Sources that support cursor-based incremental sync implement this.
///
/// Cursor shape is source-specific:
/// - S3: `BTreeMap<String, String>` (key → ETag)
/// - pg_table: `{ after_ts: String, after_id: String }` tuple cursor
/// - http: `BTreeMap<String, HttpUrlCursor>` (url → ETag + Last-Modified)
///
/// Consumers treat the cursor as opaque. chunkshop never stores it — the
/// consumer (orchestrator, application) persists it between runs.
///
/// `cursor_from(last)` returns a per-doc DELTA. Consumers build the next
/// cursor by starting from the previous cursor and merging each emitted
/// doc's delta in iteration order:
///
/// ```ignore
/// let mut next = prev.clone();
/// for d in docs { next.extend(source.cursor_from(d)); }
/// ```
///
/// For map-style cursors (S3, http) this accumulates the full manifest and
/// preserves unchanged keys. For monotonic cursors (pg_table) the last doc
/// wins.
///
/// **Async-in-trait convention.** Matches `BackendConn`'s pattern: explicit
/// `impl Future<Output = …> + Send` rather than the bare `async fn` sugar,
/// so the auto-`Send` bound on the returned future is captured explicitly
/// and resilient across compiler versions.
pub trait IncrementalSource: Send + Sync {
    /// Per-source cursor type. Must serde-round-trip (so the consumer can
    /// persist it as JSON / YAML / a column).
    type Cursor: Default + Serialize + for<'de> Deserialize<'de> + Clone + Send + Sync;

    /// Initial cursor for a never-synced state. Returned by the consumer's
    /// first call before any documents are emitted.
    fn empty_cursor(&self) -> Self::Cursor;

    /// Stream documents changed since `cursor`. May return
    /// `Err(StaleCursorError::new(...).into())` to signal the cursor is too
    /// old; consumer should retry with `empty_cursor()`.
    fn iter_changes_since(
        &self,
        cursor: &Self::Cursor,
    ) -> impl Future<Output = anyhow::Result<Vec<Document>>> + Send;

    /// Per-doc cursor DELTA. See trait docs for merge contract.
    fn cursor_from(&self, last_document: &Document) -> Self::Cursor;
}

/// Sources that can enumerate source-side deletions implement this.
///
/// Typically called at a lower cadence than `iter_changes_since` because
/// prune detection often requires walking the full source manifest. Returns
/// source-IDs (the `Document.id` field), not full `Document` objects.
pub trait PrunableSource: Send + Sync {
    type Cursor: Default + Serialize + for<'de> Deserialize<'de> + Clone + Send + Sync;

    fn empty_prune_cursor(&self) -> Self::Cursor;

    fn iter_deleted_since(
        &self,
        cursor: &Self::Cursor,
    ) -> impl Future<Output = anyhow::Result<Vec<String>>> + Send;
}
