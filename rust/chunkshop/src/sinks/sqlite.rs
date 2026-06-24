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
use crate::config::{validate_ident, SqliteTargetConfig};
use crate::sinks::base::{render_filter_value, Filters, Sink};

#[derive(Clone)]
pub struct SqliteSink {
    pub(crate) cfg: SqliteTargetConfig,
    pub(crate) backend: SQLiteBackend,
    pub(crate) embed_dim: usize,
}

/// Process-global "have we warned about hnsw=true on SQLite yet?" flag.
/// Mirrors Python's `_HNSW_WARNED` set keyed on PID — one warning per process.
static HNSW_WARNED_ONCE: OnceLock<()> = OnceLock::new();

fn jsonb_path_get<'a>(meta: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        let obj = cur.as_object()?;
        cur = obj.get(seg)?;
    }
    Some(cur)
}

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
        ColSpec {
            name: "id",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: None,
            is_primary_key: true,
        },
        ColSpec {
            name: "doc_id",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "seq_num",
            type_ddl: "INTEGER".into(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "original_content",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "embedded_content",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "tags",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: Some("'[]'"),
            is_primary_key: false,
        },
        ColSpec {
            name: "metadata",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: Some("'{}'"),
            is_primary_key: false,
        },
        ColSpec {
            name: "embedding",
            type_ddl: format!("FLOAT[{dim}]"),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "source",
            type_ddl: "TEXT".into(),
            nullable: true,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "created_at",
            type_ddl: "TEXT".into(),
            nullable: false,
            default: Some("CURRENT_TIMESTAMP"),
            is_primary_key: false,
        },
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
        Self {
            cfg,
            backend,
            embed_dim,
        }
    }

    fn fq_main(&self) -> String {
        self.backend
            .fq_table(&self.cfg.database_name, &self.cfg.table)
    }
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
            &self.fq_main(),
            &canonical_cols(self.embed_dim),
            self.cfg.hnsw,
            self.embed_dim,
            None,
            None,
        ) {
            conn.execute_batch(&stmt)
                .with_context(|| format!("ddl: {stmt}"))?;
        }
        self.ensure_promote_columns(conn)?;
        Ok(())
    }

    fn ensure_promote_columns(&self, conn: &rusqlite::Connection) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq_main(),
                &pc.column_name(),
                pg_type_to_sqlite(&pc.type_),
            );
            match conn.execute_batch(&stmt) {
                Ok(()) => {}
                Err(e) => {
                    let m = e.to_string().to_lowercase();
                    if m.contains("duplicate column") {
                        continue;
                    }
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
                    "overwrite refuses to drop {table}: table holds rows with source_tag \
                     values {foreign:?} that differ from this cell's source_tag {my_tag:?}. \
                     Set target.force_overwrite: true in YAML to bypass.",
                    table = self.cfg.table,
                    foreign = foreign,
                    my_tag = my_tag,
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
            &self.fq_main(),
            "source",
            "TEXT",
        )) {
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
                "append mode: target dim {d} != cell embed_dim {}",
                self.embed_dim
            ));
        }
        // Ensure source column + promote columns.
        match conn.execute_batch(&self.backend.add_column_if_not_exists_sql(
            &self.fq_main(),
            "source",
            "TEXT",
        )) {
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
        Ok(re
            .captures(&sql)
            .and_then(|c| c.get(1))
            .and_then(|m| m.as_str().parse().ok()))
    }

    fn write_document_in_tx(
        &self,
        tx: &rusqlite::Transaction<'_>,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> Result<()> {
        let promote = &self.cfg.promote_metadata;
        // Main table cols (no embedding).
        let mut main_col_names: Vec<String> = vec![
            "id".into(),
            "doc_id".into(),
            "seq_num".into(),
            "original_content".into(),
            "embedded_content".into(),
            "tags".into(),
            "metadata".into(),
            "source".into(),
        ];
        for pc in promote {
            main_col_names.push(pc.column_name());
        }
        let mut update_cols: Vec<&str> =
            vec!["original_content", "embedded_content", "tags", "metadata"];
        // Source excluded from update — write-once.
        let promoted_names: Vec<String> = promote.iter().map(|pc| pc.column_name()).collect();
        for n in &promoted_names {
            update_cols.push(n.as_str());
        }
        let upsert = self.backend.upsert_clause(&["id"], &update_cols);
        let cols_sql: String = main_col_names
            .iter()
            .map(|c| self.backend.quote_ident(c))
            .collect::<Vec<_>>()
            .join(", ");
        let placeholders: String = std::iter::repeat("?")
            .take(main_col_names.len())
            .collect::<Vec<_>>()
            .join(", ");
        let main_stmt = format!(
            "INSERT INTO {tbl} ({cols_sql}) VALUES ({placeholders}) {upsert}",
            tbl = self.fq_main()
        );

        // vec0 — DELETE-by-id then INSERT (vec0 refuses UPSERT and INSERT OR REPLACE).
        let vec_delete = format!("DELETE FROM {} WHERE id = ?", self.fq_vec());
        let vec_insert = format!(
            "INSERT INTO {} (id, embedding) VALUES (?, ?)",
            self.fq_vec()
        );

        let mut main_q = tx.prepare(&main_stmt).context("prepare main upsert")?;
        let mut vec_del_q = tx.prepare(&vec_delete).context("prepare vec delete")?;
        let mut vec_ins_q = tx.prepare(&vec_insert).context("prepare vec insert")?;

        for (i, c) in chunks.iter().enumerate() {
            let id = format!("{}::{}", c.doc_id, c.seq_num);
            let tags_lit = serde_json::to_string(&tags_per_chunk[i])?;
            let meta_lit = serde_json::to_string(&c.metadata)?;
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![
                Box::new(id.clone()),
                Box::new(c.doc_id.clone()),
                Box::new(c.seq_num as i64),
                Box::new(c.original_content.clone()),
                Box::new(c.embedded_content.clone()),
                Box::new(tags_lit),
                Box::new(meta_lit),
                Box::new(self.cfg.source_tag.clone()),
            ];
            for pc in promote {
                let v = jsonb_path_get(&c.metadata, &pc.path);
                let s: Option<String> = v.map(|val| match val {
                    serde_json::Value::String(s) => s.clone(),
                    other => serde_json::to_string(other).unwrap_or_default(),
                });
                params.push(Box::new(s));
            }
            let p_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
            main_q
                .execute(p_refs.as_slice())
                .context("upsert main row")?;

            // vec table
            vec_del_q
                .execute(rusqlite::params![id])
                .context("delete vec")?;
            let vec_lit = self.backend.vector_literal(&embeddings[i]);
            vec_ins_q
                .execute(rusqlite::params![id, vec_lit])
                .context("insert vec")?;
        }

        if self.cfg.delete_orphans {
            drop(main_q);
            drop(vec_del_q);
            drop(vec_ins_q);
            let n_new = chunks.len() as i64;
            tx.execute(
                &format!(
                    "DELETE FROM {} WHERE doc_id = ? AND seq_num >= ?",
                    self.fq_main()
                ),
                rusqlite::params![doc_id, n_new],
            )
            .context("delete orphans main")?;
            // Vec table: id format is `doc_id::seq_num`. Match by LIKE + parse seq.
            tx.execute(
                &format!(
                    "DELETE FROM {} WHERE id LIKE ? || '::%' \
                     AND CAST(substr(id, instr(id, '::') + 2) AS INTEGER) >= ?",
                    self.fq_vec()
                ),
                rusqlite::params![doc_id, n_new],
            )
            .context("delete orphans vec")?;
        }
        Ok(())
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
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        let this = self.clone();
        let doc_id = doc_id.to_string();
        let chunks = chunks.to_vec();
        let embeddings = embeddings.to_vec();
        let tags_per_chunk = tags_per_chunk.to_vec();
        async move {
            if chunks.len() != embeddings.len() || chunks.len() != tags_per_chunk.len() {
                return Err(anyhow!(
                    "chunks/embeddings/tags length mismatch: {} / {} / {}",
                    chunks.len(),
                    embeddings.len(),
                    tags_per_chunk.len()
                ));
            }
            if chunks.is_empty() {
                return Ok(());
            }

            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<()> {
                let mut g = conn.blocking_lock();
                let tx = g.transaction().context("begin tx")?;
                this.write_document_in_tx(&tx, &doc_id, &chunks, &embeddings, &tags_per_chunk)?;
                tx.commit().context("commit tx")?;
                Ok(())
            })
            .await
            .context("spawn_blocking write_document")?
        }
    }
    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        let this = self.clone();
        let doc_id = doc_id.to_string();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<i64> {
                let mut g = conn.blocking_lock();
                let tx = g.transaction().context("begin tx")?;
                // Two-phase: SELECT ids first, then DELETE both tables by id IN (...).
                let ids: Vec<String> = {
                    let stmt = if this.cfg.source_tag.is_some() {
                        format!(
                            "SELECT id FROM {} WHERE doc_id = ? AND source = ?",
                            this.fq_main()
                        )
                    } else {
                        format!("SELECT id FROM {} WHERE doc_id = ?", this.fq_main())
                    };
                    let mut q = tx.prepare(&stmt)?;
                    let rows: rusqlite::Result<Vec<String>> =
                        if let Some(tag) = &this.cfg.source_tag {
                            q.query_map(rusqlite::params![doc_id, tag], |r| r.get(0))?
                                .collect()
                        } else {
                            q.query_map(rusqlite::params![doc_id], |r| r.get(0))?
                                .collect()
                        };
                    rows.context("collect ids to delete")?
                };
                if ids.is_empty() {
                    tx.commit()?;
                    return Ok(0);
                }
                let placeholders: String = std::iter::repeat("?")
                    .take(ids.len())
                    .collect::<Vec<_>>()
                    .join(",");
                let main_del = format!(
                    "DELETE FROM {} WHERE id IN ({placeholders})",
                    this.fq_main()
                );
                let vec_del = format!("DELETE FROM {} WHERE id IN ({placeholders})", this.fq_vec());
                let p: Vec<&dyn rusqlite::ToSql> =
                    ids.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
                let n = tx.execute(&main_del, p.as_slice()).context("delete main")? as i64;
                tx.execute(&vec_del, p.as_slice()).context("delete vec")?;
                tx.commit()?;
                Ok(n)
            })
            .await
            .context("spawn_blocking delete_document")?
        }
    }
    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        let this = self.clone();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<i64> {
                let g = conn.blocking_lock();
                let n: i64 = g
                    .query_row(
                        &format!("SELECT COUNT(DISTINCT doc_id) FROM {}", this.fq_main()),
                        [],
                        |r| r.get(0),
                    )
                    .context("count_docs")?;
                Ok(n)
            })
            .await
            .context("spawn_blocking count_docs")?
        }
    }
    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        let this = self.clone();
        let q_owned = query_vec.to_vec();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<Vec<(String, i32, f64)>> {
                let g = conn.blocking_lock();
                let vec_lit = this.backend.vector_literal(&q_owned);
                let stmt = format!(
                    "SELECT c.doc_id, c.seq_num, v.distance \
                     FROM {vec} v JOIN {main} c ON c.id = v.id \
                     WHERE v.embedding MATCH ? AND k = ? \
                     ORDER BY v.distance",
                    vec = this.fq_vec(),
                    main = this.fq_main()
                );
                let mut q = g.prepare(&stmt).context("prepare top_k")?;
                let rows = q
                    .query_map(rusqlite::params![vec_lit, k as i64], |r| {
                        Ok((
                            r.get::<_, String>(0)?,
                            r.get::<_, i32>(1)?,
                            r.get::<_, f64>(2)?,
                        ))
                    })
                    .context("query top_k")?;
                let out: rusqlite::Result<Vec<_>> = rows.collect();
                Ok(out.context("collect top_k rows")?)
            })
            .await
            .context("spawn_blocking query_top_k")?
        }
    }

    /// Scoped top-k (#75): sqlite-vec's `MATCH ... AND k = ?` can't take an
    /// arbitrary WHERE inside the vec scan, so over-fetch (k*4) then apply the
    /// AND-filter on the joined `c` alias and truncate to `k`. Mirrors Python
    /// `search_sqlite.py::semantic_search` + `_build_where`.
    fn query_top_k_filtered(
        &self,
        query_vec: &[f32],
        k: usize,
        filter: Option<&Filters>,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        let this = self.clone();
        let q_owned = query_vec.to_vec();
        // Build the WHERE fragment + owned params before the blocking section.
        let built = match filter {
            Some(f) if !f.is_empty() => Some(sqlite_build_where(f, &this.backend)),
            _ => None,
        };
        async move {
            // None / empty filter is identical to the unfiltered path.
            let built = match built {
                Some(r) => r?,
                None => return this.query_top_k(&q_owned, k).await,
            };
            let (where_sql, filter_params) = built;
            // Over-fetch so post-join filtering still yields a full top-k.
            let fetch_k = k.saturating_mul(4).max(k) as i64;
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<Vec<(String, i32, f64)>> {
                let g = conn.blocking_lock();
                let vec_lit = this.backend.vector_literal(&q_owned);
                let stmt = format!(
                    "SELECT c.doc_id, c.seq_num, v.distance \
                     FROM {vec} v JOIN {main} c ON c.id = v.id \
                     WHERE v.embedding MATCH ? AND k = ? AND {where_sql} \
                     ORDER BY v.distance",
                    vec = this.fq_vec(),
                    main = this.fq_main()
                );
                let mut q = g.prepare(&stmt).context("prepare top_k_filtered")?;
                // params: vec_lit, fetch_k, then the filter params in order.
                let mut all: Vec<&dyn rusqlite::ToSql> =
                    Vec::with_capacity(2 + filter_params.len());
                all.push(&vec_lit);
                all.push(&fetch_k);
                for b in &filter_params {
                    all.push(b.as_ref());
                }
                let rows = q
                    .query_map(all.as_slice(), |r| {
                        Ok((
                            r.get::<_, String>(0)?,
                            r.get::<_, i32>(1)?,
                            r.get::<_, f64>(2)?,
                        ))
                    })
                    .context("query top_k_filtered")?;
                let mut out: Vec<(String, i32, f64)> =
                    rows.collect::<rusqlite::Result<_>>().context("collect rows")?;
                out.truncate(k);
                Ok(out)
            })
            .await
            .context("spawn_blocking query_top_k_filtered")?
        }
    }
}

/// Build a parameterized WHERE conjunction (no leading WHERE) over the `c`
/// alias from a [`Filters`]. All values are bound params; column NAMES are
/// allowlist-validated so they can be safely quoted into the SQL.
#[allow(clippy::type_complexity)]
fn sqlite_build_where(
    filter: &Filters,
    backend: &SQLiteBackend,
) -> Result<(String, Vec<Box<dyn rusqlite::ToSql + Send>>)> {
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<Box<dyn rusqlite::ToSql + Send>> = Vec::new();

    if let Some(src) = &filter.source {
        clauses.push("c.source = ?".into());
        params.push(Box::new(src.clone()));
    }

    if !filter.tags.is_empty() {
        // tags stored as a JSON array of lowercased strings; overlap = any
        // query tag present, case-insensitive (query side casefolded).
        let placeholders = vec!["?"; filter.tags.len()].join(", ");
        clauses.push(format!(
            "EXISTS (SELECT 1 FROM json_each(c.tags) je WHERE lower(je.value) IN ({placeholders}))"
        ));
        for t in &filter.tags {
            params.push(Box::new(t.to_lowercase()));
        }
    }

    if !filter.metadata.is_empty() {
        // No jsonb-containment operator in SQLite: per-key equality on
        // top-level keys (covers flat metadata; nested containment unsupported).
        for (key, val) in &filter.metadata {
            clauses.push("json_extract(c.metadata, '$.' || ?) = ?".into());
            params.push(Box::new(key.clone()));
            params.push(value_to_sql(val));
        }
    }

    for (col, val) in &filter.columns {
        validate_ident(col, "filter column")?;
        clauses.push(format!("{} = ?", backend.quote_ident(col)));
        // Promoted columns are stored as text; compare against the same text.
        params.push(Box::new(render_filter_value(val)));
    }

    Ok((clauses.join(" AND "), params))
}

/// Bind a JSON metadata value with its native SQLite type so it compares equal
/// to what `json_extract` returns (which is typed). Strings/numbers/bools map
/// to their SQLite equivalents; anything else falls back to text.
fn value_to_sql(v: &serde_json::Value) -> Box<dyn rusqlite::ToSql + Send> {
    use serde_json::Value;
    match v {
        Value::String(s) => Box::new(s.clone()),
        Value::Bool(b) => Box::new(*b as i64),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Box::new(i)
            } else if let Some(f) = n.as_f64() {
                Box::new(f)
            } else {
                Box::new(n.to_string())
            }
        }
        other => Box::new(other.to_string()),
    }
}
