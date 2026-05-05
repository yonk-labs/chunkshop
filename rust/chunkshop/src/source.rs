//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde_json::json;
use sha1::{Digest, Sha1};

use crate::config::{
    FilesSourceConfig, HttpSourceConfig, JsonCorpusSourceConfig, PgTableSourceConfig,
    S3SourceConfig,
};

// `Document` lives in `sources::base::Document` as of v4.0. Re-exported here
// during the R1 transition; this re-export is removed when source.rs is
// deleted (Phase G, Task 27).
pub use crate::sources::base::Document;

pub struct FilesSource {
    cfg: FilesSourceConfig,
}

impl FilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        Self { cfg }
    }

    /// Enumerate files matching the glob, in sorted order, reading each as text.
    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut paths: Vec<PathBuf> = glob::glob(&self.cfg.glob)
            .with_context(|| format!("invalid glob {:?}", self.cfg.glob))?
            .filter_map(std::result::Result::ok)
            .collect();
        if paths.is_empty() {
            return Err(anyhow!("no files matched glob: {}", self.cfg.glob));
        }
        paths.sort();

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let text = std::fs::read_to_string(&p)
                .with_context(|| format!("reading {}", p.display()))?;
            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            out.push(Document {
                id: doc_id,
                content: text,
                title,
                metadata: json!({ "source_path": p.display().to_string() }),
            });
        }
        Ok(out)
    }

    fn id_for(&self, path: &Path) -> Result<String> {
        match self.cfg.id_from.as_str() {
            "path" => Ok(path.display().to_string()),
            "stem" => path
                .file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
                .ok_or_else(|| anyhow!("file has no stem: {}", path.display())),
            "sha1" => {
                let mut hasher = Sha1::new();
                hasher.update(path.display().to_string().as_bytes());
                Ok(format!("{:x}", hasher.finalize()))
            }
            other => Err(anyhow!("unknown id_from: {other}")),
        }
    }
}

/// JSON-corpus source. Mirrors `python/src/chunkshop/sources/json_corpus.py`.
/// Reads a JSON file, extracts the array under `documents_key`, and yields
/// one `Document` per row. Field-name configuration matches Python's pydantic
/// defaults (`id` / `content` / `title`).
pub struct JsonCorpusSource {
    cfg: JsonCorpusSourceConfig,
}

impl JsonCorpusSource {
    pub fn new(cfg: JsonCorpusSourceConfig) -> Self {
        Self { cfg }
    }

    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let bytes = std::fs::read(&self.cfg.path)
            .with_context(|| format!("reading {}", self.cfg.path))?;
        let parsed: serde_json::Value = serde_json::from_slice(&bytes)
            .with_context(|| format!("parsing JSON from {}", self.cfg.path))?;
        let arr = parsed
            .get(&self.cfg.documents_key)
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                anyhow!(
                    "no array at key {:?} in {}",
                    self.cfg.documents_key,
                    self.cfg.path
                )
            })?;

        let mut out = Vec::with_capacity(arr.len());
        for (i, row_value) in arr.iter().enumerate() {
            let row = row_value.as_object().ok_or_else(|| {
                anyhow!("row {i} in {} is not a JSON object", self.cfg.path)
            })?;
            let id = row
                .get(&self.cfg.id_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.id_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let content = row
                .get(&self.cfg.content_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.content_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let title = self
                .cfg
                .title_field
                .as_ref()
                .and_then(|tf| row.get(tf).and_then(|v| v.as_str()).map(String::from));

            // Metadata = row keys minus id/content/title fields. Preserve raw
            // JSON values so downstream extractors / promote_metadata can
            // pull typed values.
            let mut meta = serde_json::Map::new();
            for (k, v) in row.iter() {
                if k == &self.cfg.id_field {
                    continue;
                }
                if k == &self.cfg.content_field {
                    continue;
                }
                if let Some(tf) = &self.cfg.title_field {
                    if k == tf {
                        continue;
                    }
                }
                meta.insert(k.clone(), v.clone());
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

/// HTTP source. Mirrors `python/src/chunkshop/sources/http.py`.
/// Fetches each URL in `cfg.urls` plus URLs extracted from `cfg.sitemap` (if
/// set). Builds one Document per fetched URL with `id=<url>`,
/// `content=<response body>`, `title=<HTML <title> tag>` and metadata
/// `{url, status_code, content_type}`. Single GET per URL — no retries, no
/// auth, no rate limiting. Fail-fast on non-2xx responses.
pub struct HttpSource {
    cfg: HttpSourceConfig,
}

impl HttpSource {
    pub fn new(cfg: HttpSourceConfig) -> Self {
        Self { cfg }
    }

    /// Fetch one URL → (body, status, content_type). Errors out on non-2xx.
    async fn fetch(client: &reqwest::Client, url: &str) -> Result<(String, u16, String)> {
        let resp = client
            .get(url)
            .header("User-Agent", "chunkshop-http/1.0")
            .send()
            .await
            .with_context(|| format!("GET {url}"))?;
        let status = resp.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(anyhow!("GET {url}: status {status}"));
        }
        let ctype = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = resp
            .text()
            .await
            .with_context(|| format!("reading body of {url}"))?;
        Ok((body, status, ctype))
    }

    /// Extract `<title>...</title>` content (case-insensitive, dotall). Returns
    /// `None` if no title tag or empty title.
    fn extract_title(body: &str) -> Option<String> {
        let re = regex::Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
        let captures = re.captures(body)?;
        let raw = captures.get(1)?.as_str().trim();
        if raw.is_empty() {
            None
        } else {
            Some(raw.to_string())
        }
    }

    /// Extract `<loc>` URLs from a sitemap XML body. Single-pass regex; works
    /// regardless of namespace prefix. Returns document order.
    fn parse_sitemap(body: &str) -> Vec<String> {
        let re = match regex::Regex::new(r"(?is)<loc>(.*?)</loc>") {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        re.captures_iter(body)
            .filter_map(|c| c.get(1).map(|m| m.as_str().trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect()
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut fetch_list: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for u in &self.cfg.urls {
            if seen.insert(u.clone()) {
                fetch_list.push(u.clone());
            }
        }
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .context("build reqwest client")?;
        if let Some(sm) = &self.cfg.sitemap {
            let (sm_body, _, _) = Self::fetch(&client, sm).await?;
            for u in Self::parse_sitemap(&sm_body) {
                if seen.insert(u.clone()) {
                    fetch_list.push(u);
                }
            }
        }

        let mut out: Vec<Document> = Vec::with_capacity(fetch_list.len());
        for url in fetch_list {
            let (body, status, ctype) = Self::fetch(&client, &url).await?;
            let title = Self::extract_title(&body);
            out.push(Document {
                id: url.clone(),
                content: body,
                title,
                metadata: serde_json::json!({
                    "url": url,
                    "status_code": status,
                    "content_type": ctype,
                }),
            });
        }
        Ok(out)
    }
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
