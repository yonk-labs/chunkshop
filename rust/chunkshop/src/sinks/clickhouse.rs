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

impl ClickhouseSink {
    async fn ensure_promote_columns(&self, client: &Client) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            let ch_type = pg_type_to_ch(&pc.type_);
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq(),
                &pc.column_name(),
                &ch_type,
            );
            client.query(&stmt).execute().await.context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl(&self, client: &Client) -> Result<()> {
        let cols = canonical_cols(self.embed_dim);
        let engine = self.cfg.engine.as_deref();
        for stmt in self.backend.emit_chunks_table_ddl(
            &self.fq(),
            &cols,
            self.cfg.hnsw,
            self.embed_dim,
            engine,
        ) {
            client.query(&stmt).execute().await.context("emit_chunks_table_ddl statement")?;
        }
        self.ensure_promote_columns(client).await
    }

    async fn overwrite_create(&self, client: &Client) -> Result<()> {
        let exists = self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await?;
        if exists && !self.cfg.force_overwrite {
            // Foreign-tag safety: refuse to drop a table holding rows from a
            // different source_tag.
            #[derive(Row, serde::Deserialize)]
            struct SourceRow {
                source: String,
            }
            let q = format!(
                "SELECT DISTINCT source FROM {} WHERE source != '' LIMIT 10",
                self.fq()
            );
            let mut cur = client.query(&q).fetch::<SourceRow>()?;
            let mut existing = std::collections::BTreeSet::new();
            while let Some(r) = cur.next().await? {
                existing.insert(r.source);
            }
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {db}.{tbl}: foreign source_tag values {foreign:?}. \
                     Set target.force_overwrite: true to bypass.",
                    db = self.cfg.database_name,
                    tbl = self.cfg.table,
                    foreign = foreign
                ));
            }
        }
        if exists {
            client
                .query(&self.backend.drop_table_sql(&self.fq()))
                .execute()
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl(client).await
    }

    async fn create_if_missing(&self, client: &Client) -> Result<()> {
        if !self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await? {
            return self.create_base_ddl(client).await;
        }
        let stmt = self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "String");
        client.query(&stmt).execute().await.context("ADD COLUMN source")?;
        self.ensure_promote_columns(client).await
    }

    async fn append_preflight(&self, client: &Client) -> Result<()> {
        if !self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await? {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.database_name,
                self.cfg.table
            ));
        }
        let current_dim = self
            .backend
            .embedding_dim(client, &self.cfg.database_name, &self.cfg.table)
            .await?;
        match current_dim {
            None => {
                warn!(
                    "append mode on empty CH table — cannot verify embedding dim matches. \
                     Continuing on faith; subsequent reads with mismatched dim will produce \
                     garbage cosine distances."
                );
            }
            Some(d) if d != self.embed_dim => {
                return Err(anyhow!(
                    "append mode: target embedding dim is {d}, cell's embedder dim is {own}. \
                     Vectors are not comparable.",
                    own = self.embed_dim
                ));
            }
            _ => {}
        }
        let stmt = self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "String");
        client.query(&stmt).execute().await.context("ADD COLUMN source")?;
        self.ensure_promote_columns(client).await
    }

    pub async fn create_table_impl(&self) -> Result<()> {
        let client = self.backend.client().await?;
        self.backend.with_create_lock(&client, &self.cfg.database_name).await?;
        client
            .query(&self.backend.create_database_sql(&self.cfg.database_name))
            .execute()
            .await
            .context("CREATE DATABASE")?;
        match self.cfg.mode.as_str() {
            "overwrite" => self.overwrite_create(&client).await,
            "create_if_missing" => self.create_if_missing(&client).await,
            "append" => self.append_preflight(&client).await,
            other => Err(anyhow!("unknown target.mode: {other:?}")),
        }
    }

    /// Append-only document write. Mirrors Python's
    /// `ClickHouseSink.write_document` (typed bulk insert via
    /// `clickhouse-connect.client.insert(...)`). The fast path uses the
    /// official `clickhouse` crate's `Insert<ChunkRow>` for binary RowBinary
    /// streaming. The promote-metadata branch falls back to per-row INSERTs
    /// because the typed Insert can't carry variable extra columns.
    ///
    /// CH semantics (different from PG):
    /// * No upsert. Re-ingesting `(doc_id, seq_num)` produces duplicate rows
    ///   on the default `MergeTree` engine; users opt into
    ///   `ReplacingMergeTree(created_at)` for lazy dedup at merge time.
    /// * `delete_orphans=true` is a no-op (warned at sink construction —
    ///   see `DELETE_ORPHANS_WARNED`).
    pub async fn write_document_impl(
        &self,
        _doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> Result<()> {
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
        let client = self.backend.client().await?;

        if promote.is_empty() {
            // Fast path: typed bulk insert via the official driver's Insert<T>.
            // NOTE: `Client::insert` re-escapes its argument with backticks, but
            // `self.fq()` is already `\`db\`.\`table\`` — using `insert_unescaped`
            // avoids a double-escape that would produce one giant identifier.
            let mut insert = client.insert_unescaped::<ChunkRow>(&self.fq()).await?;
            for ((c, emb), tags) in chunks
                .iter()
                .zip(embeddings.iter())
                .zip(tags_per_chunk.iter())
            {
                let row = ChunkRow {
                    id: format!("{}::{}", c.doc_id, c.seq_num),
                    doc_id: c.doc_id.clone(),
                    seq_num: c.seq_num as i32,
                    original_content: c.original_content.clone(),
                    embedded_content: c.embedded_content.clone(),
                    tags: tags.clone(),
                    metadata: serde_json::to_string(&c.metadata)?,
                    embedding: emb.clone(),
                    source: self.cfg.source_tag.clone().unwrap_or_default(),
                };
                insert.write(&row).await?;
            }
            insert.end().await?;
        } else {
            // Promoted-metadata path: typed Insert doesn't carry variable extra
            // columns, so we issue per-row INSERTs with parameter binding.
            // Slower, but the promote path is operator-opt-in and per-row is
            // acceptable.
            let mut col_names: Vec<String> = vec![
                "id",
                "doc_id",
                "seq_num",
                "original_content",
                "embedded_content",
                "tags",
                "metadata",
                "embedding",
                "source",
            ]
            .into_iter()
            .map(|s| self.backend.quote_ident(s))
            .collect();
            for pc in promote {
                col_names.push(self.backend.quote_ident(&pc.column_name()));
            }
            let cols_sql = col_names.join(", ");

            for ((c, emb), tags) in chunks
                .iter()
                .zip(embeddings.iter())
                .zip(tags_per_chunk.iter())
            {
                let id = format!("{}::{}", c.doc_id, c.seq_num);
                let metadata = serde_json::to_string(&c.metadata)?;
                let mut q_str = format!(
                    "INSERT INTO {} ({}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?",
                    self.fq(),
                    cols_sql
                );
                for _ in promote {
                    q_str.push_str(", ?");
                }
                q_str.push(')');
                let mut q = client
                    .query(&q_str)
                    .bind(id)
                    .bind(c.doc_id.clone())
                    .bind(c.seq_num as i32)
                    .bind(c.original_content.clone())
                    .bind(c.embedded_content.clone())
                    .bind(tags.clone())
                    .bind(metadata)
                    .bind(emb.clone())
                    .bind(self.cfg.source_tag.clone().unwrap_or_default());
                for pc in promote {
                    let v = jsonb_path_get(&c.metadata, &pc.path);
                    let cell = match v {
                        Some(serde_json::Value::String(s)) => s.clone(),
                        Some(other) => serde_json::to_string(other).unwrap_or_default(),
                        None => String::new(),
                    };
                    q = q.bind(cell);
                }
                q.execute()
                    .await
                    .context("INSERT chunk row (promoted path)")?;
            }
        }
        // delete_orphans is a no-op on CH (warned at sink construction).
        // Re-ingesting same (doc_id, seq_num) produces dup rows; users opt into
        // ReplacingMergeTree(created_at) for lazy dedup at merge time.
        Ok(())
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
