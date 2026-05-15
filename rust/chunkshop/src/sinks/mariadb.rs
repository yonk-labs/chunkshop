//! MariaDB sink — chunkshop data-model writer using `MariadbBackend` for dialect.
//!
//! Mirrors `python/src/chunkshop/sinks/mariadb.py`. Owns mode dispatch
//! (overwrite/append/create_if_missing), foreign-tag safety, append preflight,
//! `_ensure_promote_columns`, source write-once on UPDATE, delete_orphans, and
//! the canonical chunks-table column list.
//!
//! MariaDB-specific deltas vs `PgSink`:
//! - No `CREATE EXTENSION` (VECTOR is built-in since 11.7).
//! - All schema setup runs in one tx (atomicity goal).
//! - sqlx-mysql has no VECTOR adapter, so each row's INSERT SQL is composed
//!   per-row with the embedding column's value spliced in as a `VEC_FromText`
//!   literal. Other columns bind as `?` placeholders.
//! - `tags` and `metadata` bind as JSON-encoded strings (the column type is
//!   JSON; MariaDB infers from column type).
//! - Placeholders use `?` (not `$N`), no `::type` casts.

use std::future::Future;

use anyhow::{anyhow, Context, Result};
use sqlx::{MySql, MySqlPool, Row, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};
use crate::backends::mariadb::MariadbBackend;
use crate::chunker::Chunk;
use crate::config::{MariadbTargetConfig, PromoteColumn};
use crate::sinks::base::Sink;

pub struct MariadbSink {
    cfg: MariadbTargetConfig,
    backend: MariadbBackend,
    embed_dim: usize,
}

/// Traverse a dotted path through a JSON value. Returns `None` if any segment
/// is missing or an intermediate is not an object. Mirrors Python's
/// `_jsonb_path_get`. Chunkshop-specific dict navigation, not SQL — lives here
/// rather than on Backend.
fn jsonb_path_get<'a>(
    meta: &'a serde_json::Value,
    path: &str,
) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        let obj = cur.as_object()?;
        cur = obj.get(seg)?;
    }
    Some(cur)
}

/// PG-flavored `PromoteColumn.type_` → MariaDB column DDL type.
/// Mirrors Python's `_PG_TO_MARIADB_TYPE` dict in `sinks/mariadb.py`.
fn pg_type_to_mariadb(pg_type: &str) -> &str {
    match pg_type {
        "text" => "TEXT",
        "text[]" => "JSON",
        "int" => "INT",
        "bigint" => "BIGINT",
        "boolean" => "BOOLEAN",
        "jsonb" => "JSON",
        "timestamptz" => "TIMESTAMP",
        "date" => "DATE",
        other => other,
    }
}

/// The chunkshop-canonical chunks-table column list, MariaDB-typed via the
/// backend. Mirrors `_canonical_cols` in Python and `pg.rs::canonical_cols`.
fn canonical_cols<B: BackendDialect>(b: &B, dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec {
            name: "id",
            type_ddl: b.text_pk_type_ddl(),
            nullable: false,
            default: None,
            is_primary_key: true,
        },
        ColSpec {
            name: "doc_id",
            type_ddl: b.text_pk_type_ddl(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "seq_num",
            type_ddl: "INT".to_string(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "original_content",
            type_ddl: "LONGTEXT".to_string(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "embedded_content",
            type_ddl: "LONGTEXT".to_string(),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "tags",
            type_ddl: b.tags_array_type_ddl(),
            nullable: false,
            // MariaDB requires expression defaults to be wrapped in parens.
            default: Some("(JSON_ARRAY())"),
            is_primary_key: false,
        },
        ColSpec {
            name: "metadata",
            type_ddl: b.json_type_ddl(),
            nullable: false,
            default: Some("(JSON_OBJECT())"),
            is_primary_key: false,
        },
        ColSpec {
            name: "embedding",
            type_ddl: b.vector_type_ddl(dim),
            nullable: false,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "source",
            type_ddl: "VARCHAR(255)".to_string(),
            nullable: true,
            default: None,
            is_primary_key: false,
        },
        ColSpec {
            name: "created_at",
            type_ddl: "TIMESTAMP".to_string(),
            nullable: false,
            default: Some("CURRENT_TIMESTAMP"),
            is_primary_key: false,
        },
    ]
}

impl MariadbSink {
    pub fn new(cfg: MariadbTargetConfig, backend: MariadbBackend, embed_dim: usize) -> Self {
        Self {
            cfg,
            backend,
            embed_dim,
        }
    }

    fn fq(&self) -> String {
        self.backend
            .fq_table(&self.cfg.database_name, &self.cfg.table)
    }

    /// Inherent accessor — exposed for tests / introspection. Not on the Sink
    /// trait. Mirrors `PgSink::pool`.
    pub async fn pool(&self) -> Result<&MySqlPool> {
        self.backend.pool().await
    }

    // --- mode dispatch helpers (private). Ported from MariaDbSink (Python). ---

    async fn overwrite_create_in_tx(&self, tx: &mut Transaction<'_, MySql>) -> Result<()> {
        if self
            .backend
            .table_exists(tx, &self.cfg.database_name, &self.cfg.table)
            .await?
            && !self.cfg.force_overwrite
        {
            let stmt = format!(
                "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
                self.fq()
            );
            let rows = sqlx::query(&stmt).fetch_all(&mut **tx).await?;
            let existing: std::collections::BTreeSet<String> = rows
                .into_iter()
                .filter_map(|r| r.try_get::<String, _>("source").ok())
                .collect();
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {schema}.{table}: table holds rows with \
                     source_tag values {foreign:?} that differ from this cell's source_tag \
                     {my_tag:?}. Set target.force_overwrite: true in YAML to bypass.",
                    schema = self.cfg.database_name,
                    table = self.cfg.table,
                    foreign = foreign,
                    my_tag = my_tag,
                ));
            }
        }
        if self
            .backend
            .table_exists(tx, &self.cfg.database_name, &self.cfg.table)
            .await?
        {
            sqlx::query(&self.backend.drop_table_sql(&self.fq()))
                .execute(&mut **tx)
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl_in_tx(tx).await
    }

    async fn create_if_missing_in_tx(&self, tx: &mut Transaction<'_, MySql>) -> Result<()> {
        if !self
            .backend
            .table_exists(tx, &self.cfg.database_name, &self.cfg.table)
            .await?
        {
            return self.create_base_ddl_in_tx(tx).await;
        }
        sqlx::query(
            &self
                .backend
                .add_column_if_not_exists_sql(&self.fq(), "source", "VARCHAR(255)"),
        )
        .execute(&mut **tx)
        .await
        .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn append_preflight_in_tx(&self, tx: &mut Transaction<'_, MySql>) -> Result<()> {
        if !self
            .backend
            .table_exists(tx, &self.cfg.database_name, &self.cfg.table)
            .await?
        {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.database_name,
                self.cfg.table
            ));
        }
        let current_dim = self
            .backend
            .embedding_dim(tx, &self.cfg.database_name, &self.cfg.table)
            .await?;
        let Some(d) = current_dim else {
            return Err(anyhow!(
                "append mode: table {}.{} has no 'embedding' vector column. Not a chunkshop \
                 table — pick a different target or use mode='overwrite'.",
                self.cfg.database_name,
                self.cfg.table
            ));
        };
        if d != self.embed_dim {
            return Err(anyhow!(
                "append mode: target embedding dim is {d}, cell embedder dim is {own}. \
                 Vectors are not comparable. Use a different target or re-ingest into overwrite.",
                d = d,
                own = self.embed_dim,
            ));
        }
        sqlx::query(
            &self
                .backend
                .add_column_if_not_exists_sql(&self.fq(), "source", "VARCHAR(255)"),
        )
        .execute(&mut **tx)
        .await
        .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn ensure_promote_columns_in_tx(
        &self,
        tx: &mut Transaction<'_, MySql>,
    ) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            // pc.type_ is allowlisted in PromoteColumn::validate_type; map to
            // the MariaDB-side type name.
            let mariadb_type = pg_type_to_mariadb(&pc.type_);
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq(),
                &pc.column_name(),
                mariadb_type,
            );
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl_in_tx(&self, tx: &mut Transaction<'_, MySql>) -> Result<()> {
        let cols = canonical_cols(&self.backend, self.embed_dim);
        for stmt in
            self.backend
                .emit_chunks_table_ddl(&self.fq(), &cols, self.cfg.hnsw, self.embed_dim, None)
        {
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("emit_chunks_table_ddl statement")?;
        }
        self.ensure_promote_columns_in_tx(tx).await
    }
}

impl Sink for MariadbSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let mut tx = pool.begin().await.context("begin schema-setup tx")?;

            self.backend
                .acquire_create_lock(&mut tx, &self.cfg.database_name)
                .await?;

            // No CREATE EXTENSION step — VECTOR is built-in since MariaDB 11.7.
            sqlx::query(&self.backend.create_database_sql(&self.cfg.database_name))
                .execute(&mut *tx)
                .await
                .context("CREATE DATABASE")?;

            match self.cfg.mode.as_str() {
                "overwrite" => self.overwrite_create_in_tx(&mut tx).await?,
                "create_if_missing" => self.create_if_missing_in_tx(&mut tx).await?,
                "append" => self.append_preflight_in_tx(&mut tx).await?,
                other => return Err(anyhow!("unknown target.mode: {other:?}")),
            }
            tx.commit().await.context("commit schema-setup tx")?;
            Ok(())
        }
    }

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            if chunks.len() != embeddings.len() {
                return Err(anyhow!(
                    "chunks ({}) and embeddings ({}) length mismatch",
                    chunks.len(),
                    embeddings.len()
                ));
            }
            if chunks.len() != tags_per_chunk.len() {
                return Err(anyhow!(
                    "chunks ({}) and tags_per_chunk ({}) length mismatch",
                    chunks.len(),
                    tags_per_chunk.len()
                ));
            }
            if chunks.is_empty() {
                return Ok(());
            }

            let promote = &self.cfg.promote_metadata;
            let base_col_names: Vec<&str> = vec![
                "id",
                "doc_id",
                "seq_num",
                "original_content",
                "embedded_content",
                "tags",
                "metadata",
                "embedding",
                "source",
            ];
            let mut all_cols: Vec<String> =
                base_col_names.iter().map(|c| c.to_string()).collect();
            for pc in promote {
                all_cols.push(pc.column_name());
            }
            let cols_sql: String = all_cols
                .iter()
                .map(|c| self.backend.quote_ident(c))
                .collect::<Vec<_>>()
                .join(", ");

            // Update cols: skip id, doc_id, seq_num AND source (write-once on
            // ON DUPLICATE KEY UPDATE — same provenance contract as PG).
            let mut update_cols_owned: Vec<String> = vec![
                "original_content".into(),
                "embedded_content".into(),
                "tags".into(),
                "metadata".into(),
                "embedding".into(),
            ];
            for pc in promote {
                update_cols_owned.push(pc.column_name());
            }
            let update_refs: Vec<&str> = update_cols_owned.iter().map(|s| s.as_str()).collect();
            // Empty key list — MariaDB's upsert_clause ignores keys (ON
            // DUPLICATE KEY UPDATE keys off the table's PK, not an explicit list).
            let upsert = self.backend.upsert_clause(&[], &update_refs);

            let pool = self.backend.pool().await?;
            let mut tx = pool.begin().await?;

            // Build placeholders template once; substitute the embedding's
            // VEC_FromText(...) per row.
            let placeholders_template: Vec<String> = all_cols
                .iter()
                .map(|c| {
                    if c == "embedding" {
                        "__VEC_PLACEHOLDER__".to_string()
                    } else {
                        "?".to_string()
                    }
                })
                .collect();
            let vals_sql_template = placeholders_template.join(", ");

            for ((c, emb), tags) in chunks
                .iter()
                .zip(embeddings.iter())
                .zip(tags_per_chunk.iter())
            {
                let vec_lit = self.backend.vector_literal(emb);
                let vals_sql = vals_sql_template.replace("__VEC_PLACEHOLDER__", &vec_lit);
                let row_sql = format!(
                    "INSERT INTO {tbl} ({cols}) VALUES ({vals}) {upsert}",
                    tbl = self.fq(),
                    cols = cols_sql,
                    vals = vals_sql,
                    upsert = upsert,
                );

                let id = format!("{}::{}", c.doc_id, c.seq_num);
                let tags_json = serde_json::to_string(tags)
                    .context("serialize tags array to JSON")?;
                let meta_json = serde_json::to_string(&c.metadata)
                    .context("serialize chunk metadata to JSON")?;

                let mut q = sqlx::query(&row_sql)
                    .bind(id)
                    .bind(&c.doc_id)
                    .bind(c.seq_num as i32)
                    .bind(&c.original_content)
                    .bind(&c.embedded_content)
                    .bind(tags_json)
                    .bind(meta_json)
                    // embedding is INLINE — no bind for it.
                    .bind(self.cfg.source_tag.as_deref());

                for pc in promote {
                    q = q.bind(promote_value_for(&c.metadata, pc));
                }

                q.execute(&mut *tx).await.context("INSERT chunk row")?;
            }

            // delete_orphans: same-tx cleanup of stale chunks at higher seq_nums
            // when a doc shrinks. doc_id parameter is the canonical key.
            if self.cfg.delete_orphans {
                let new_count = chunks.len() as i64;
                let delete_sql = format!(
                    "DELETE FROM {tbl} WHERE doc_id = ? AND seq_num >= ?",
                    tbl = self.fq(),
                );
                sqlx::query(&delete_sql)
                    .bind(doc_id)
                    .bind(new_count)
                    .execute(&mut *tx)
                    .await
                    .context("DELETE orphan chunks")?;
            }
            tx.commit().await?;
            Ok(())
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let result = if let Some(tag) = &self.cfg.source_tag {
                let stmt = format!(
                    "DELETE FROM {tbl} WHERE doc_id = ? AND source = ?",
                    tbl = self.fq()
                );
                sqlx::query(&stmt).bind(doc_id).bind(tag).execute(pool).await?
            } else {
                let stmt = format!("DELETE FROM {tbl} WHERE doc_id = ?", tbl = self.fq());
                sqlx::query(&stmt).bind(doc_id).execute(pool).await?
            };
            Ok(result.rows_affected() as i64)
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let stmt = format!("SELECT COUNT(DISTINCT doc_id) FROM {}", self.fq());
            let row = sqlx::query(&stmt).fetch_one(pool).await?;
            // MariaDB returns COUNT(*) as BIGINT.
            Ok(row.get::<i64, _>(0))
        }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            // Vector literal goes INLINE — sqlx-mysql has no VECTOR adapter.
            let vec_lit = self.backend.vector_literal(query_vec);
            // MariaDB's VECTOR INDEX (HNSW) only accelerates VEC_DISTANCE_EUCLIDEAN,
            // not VEC_DISTANCE_COSINE. Since chunkshop's embeddings are L2-normalized
            // (fastembed BGE/Nomic all normalize), ranking by euclidean is
            // mathematically identical to ranking by cosine for unit vectors
            // (eucl² = 2·cos_dist). HNSW also makes the result approximate.
            // We use the hybrid shape: euclidean in ORDER BY (index-accelerated),
            // cosine in SELECT (reported distance matches the other 3 backends).
            // Drops query latency ~180× at 8k chunks. Requires target.hnsw: true
            // on the cell so the VECTOR INDEX exists.
            let stmt = format!(
                "SELECT doc_id, seq_num, VEC_DISTANCE_COSINE(embedding, {vec_lit}) AS distance \
                 FROM {tbl} \
                 ORDER BY VEC_DISTANCE_EUCLIDEAN(embedding, {vec_lit}) LIMIT ?",
                tbl = self.fq()
            );
            let rows = sqlx::query(&stmt)
                .bind(k as i64)
                .fetch_all(pool)
                .await?;
            Ok(rows
                .into_iter()
                .map(|r| {
                    let doc_id: String = r.get(0);
                    let seq_num: i32 = r.get(1);
                    // VEC_DISTANCE_COSINE returns DOUBLE; try f64 first then
                    // fall back to f32 on type mismatch (defensive — different
                    // MariaDB builds may return FLOAT).
                    let distance: f64 = match r.try_get::<f64, _>(2) {
                        Ok(v) => v,
                        Err(_) => r.try_get::<f32, _>(2).map(|f| f as f64).unwrap_or(0.0),
                    };
                    (doc_id, seq_num, distance)
                })
                .collect())
        }
    }
}

/// Project a chunk's metadata down to the right text representation for the
/// promoted column. MariaDB column types (TEXT, INT, BIGINT, BOOLEAN, JSON,
/// TIMESTAMP, DATE) infer from the column declaration — we just hand them a
/// string. JSON columns accept JSON-encoded text. Mirrors `pg.rs::promote_value_for`.
fn promote_value_for(metadata: &serde_json::Value, pc: &PromoteColumn) -> Option<String> {
    let v = jsonb_path_get(metadata, &pc.path)?;
    Some(match v {
        serde_json::Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    })
}
