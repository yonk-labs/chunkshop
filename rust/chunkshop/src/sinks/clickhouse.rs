//! ClickHouse sink — append-only chunks-table writer using ClickhouseBackend dialect.
//!
//! Mirrors python/src/chunkshop/sinks/clickhouse.py. Same shape as PgSink but
//! INSERT-only (no `ON CONFLICT`); `delete_orphans: true` warns once per process
//! and no-ops. Bulk INSERT via the official `clickhouse` crate's `Insert<T>`.

#![allow(dead_code, unused_imports)]

use std::sync::OnceLock;

use anyhow::{anyhow, Context, Result};
use clickhouse::{Client, Row};
use serde::Serialize;
use tracing::warn;

use crate::backends::base::{BackendDialect, ColSpec};
use crate::backends::clickhouse::ClickhouseBackend;
use crate::chunker::Chunk;
use crate::config::{ClickhouseTargetConfig, PromoteColumn};

/// One-time-per-process warn flag for `delete_orphans: true`. CH mutations are
/// async and don't fit chunkshop's per-document atomic write contract, so the
/// flag is treated as a no-op + warning. PID-keying isn't necessary in Rust
/// (each process gets its own static); the OnceLock is process-scoped by
/// definition.
static DELETE_ORPHANS_WARNED: OnceLock<()> = OnceLock::new();

const ORPHAN_WARN_MSG: &str =
    "target.delete_orphans=true on ClickHouse is a no-op — CH mutations are async \
     background ops that don't fit chunkshop's per-document atomic write contract. \
     Use ReplacingMergeTree(created_at) for lazy dedup or run manual ALTER TABLE … DELETE WHERE.";

pub struct ClickhouseSink {
    cfg: ClickhouseTargetConfig,
    backend: ClickhouseBackend,
    embed_dim: usize,
}

impl ClickhouseSink {
    pub fn new(cfg: ClickhouseTargetConfig, backend: ClickhouseBackend, embed_dim: usize) -> Self {
        if cfg.delete_orphans {
            DELETE_ORPHANS_WARNED.get_or_init(|| {
                warn!("{ORPHAN_WARN_MSG}");
            });
        }
        Self { cfg, backend, embed_dim }
    }

    fn fq(&self) -> String {
        self.backend.fq_table(&self.cfg.database_name, &self.cfg.table)
    }
}

/// Canonical chunkshop columns, CH-typed. Mirrors
/// python/src/chunkshop/sinks/clickhouse.py::_canonical_cols.
fn canonical_cols(_dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "Int32".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: "Array(String)".into(), nullable: false, default: Some("[]"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: "String".into(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: "Array(Float32)".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "String".into(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "DateTime64(6)".into(), nullable: false, default: Some("now64()"), is_primary_key: false },
    ]
}

/// PG type → CH type map for promoted columns. Mirrors
/// python/src/chunkshop/sinks/clickhouse.py::_PG_TO_CH_TYPE.
fn pg_type_to_ch(pg_type: &str) -> String {
    match pg_type {
        "text" => "String".into(),
        "text[]" => "Array(String)".into(),
        "int" => "Int32".into(),
        "bigint" => "Int64".into(),
        "boolean" => "UInt8".into(),
        "jsonb" => "String".into(),
        "timestamptz" => "DateTime64(6)".into(),
        "date" => "Date".into(),
        other => other.to_string(),
    }
}

/// Walk a dotted path through a JSON object. Returns None if any segment is
/// missing or not a JSON object. Mirrors Python's `_jsonb_path_get`.
fn jsonb_path_get<'a>(meta: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        cur = cur.as_object()?.get(seg)?;
    }
    Some(cur)
}

/// The row shape we feed `client.insert::<ChunkRow>(...)`. Field order MUST
/// match the canonical column order (id, doc_id, seq_num, original_content,
/// embedded_content, tags, metadata, embedding, source). created_at is handled
/// by the column DEFAULT (we don't write it). Promoted columns are NOT in this
/// struct because they're variable per config — write_document falls back to a
/// raw-SQL VALUES path when promote_metadata is non-empty.
#[derive(Row, Serialize)]
pub(crate) struct ChunkRow {
    pub id: String,
    pub doc_id: String,
    pub seq_num: i32,
    pub original_content: String,
    pub embedded_content: String,
    pub tags: Vec<String>,
    pub metadata: String,
    pub embedding: Vec<f32>,
    pub source: String,
}
