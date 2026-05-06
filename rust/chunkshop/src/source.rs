//! Source implementations (legacy monolith — being split into `sources/` in Phase E).
//! FilesSource has moved to `sources/files.rs`; remaining structs move in Tasks 18–20.

use anyhow::{Context, Result};
use serde_json::json;

use crate::config::PgTableSourceConfig;

// `Document` lives in `sources::base::Document` as of v4.0. Re-exported here
// during the R1 transition; this re-export is removed when source.rs is
// deleted (Phase G, Task 27).
pub use crate::sources::base::Document;
pub use crate::sources::files::FilesSource;
pub use crate::sources::http::HttpSource;
pub use crate::sources::json_corpus::JsonCorpusSource;
pub use crate::sources::s3::S3Source;

/// Postgres source. Mirrors `python/src/chunkshop/sources/pg_table.py`.
/// Reads documents from a Postgres table by id_column / content_column /
/// optional title_column, with an optional WHERE clause. Identifiers are
/// validated at config-load (allowlist regex); the WHERE clause is trusted
/// operator input and concatenated verbatim — see `PgTableSourceConfig`.
pub struct PgTableSource {
    cfg: PgTableSourceConfig,
}

impl PgTableSource {
    pub fn new(cfg: PgTableSourceConfig) -> Self {
        Self { cfg }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let dsn = std::env::var(&self.cfg.dsn_env).with_context(|| {
            format!(
                "DSN env var {} not set (required by source.dsn_env)",
                self.cfg.dsn_env
            )
        })?;

        // Column order matches Python's pg_table.py:
        //   [id, content, optional title, *metadata_columns...]
        // Metadata columns get pulled into Document.metadata under their
        // original column name.
        let mut select = format!(
            r#"SELECT "{id_col}", "{content_col}""#,
            id_col = self.cfg.id_column,
            content_col = self.cfg.content_column,
        );
        let mut title_idx: Option<usize> = None;
        if let Some(tc) = &self.cfg.title_column {
            title_idx = Some(2);
            select.push_str(&format!(r#", "{tc}""#));
        }
        let meta_start = if title_idx.is_some() { 3 } else { 2 };
        for col in &self.cfg.metadata_columns {
            select.push_str(&format!(r#", "{col}""#));
        }
        select.push_str(&format!(
            r#" FROM "{schema}"."{table}""#,
            schema = self.cfg.schema_name,
            table = self.cfg.table,
        ));
        if let Some(w) = &self.cfg.where_clause {
            select.push_str(&format!(" WHERE {w}"));
        }

        let pool = sqlx::postgres::PgPoolOptions::new()
            .max_connections(1)
            .connect(&dsn)
            .await
            .with_context(|| format!("connecting to {}", self.cfg.dsn_env))?;

        let rows = sqlx::query(&select)
            .fetch_all(&pool)
            .await
            .with_context(|| format!("running query: {select}"))?;

        let mut out = Vec::with_capacity(rows.len());
        for row in rows {
            use sqlx::Row;
            let id: String = row
                .try_get::<String, _>(0)
                .or_else(|_| row.try_get::<i64, _>(0).map(|n| n.to_string()))
                .or_else(|_| row.try_get::<i32, _>(0).map(|n| n.to_string()))
                .with_context(|| format!("reading id column from row"))?;
            let content: String = row.try_get(1).context("reading content column")?;
            let title: Option<String> = match title_idx {
                Some(i) => row.try_get::<Option<String>, _>(i).unwrap_or(None),
                None => None,
            };
            // Build metadata from the trailing columns. Each metadata column
            // is read as JSON-friendly types (string / i64 / f64 / bool /
            // text[] -> array). Unknown types fall back to NULL.
            let mut meta = serde_json::Map::new();
            for (i, col) in self.cfg.metadata_columns.iter().enumerate() {
                let idx = meta_start + i;
                let v = read_meta_value(&row, idx);
                meta.insert(col.clone(), v);
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
            });
        }
        Ok(out)
    }
}

/// Read a Postgres column at `idx` as a JSON-friendly value. Tries common
/// types in order; falls back to JSON null on unsupported types. Mirrors
/// what Python's psycopg returns for the same columns.
fn read_meta_value(row: &sqlx::postgres::PgRow, idx: usize) -> serde_json::Value {
    use sqlx::Row;
    if let Ok(v) = row.try_get::<Option<String>, _>(idx) {
        return v.map(serde_json::Value::String).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i32>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<f64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<bool>, _>(idx) {
        return v.map(|b| json!(b)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<Vec<String>>, _>(idx) {
        return v.map(|a| json!(a)).unwrap_or(serde_json::Value::Null);
    }
    serde_json::Value::Null
}

