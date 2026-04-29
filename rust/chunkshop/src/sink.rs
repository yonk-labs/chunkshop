//! pgvector sink. Creates extension + schema + table, upserts chunk rows.
//!
//! Same target table shape as Python — vectors written here are compatible
//! with Python-written rows. Modes (all implemented as of MB-3):
//!
//! - `overwrite`: DROP + CREATE. Refuses to drop a table that holds rows
//!   with a different `source_tag` unless `target.force_overwrite: true`.
//! - `create_if_missing`: CREATE IF NOT EXISTS, add `source` column + any
//!   `promote_metadata` columns if absent.
//! - `append`: pre-flight (table exists, embedding-dim match, ALTER for
//!   `source` + promote columns). Errors out cleanly if any check fails.
//!
//! Schema setup runs inside a transaction with a `pg_advisory_xact_lock`
//! keyed on the schema name (BLAKE2b digest, first 8 bytes, big-endian
//! signed). This serializes concurrent cells against the same schema and
//! matches Python's `_advisory_lock_key`.
//!
//! `source` column is **write-once on `ON CONFLICT`**: a later cell colliding
//! on `(doc_id, seq_num)` re-runs the row's content + embedding + promoted
//! columns but never overwrites the original cell's `source_tag`. Matches
//! Python and is load-bearing for multi-source filtering.

use anyhow::{anyhow, Context, Result};
use blake2::{digest::consts::U8, Blake2b, Digest};
use pgvector::Vector;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Row, Transaction};

use crate::chunker::Chunk;
use crate::config::{PromoteColumn, TargetConfig};

/// Deterministic 64-bit signed int key for `pg_advisory_xact_lock`. Mirrors
/// Python's `_advisory_lock_key`: BLAKE2b-8-byte digest of the schema name,
/// big-endian signed.
fn advisory_lock_key(schema_name: &str) -> i64 {
    let mut hasher = Blake2b::<U8>::new();
    hasher.update(schema_name.as_bytes());
    let digest = hasher.finalize();
    i64::from_be_bytes(digest.into())
}

/// Traverse a dotted path through a JSON value. Returns `None` if any segment
/// is missing or an intermediate is not an object. Mirrors Python's
/// `_jsonb_path_get`.
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

pub struct PgVectorSink {
    cfg: TargetConfig,
    embed_dim: usize,
    pool: PgPool,
}

impl PgVectorSink {
    pub async fn connect(cfg: TargetConfig, embed_dim: usize) -> Result<Self> {
        let dsn = std::env::var(&cfg.dsn_env).with_context(|| {
            format!(
                "DSN env var {} not set (required by target.dsn_env)",
                cfg.dsn_env
            )
        })?;
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(&dsn)
            .await
            .with_context(|| format!("connecting to {}", cfg.dsn_env))?;
        Ok(Self {
            cfg,
            embed_dim,
            pool,
        })
    }

    fn fq_table(&self) -> String {
        // Identifiers are regex-validated in config.rs.
        format!("\"{}\".\"{}\"", self.cfg.schema_name, self.cfg.table)
    }

    pub async fn create_table(&self) -> Result<()> {
        let mut tx = self.pool.begin().await.context("begin schema-setup tx")?;
        let key = advisory_lock_key(&self.cfg.schema_name);
        sqlx::query("SELECT pg_advisory_xact_lock($1)")
            .bind(key)
            .execute(&mut *tx)
            .await
            .context("acquire schema advisory lock")?;

        sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
            .execute(&mut *tx)
            .await
            .context("CREATE EXTENSION vector")?;

        let schema_stmt = format!(r#"CREATE SCHEMA IF NOT EXISTS "{}""#, self.cfg.schema_name);
        sqlx::query(&schema_stmt)
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

    async fn table_exists_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<bool> {
        let row = sqlx::query(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename=$2)",
        )
        .bind(&self.cfg.schema_name)
        .bind(&self.cfg.table)
        .fetch_one(&mut **tx)
        .await?;
        Ok(row.get::<bool, _>(0))
    }

    async fn current_embed_dim_in_tx(
        &self,
        tx: &mut Transaction<'_, Postgres>,
    ) -> Result<Option<usize>> {
        let row = sqlx::query(
            r#"
            SELECT format_type(atttypid, atttypmod) AS t
            FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = $1 AND n.nspname = $2
            ) AND attname = 'embedding'
            "#,
        )
        .bind(&self.cfg.table)
        .bind(&self.cfg.schema_name)
        .fetch_optional(&mut **tx)
        .await?;
        let Some(r) = row else { return Ok(None) };
        let s: String = r.get("t");
        let re = regex::Regex::new(r"^vector\((\d+)\)$").unwrap();
        Ok(re
            .captures(&s)
            .and_then(|c| c.get(1))
            .and_then(|m| m.as_str().parse().ok()))
    }

    async fn overwrite_create_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if self.table_exists_in_tx(tx).await? && !self.cfg.force_overwrite {
            let stmt = format!(
                "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
                self.fq_table()
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
                    schema = self.cfg.schema_name,
                    table = self.cfg.table,
                    foreign = foreign,
                    my_tag = my_tag,
                ));
            }
        }
        if self.table_exists_in_tx(tx).await? {
            let drop_stmt = format!("DROP TABLE {}", self.fq_table());
            sqlx::query(&drop_stmt)
                .execute(&mut **tx)
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl_in_tx(tx).await
    }

    async fn create_if_missing_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.table_exists_in_tx(tx).await? {
            return self.create_base_ddl_in_tx(tx).await;
        }
        let alter = format!(
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source text",
            self.fq_table()
        );
        sqlx::query(&alter)
            .execute(&mut **tx)
            .await
            .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn append_preflight_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.table_exists_in_tx(tx).await? {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.schema_name,
                self.cfg.table
            ));
        }
        let current_dim = self.current_embed_dim_in_tx(tx).await?;
        let Some(d) = current_dim else {
            return Err(anyhow!(
                "append mode: table {}.{} has no 'embedding' vector column. Not a chunkshop \
                 table — pick a different target or use mode='overwrite'.",
                self.cfg.schema_name,
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
        let alter = format!(
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source text",
            self.fq_table()
        );
        sqlx::query(&alter)
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
            // pc.type_ is allowlisted in PromoteColumn::validate_type — safe to interpolate.
            let stmt = format!(
                r#"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS "{col}" {ty}"#,
                tbl = self.fq_table(),
                col = pc.column_name(),
                ty = pc.type_,
            );
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        let ddl = format!(
            r#"
            CREATE TABLE IF NOT EXISTS {tbl} (
                id text PRIMARY KEY,
                doc_id text NOT NULL,
                seq_num int NOT NULL,
                original_content text NOT NULL,
                embedded_content text NOT NULL,
                tags text[] NOT NULL DEFAULT '{{}}',
                metadata jsonb NOT NULL DEFAULT '{{}}',
                embedding vector({dim}) NOT NULL,
                source text,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            "#,
            tbl = self.fq_table(),
            dim = self.embed_dim
        );
        sqlx::query(&ddl)
            .execute(&mut **tx)
            .await
            .context("CREATE TABLE")?;

        let seq_idx_name = format!("{}_doc_seq_idx", self.cfg.table);
        let idx = format!(
            "CREATE INDEX IF NOT EXISTS \"{name}\" ON {tbl} (doc_id, seq_num)",
            name = seq_idx_name,
            tbl = self.fq_table()
        );
        sqlx::query(&idx)
            .execute(&mut **tx)
            .await
            .context("CREATE INDEX doc_seq")?;

        if self.cfg.hnsw {
            let hnsw_idx_name = format!("{}_emb_hnsw_idx", self.cfg.table);
            let hnsw = format!(
                "CREATE INDEX IF NOT EXISTS \"{name}\" ON {tbl} USING hnsw (embedding vector_cosine_ops)",
                name = hnsw_idx_name,
                tbl = self.fq_table()
            );
            sqlx::query(&hnsw)
                .execute(&mut **tx)
                .await
                .context("CREATE INDEX hnsw")?;
        }

        self.ensure_promote_columns_in_tx(tx).await
    }

    /// Write all chunks for a single document in one transaction. Includes
    /// promote_metadata columns if any. ON CONFLICT UPDATE refreshes content,
    /// metadata, embedding, and promoted columns but **never `source`** — the
    /// original cell's source_tag wins forever (write-once provenance).
    pub async fn write_document(
        &self,
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
        let n_base = 9; // id, doc_id, seq_num, original_content, embedded_content, tags, metadata, embedding, source

        // Build column list and placeholders.
        let mut cols: Vec<String> = vec![
            "id".into(),
            "doc_id".into(),
            "seq_num".into(),
            "original_content".into(),
            "embedded_content".into(),
            "tags".into(),
            "metadata".into(),
            "embedding".into(),
            "source".into(),
        ];
        let mut placeholders: Vec<String> = (1..=n_base)
            .map(|i| match i {
                7 => format!("${i}::jsonb"),
                _ => format!("${i}"),
            })
            .collect();
        for (i, pc) in promote.iter().enumerate() {
            cols.push(pc.column_name());
            // pc.type_ is allowlisted; safe to interpolate as ::cast.
            placeholders.push(format!("${}::{}", n_base + 1 + i, pc.type_));
        }
        let cols_sql = cols
            .iter()
            .map(|c| format!(r#""{c}""#))
            .collect::<Vec<_>>()
            .join(", ");
        let vals_sql = placeholders.join(", ");

        // ON CONFLICT UPDATE: skip id, doc_id, seq_num, AND source. Include all
        // promote columns so re-runs refresh extracted metadata.
        let mut update_cols: Vec<String> = vec![
            "original_content".into(),
            "embedded_content".into(),
            "tags".into(),
            "metadata".into(),
            "embedding".into(),
        ];
        for pc in promote {
            update_cols.push(pc.column_name());
        }
        let updates_sql = update_cols
            .iter()
            .map(|c| format!(r#""{c}" = EXCLUDED."{c}""#))
            .collect::<Vec<_>>()
            .join(", ");

        let insert_sql = format!(
            "INSERT INTO {tbl} ({cols}) VALUES ({vals}) ON CONFLICT (id) DO UPDATE SET {updates}",
            tbl = self.fq_table(),
            cols = cols_sql,
            vals = vals_sql,
            updates = updates_sql,
        );

        let mut tx = self.pool.begin().await?;
        for ((c, emb), tags) in chunks
            .iter()
            .zip(embeddings.iter())
            .zip(tags_per_chunk.iter())
        {
            let id = format!("{}::{}", c.doc_id, c.seq_num);
            let vec = Vector::from(emb.clone());
            let meta_str = serde_json::to_string(&c.metadata)?;

            let mut q = sqlx::query(&insert_sql)
                .bind(id)
                .bind(&c.doc_id)
                .bind(c.seq_num as i32)
                .bind(&c.original_content)
                .bind(&c.embedded_content)
                .bind(tags)
                .bind(&meta_str)
                .bind(&vec)
                .bind(self.cfg.source_tag.as_deref());

            for pc in promote {
                q = q.bind(promote_value_for(&c.metadata, pc));
            }

            q.execute(&mut *tx).await.context("INSERT chunk row")?;
        }
        // delete_orphans: same-tx cleanup of stale chunks at higher seq_nums
        // when a doc shrinks. All chunks here share doc_id, so chunks[0].doc_id
        // is the right key. No-op when growing (seq_num >= len(chunks) catches none).
        if self.cfg.delete_orphans {
            let doc_id = &chunks[0].doc_id;
            let new_count = chunks.len() as i32;
            let delete_sql = format!(
                "DELETE FROM {tbl} WHERE doc_id = $1 AND seq_num >= $2",
                tbl = self.fq_table(),
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

    pub async fn count_docs(&self) -> Result<i64> {
        let stmt = format!("SELECT COUNT(DISTINCT doc_id) FROM {}", self.fq_table());
        let row = sqlx::query(&stmt).fetch_one(&self.pool).await?;
        Ok(row.get::<i64, _>(0))
    }

    /// Borrow the underlying pool for sibling SQL (used by `Pipeline::delete_document`).
    pub fn pool(&self) -> &PgPool {
        &self.pool
    }
}

/// Project a chunk's metadata down to the right text representation for the
/// promoted column's typed cast. Postgres handles the actual cast via the
/// `::<type>` placeholder (see `write_document`):
///   - `text`        — bind the JSON string verbatim if the value is a JSON
///                     string; otherwise bind the value's JSON form (numbers,
///                     bools, etc. round-trip as their canonical text).
///   - `text[]`      — bind the JSON array literal (`["a","b"]`) for pg to
///                     parse as a text[].
///   - `int`/`bigint`/`boolean`/`jsonb`/`timestamptz`/`date` — bind the
///                     value's canonical JSON text and let pg cast.
/// Missing path → `None` → pg sees NULL.
fn promote_value_for(metadata: &serde_json::Value, pc: &PromoteColumn) -> Option<String> {
    let v = jsonb_path_get(metadata, &pc.path)?;
    Some(match v {
        serde_json::Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    })
}
