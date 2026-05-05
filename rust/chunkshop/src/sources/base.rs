//! Source-side shared types. `Document` is the unit yielded by every source.
//!
//! Mirrors `python/src/chunkshop/sources/base.py`. Per-source impls live in
//! sibling files (files.rs, json_corpus.rs, pg_table.rs, http.rs, s3.rs).

#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub content: String,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
}
