//! Postgres sink — chunkshop data-model writer using `PostgresBackend` for dialect.
//!
//! Mirrors `python/src/chunkshop/sinks/pg.py`. Owns mode dispatch
//! (overwrite/append/create_if_missing), foreign-tag safety, append preflight,
//! `_ensure_promote_columns`, source write-once on UPDATE, delete_orphans, and
//! the canonical chunks-table column list.

use std::future::Future;

use anyhow::{anyhow, Context, Result};
use sqlx::{PgPool, Postgres, Row, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};
use crate::backends::postgres::PostgresBackend;
use crate::chunker::Chunk;
use crate::config::{PostgresTargetConfig, PromoteColumn};
use crate::sinks::base::Sink;

pub struct PgSink {
    cfg: PostgresTargetConfig,
    backend: PostgresBackend,
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

/// The chunkshop-canonical chunks-table column list, PG-typed via the backend.
fn canonical_cols<B: BackendDialect>(b: &B, dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: b.text_pk_type_ddl(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: b.text_pk_type_ddl(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "int".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "text".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "text".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: b.tags_array_type_ddl(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: b.json_type_ddl(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: b.vector_type_ddl(dim), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "text".to_string(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "timestamptz".to_string(), nullable: false, default: Some("now()"), is_primary_key: false },
    ]
}

impl PgSink {
    pub fn new(cfg: PostgresTargetConfig, backend: PostgresBackend, embed_dim: usize) -> Self {
        Self { cfg, backend, embed_dim }
    }

    fn fq(&self) -> String {
        self.backend.fq_table(&self.cfg.database_name, &self.cfg.table)
    }

    /// Inherent accessor — used by `Pipeline::sample_row` (demo helper).
    /// NOT on the Sink trait; for v0.4.0 PG-only usage. Removed when Pipeline
    /// stops using raw pool access (separate cleanup task, post-R1).
    pub async fn pool(&self) -> Result<&PgPool> {
        self.backend.pool().await
    }

    // --- mode dispatch helpers (private). Ported from PgVectorSink. ---

    async fn overwrite_create_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await?
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
        if self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            sqlx::query(&self.backend.drop_table_sql(&self.fq()))
                .execute(&mut **tx)
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl_in_tx(tx).await
    }

    async fn create_if_missing_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            return self.create_base_ddl_in_tx(tx).await;
        }
        sqlx::query(&self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "text"))
            .execute(&mut **tx)
            .await
            .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn append_preflight_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.database_name,
                self.cfg.table
            ));
        }
        let current_dim = self.backend.embedding_dim(tx, &self.cfg.database_name, &self.cfg.table).await?;
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
        sqlx::query(&self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "text"))
            .execute(&mut **tx)
            .await
            .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn ensure_promote_columns_in_tx(
        &self,
        tx: &mut Transaction<'_, Postgres>,
    ) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            // pc.type_ is allowlisted in PromoteColumn::validate_type.
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq(),
                &pc.column_name(),
                &pc.type_,
            );
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        let cols = canonical_cols(&self.backend, self.embed_dim);
        for stmt in self.backend.emit_chunks_table_ddl(&self.fq(), &cols, self.cfg.hnsw, self.embed_dim, None) {
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("emit_chunks_table_ddl statement")?;
        }
        self.ensure_promote_columns_in_tx(tx).await
    }
}

impl Sink for PgSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let mut tx = pool.begin().await.context("begin schema-setup tx")?;

            self.backend
                .acquire_create_lock(&mut tx, &self.cfg.database_name)
                .await?;

            sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
                .execute(&mut *tx)
                .await
                .context("CREATE EXTENSION vector")?;

            sqlx::query(&self.backend.create_database_sql(&self.cfg.database_name))
                .execute(&mut *tx)
                .await
                .context("CREATE SCHEMA")?;

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
        _doc_id: &str,
        _chunks: &[Chunk],
        _embeddings: &[Vec<f32>],
        _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { unimplemented!("Task 15") }
    }

    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn query_top_k(
        &self,
        _query_vec: &[f32],
        _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { unimplemented!("Task 16") }
    }
}
