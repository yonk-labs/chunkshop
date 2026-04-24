//! pgvector sink. Creates extension + schema + table, upserts chunk rows.
//!
//! Same target table shape as Python — vectors written here are compatible
//! with Python-written rows. Schemes:
//!   - `overwrite` (default): DROP + CREATE.
//!   - `create_if_missing`:   CREATE IF NOT EXISTS, add `source` column if absent.
//!   - `append`:              not implemented; returns an error.
//!
//! Rust MVP does NOT implement:
//!   - Promoted-column writes (`promote_metadata`).
//!   - Append pre-flight dim check.
//!   - Advisory lock for concurrent-cell serialisation.

use anyhow::{anyhow, Context, Result};
use pgvector::Vector;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Row};

use crate::chunker::Chunk;
use crate::config::TargetConfig;

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
        sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
            .execute(&self.pool)
            .await
            .context("CREATE EXTENSION vector")?;

        let schema_stmt = format!(
            "CREATE SCHEMA IF NOT EXISTS \"{}\"",
            self.cfg.schema_name
        );
        sqlx::query(&schema_stmt)
            .execute(&self.pool)
            .await
            .context("CREATE SCHEMA")?;

        match self.cfg.mode.as_str() {
            "overwrite" => self.overwrite_create().await,
            "create_if_missing" => self.create_if_missing().await,
            "append" => Err(anyhow!(
                "target.mode='append' is not implemented in chunkshop-rs MVP. \
                 Use the Python implementation for append."
            )),
            other => Err(anyhow!("unknown target.mode: {other:?}")),
        }
    }

    async fn table_exists(&self) -> Result<bool> {
        let row = sqlx::query(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename=$2)",
        )
        .bind(&self.cfg.schema_name)
        .bind(&self.cfg.table)
        .fetch_one(&self.pool)
        .await?;
        Ok(row.get::<bool, _>(0))
    }

    async fn overwrite_create(&self) -> Result<()> {
        if self.table_exists().await? {
            let drop_stmt = format!("DROP TABLE {}", self.fq_table());
            sqlx::query(&drop_stmt)
                .execute(&self.pool)
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl().await
    }

    async fn create_if_missing(&self) -> Result<()> {
        if !self.table_exists().await? {
            self.create_base_ddl().await?;
        } else {
            // Ensure the `source` column exists — lets Python-created tables
            // accept Rust writes without ALTER by hand.
            let alter = format!(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source text",
                self.fq_table()
            );
            sqlx::query(&alter)
                .execute(&self.pool)
                .await
                .context("ADD COLUMN source")?;
        }
        Ok(())
    }

    async fn create_base_ddl(&self) -> Result<()> {
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
            .execute(&self.pool)
            .await
            .context("CREATE TABLE")?;

        let seq_idx_name = format!("{}_doc_seq_idx", self.cfg.table);
        let idx = format!(
            "CREATE INDEX IF NOT EXISTS \"{name}\" ON {tbl} (doc_id, seq_num)",
            name = seq_idx_name,
            tbl = self.fq_table()
        );
        sqlx::query(&idx)
            .execute(&self.pool)
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
                .execute(&self.pool)
                .await
                .context("CREATE INDEX hnsw")?;
        }
        Ok(())
    }

    /// Write all chunks for a single document in one transaction.
    pub async fn write_document(
        &self,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
    ) -> Result<()> {
        if chunks.len() != embeddings.len() {
            return Err(anyhow!(
                "chunks ({}) and embeddings ({}) length mismatch",
                chunks.len(),
                embeddings.len()
            ));
        }
        if chunks.is_empty() {
            return Ok(());
        }

        let insert_sql = format!(
            r#"
            INSERT INTO {tbl}
                (id, doc_id, seq_num, original_content, embedded_content,
                 tags, metadata, embedding, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
            ON CONFLICT (id) DO UPDATE SET
                original_content = EXCLUDED.original_content,
                embedded_content = EXCLUDED.embedded_content,
                tags = EXCLUDED.tags,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding
            "#,
            tbl = self.fq_table()
        );

        let mut tx = self.pool.begin().await?;
        let empty_tags: Vec<String> = Vec::new();
        for (c, emb) in chunks.iter().zip(embeddings.iter()) {
            let id = format!("{}::{}", c.doc_id, c.seq_num);
            let vec = Vector::from(emb.clone());
            let meta_str = serde_json::to_string(&c.metadata)?;
            sqlx::query(&insert_sql)
                .bind(&id)
                .bind(&c.doc_id)
                .bind(c.seq_num as i32)
                .bind(&c.original_content)
                .bind(&c.embedded_content)
                .bind(&empty_tags)
                .bind(&meta_str)
                .bind(&vec)
                .bind(self.cfg.source_tag.as_deref())
                .execute(&mut *tx)
                .await
                .context("INSERT chunk row")?;
        }
        tx.commit().await?;
        Ok(())
    }

    pub async fn count_docs(&self) -> Result<i64> {
        let stmt = format!("SELECT COUNT(DISTINCT doc_id) FROM {}", self.fq_table());
        let row = sqlx::query(&stmt).fetch_one(&self.pool).await?;
        Ok(row.get::<i64, _>(0))
    }
}
