//! Source implementations (legacy monolith — being split into `sources/` in Phase E).
//! FilesSource has moved to `sources/files.rs`; remaining structs move in Tasks 18–20.

use anyhow::{Context, Result};
use serde_json::json;

use crate::config::{
    PgTableSourceConfig,
    S3SourceConfig,
};

// `Document` lives in `sources::base::Document` as of v4.0. Re-exported here
// during the R1 transition; this re-export is removed when source.rs is
// deleted (Phase G, Task 27).
pub use crate::sources::base::Document;
pub use crate::sources::files::FilesSource;
pub use crate::sources::http::HttpSource;
pub use crate::sources::json_corpus::JsonCorpusSource;

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

/// S3 source. Mirrors `python/src/chunkshop/sources/s3.py`. Uses the
/// `object_store` crate's `AmazonS3` builder. Auth + region resolution are
/// `from_env()` (standard AWS credential chain). The optional `endpoint_url`
/// lets users point at minio / R2 / other S3-compatible servers.
///
/// Document shape per fetched object:
///   id        = `s3://<bucket>/<key>`
///   content   = response body decoded as utf-8 (lossy on non-UTF8)
///   title     = None
///   metadata  = `{"bucket": str, "key": str, "size": int, "etag": str}`
pub struct S3Source {
    cfg: S3SourceConfig,
}

impl S3Source {
    pub fn new(cfg: S3SourceConfig) -> Self {
        Self { cfg }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        use futures::StreamExt;
        use object_store::aws::AmazonS3Builder;
        use object_store::{path::Path as ObjPath, ObjectStore};

        // Build the S3 client. `from_env()` reads AWS_* env vars; explicit
        // bucket overrides any AWS_BUCKET in env. `with_endpoint` is opt-in
        // for minio / R2.
        let mut builder = AmazonS3Builder::from_env().with_bucket_name(&self.cfg.bucket);
        if let Some(endpoint) = &self.cfg.endpoint_url {
            builder = builder.with_endpoint(endpoint);
            // Path-style addressing is what minio uses by default; harmless for
            // real AWS too. object_store auto-detects in most cases but being
            // explicit avoids ambiguity for custom endpoints.
            builder = builder.with_allow_http(endpoint.starts_with("http://"));
        }
        let store = builder
            .build()
            .with_context(|| format!("building S3 client for bucket {}", self.cfg.bucket))?;

        // List under the prefix. object_store::list returns a stream of
        // ObjectMeta — collect everything before fetching.
        let prefix = if self.cfg.prefix.is_empty() {
            None
        } else {
            Some(ObjPath::from(self.cfg.prefix.clone()))
        };
        let mut listing = store.list(prefix.as_ref());
        let mut metas: Vec<object_store::ObjectMeta> = Vec::new();
        while let Some(item) = listing.next().await {
            metas.push(item.with_context(|| format!("list under {}", self.cfg.prefix))?);
        }

        let mut out: Vec<Document> = Vec::with_capacity(metas.len());
        for meta in metas {
            let key = meta.location.to_string();
            let result = store
                .get(&meta.location)
                .await
                .with_context(|| format!("GET s3://{}/{key}", self.cfg.bucket))?;
            let bytes = result
                .bytes()
                .await
                .with_context(|| format!("read body of s3://{}/{key}", self.cfg.bucket))?;
            let content = String::from_utf8_lossy(&bytes).to_string();
            let etag = meta.e_tag.clone().unwrap_or_default();
            out.push(Document {
                id: format!("s3://{}/{}", self.cfg.bucket, key),
                content,
                title: None,
                metadata: serde_json::json!({
                    "bucket": self.cfg.bucket,
                    "key": key,
                    "size": meta.size,
                    "etag": etag,
                }),
            });
        }
        Ok(out)
    }
}
