//! SQLite sink — chunks-table writer with two-table layout (chunks + chunks_vec).
//!
//! Mirrors `python/src/chunkshop/sinks/sqlite.py`. The embedding column lives
//! in a `vec0` virtual table joined on `id`. The Sink owns the two-table
//! dance: every `write_document` writes both atomically; every
//! `delete_orphans` deletes from both. `vec0` virtual tables refuse UPSERT
//! and INSERT OR REPLACE — the working pattern is DELETE-by-id then INSERT.

use std::collections::BTreeSet;
use std::future::Future;
use std::sync::OnceLock;

use anyhow::{anyhow, Context, Result};

use crate::backends::base::{BackendDialect, ColSpec};
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

/// Process-global "have we warned about hnsw=true on SQLite yet?" flag.
/// Mirrors Python's `_HNSW_WARNED` set keyed on PID — one warning per process.
static HNSW_WARNED_ONCE: OnceLock<()> = OnceLock::new();

/// Map PG type names to SQLite equivalents for promote_metadata columns.
/// Mirrors Python's `_SQLITE_TYPE` dict in sinks/sqlite.py.
fn pg_type_to_sqlite(pg_type: &str) -> &str {
    match pg_type {
        "text" | "text[]" | "jsonb" | "timestamptz" | "date" => "TEXT",
        "int" | "bigint" | "boolean" => "INTEGER",
        other => other,
    }
}

/// Canonical chunks-table column list INCLUDING embedding — emit_chunks_table_ddl
/// splits the embedding column out into the vec0 partner table.
fn canonical_cols(dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "INTEGER".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: "TEXT".into(), nullable: false, default: Some("'[]'"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: "TEXT".into(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: format!("FLOAT[{dim}]"), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "TEXT".into(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "TEXT".into(), nullable: false, default: Some("CURRENT_TIMESTAMP"), is_primary_key: false },
    ]
}

impl SqliteSink {
    pub fn new(cfg: SqliteTargetConfig, backend: SQLiteBackend, embed_dim: usize) -> Self {
        if cfg.hnsw {
            // Warn once per process. Subsequent SqliteSink instances built with
            // hnsw=true do NOT re-warn.
            if HNSW_WARNED_ONCE.set(()).is_ok() {
                tracing::warn!(
                    "target.hnsw=true on SQLite is a no-op — sqlite-vec uses brute-force KNN. \
                     Querying with `embedding MATCH '[...]' AND k = N` works without an index."
                );
            }
        }
        Self { cfg, backend, embed_dim }
    }

    fn fq_main(&self) -> String { self.backend.fq_table(&self.cfg.database_name, &self.cfg.table) }
    fn fq_vec(&self) -> String {
        let vec_table = format!("{}_vec", self.cfg.table);
        self.backend.fq_table(&self.cfg.database_name, &vec_table)
    }

    /// Create + run all DDL statements (main table, doc_seq index, vec0 virtual
    /// table) PLUS any promote_metadata ALTER TABLE statements on the main
    /// table. Idempotent — uses CREATE TABLE IF NOT EXISTS / CREATE VIRTUAL
    /// TABLE IF NOT EXISTS / catches duplicate-column errors.
    fn create_base_ddl(&self, conn: &rusqlite::Connection) -> Result<()> {
        for stmt in self.backend.emit_chunks_table_ddl(
            &self.fq_main(), &canonical_cols(self.embed_dim),
            self.cfg.hnsw, self.embed_dim, None,
        ) {
            conn.execute_batch(&stmt).with_context(|| format!("ddl: {stmt}"))?;
        }
        self.ensure_promote_columns(conn)?;
        Ok(())
    }

    fn ensure_promote_columns(&self, conn: &rusqlite::Connection) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq_main(), &pc.column_name(), pg_type_to_sqlite(&pc.type_),
            );
            match conn.execute_batch(&stmt) {
                Ok(()) => {}
                Err(e) => {
                    let m = e.to_string().to_lowercase();
                    if m.contains("duplicate column") { continue; }
                    return Err(anyhow!("ADD COLUMN promote_metadata: {e}"));
                }
            }
        }
        Ok(())
    }

    fn table_exists_sync(&self, conn: &rusqlite::Connection, table: &str) -> bool {
        let r: Option<i32> = conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
                rusqlite::params![table],
                |row| row.get(0),
            )
            .ok();
        r.is_some()
    }

    fn overwrite_create(&self, conn: &rusqlite::Connection) -> Result<()> {
        // Foreign-tag refuse: when the table exists and force_overwrite=false,
        // refuse to drop if any rows belong to a different source_tag.
        if self.table_exists_sync(conn, &self.cfg.table) && !self.cfg.force_overwrite {
            let stmt = format!(
                "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
                self.fq_main()
            );
            let mut q = conn.prepare(&stmt)?;
            let existing: BTreeSet<String> = q
                .query_map([], |r| r.get::<_, String>(0))?
                .filter_map(|r| r.ok())
                .collect();
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {table}: foreign source_tag {foreign:?}",
                    table = self.cfg.table, foreign = foreign,
                ));
            }
        }
        if self.table_exists_sync(conn, &self.cfg.table) {
            conn.execute_batch(&self.backend.drop_table_sql(&self.fq_main()))
                .context("drop main")?;
            conn.execute_batch(&format!("DROP TABLE IF EXISTS {}", self.fq_vec()))
                .context("drop vec")?;
        }
        self.create_base_ddl(conn)
    }

    fn create_database_noop(&self, conn: &rusqlite::Connection) -> Result<()> {
        // SELECT 1 noop on SQLite — emit anyway for symmetry with PG's CREATE SCHEMA.
        conn.execute_batch(&self.backend.create_database_sql(&self.cfg.database_name))?;
        Ok(())
    }

    fn create_if_missing(&self, conn: &rusqlite::Connection) -> Result<()> {
        if !self.table_exists_sync(conn, &self.cfg.table) {
            return self.create_base_ddl(conn);
        }
        // Idempotent ADD COLUMN source — catch duplicate-column.
        match conn.execute_batch(&self.backend.add_column_if_not_exists_sql(
            &self.fq_main(), "source", "TEXT")) {
            Ok(()) => {}
            Err(e) => {
                let m = e.to_string().to_lowercase();
                if !m.contains("duplicate column") {
                    return Err(anyhow!("ADD COLUMN source: {e}"));
                }
            }
        }
        self.ensure_promote_columns(conn)
    }

    fn append_preflight(&self, conn: &rusqlite::Connection) -> Result<()> {
        if !self.table_exists_sync(conn, &self.cfg.table) {
            return Err(anyhow!(
                "append mode: table {} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.table
            ));
        }
        let current_dim = self.read_embedding_dim_sync(conn)?;
        let Some(d) = current_dim else {
            return Err(anyhow!(
                "append mode: {} has no vec0 partner table — not a chunkshop table.",
                self.cfg.table
            ));
        };
        if d != self.embed_dim {
            return Err(anyhow!(
                "append mode: target dim {d} != cell embed_dim {}", self.embed_dim
            ));
        }
        // Ensure source column + promote columns.
        match conn.execute_batch(&self.backend.add_column_if_not_exists_sql(
            &self.fq_main(), "source", "TEXT")) {
            Ok(()) => {}
            Err(e) => {
                let m = e.to_string().to_lowercase();
                if !m.contains("duplicate column") {
                    return Err(anyhow!("ADD COLUMN source: {e}"));
                }
            }
        }
        self.ensure_promote_columns(conn)
    }

    fn read_embedding_dim_sync(&self, conn: &rusqlite::Connection) -> Result<Option<usize>> {
        let vec_table = format!("{}_vec", self.cfg.table);
        let sql: Option<String> = conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                rusqlite::params![vec_table],
                |row| row.get(0),
            )
            .ok();
        let Some(sql) = sql else { return Ok(None) };
        let re = regex::Regex::new(r"(?i)FLOAT\[(\d+)\]").unwrap();
        Ok(re.captures(&sql)
            .and_then(|c| c.get(1))
            .and_then(|m| m.as_str().parse().ok()))
    }
}

impl Sink for SqliteSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        let this = self.clone();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<()> {
                let g = conn.blocking_lock();
                this.create_database_noop(&g)?;
                match this.cfg.mode.as_str() {
                    "overwrite" => this.overwrite_create(&g)?,
                    "create_if_missing" => this.create_if_missing(&g)?,
                    "append" => this.append_preflight(&g)?,
                    other => return Err(anyhow!("unknown target.mode: {other:?}")),
                }
                Ok(())
            })
            .await
            .context("spawn_blocking create_table")?
        }
    }
    // The other 4 trait methods stay as the stub-error returns until later
    // tasks implement them.
    fn write_document(
        &self, _doc_id: &str, _chunks: &[Chunk],
        _embeddings: &[Vec<f32>], _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow!("write_document not yet implemented")) }
    }
    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("delete_document not yet implemented")) }
    }
    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("count_docs not yet implemented")) }
    }
    fn query_top_k(
        &self, _query_vec: &[f32], _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { Err(anyhow!("query_top_k not yet implemented")) }
    }
}
