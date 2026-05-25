//! ClickHouse backend (CH 24.10+ — vector_similarity experimental index required).
//!
//! Mirrors `python/src/chunkshop/backends/clickhouse.py`. Two divergences from
//! the PG backend's R1 shape:
//!   1. `BackendDialect` only — `BackendConn` is sqlx-Postgres-concrete by
//!      deliberate R1 seam (see backends/base.rs:14-16). Connection-layer
//!      methods (`table_exists`, `embedding_dim`, `with_create_lock`) live as
//!      inherent methods on the concrete type. The GAT abstraction is R2's job.
//!   2. CH has no upsert. `upsert_clause()` returns `""` always.
//!
//! Driver: official `clickhouse` crate (HTTP transport, `Vec<f32>` natively maps
//! to `Array(Float32)`). DSN format mirrors Python's `clickhouse-connect` style:
//! `clickhouse://user:pass@host:port/database`.
//!
//! `Client` is cheap to clone (it shares an internal connection pool), so we
//! initialize it lazily and clone-on-demand rather than wrapping in a `Pool`
//! helper like the PG backend does.

use anyhow::{anyhow, Context, Result};
use clickhouse::Client;
use tokio::sync::OnceCell;

pub struct ClickhouseBackend {
    dsn_env: String,
    client: OnceCell<Client>,
}

impl ClickhouseBackend {
    pub fn new(dsn_env: String) -> Self {
        Self {
            dsn_env,
            client: OnceCell::new(),
        }
    }

    /// Lazily-initialized client. Idempotent. The official `clickhouse`
    /// crate's `Client` clones cheaply (shares a connection pool), so we
    /// hand out clones rather than references.
    pub async fn client(&self) -> Result<Client> {
        let c = self
            .client
            .get_or_try_init(|| async {
                let dsn = std::env::var(&self.dsn_env)
                    .with_context(|| format!("DSN env var {} not set", self.dsn_env))?;
                build_client_from_dsn(&dsn)
            })
            .await?;
        Ok(c.clone())
    }

    /// Force-initialize. Idempotent. Mirrors PG's `BackendConn::connect` shape
    /// for symmetry, even though CH has no transactional connect step.
    pub async fn connect(&self) -> Result<()> {
        let _ = self.client().await?;
        Ok(())
    }

    /// Check whether a table exists in the given database. Mirrors Python's
    /// ClickHouseBackend.table_exists. Inherent (not on BackendConn) because
    /// R1's BackendConn trait is sqlx-Postgres-concrete by deliberate seam.
    pub async fn table_exists(&self, client: &Client, db: &str, table: &str) -> Result<bool> {
        #[derive(clickhouse::Row, serde::Deserialize)]
        struct Count {
            c: u64,
        }
        let mut cur = client
            .query("SELECT count() AS c FROM system.tables WHERE database = ? AND name = ?")
            .bind(db)
            .bind(table)
            .fetch::<Count>()?;
        let row = cur
            .next()
            .await?
            .ok_or_else(|| anyhow!("system.tables count() returned no rows"))?;
        Ok(row.c > 0)
    }

    /// Best-effort embedding-dim introspection. Reads `length(embedding)` from
    /// the first row. Returns None on empty table or missing column.
    /// Mirrors Python's `ClickHouseBackend.embedding_dim`.
    pub async fn embedding_dim(
        &self,
        client: &Client,
        db: &str,
        table: &str,
    ) -> Result<Option<usize>> {
        #[derive(clickhouse::Row, serde::Deserialize)]
        struct DimRow {
            d: u64,
        }
        let fq = self.fq_table(db, table);
        let q = format!("SELECT length(embedding) AS d FROM {fq} LIMIT 1");
        let mut cur = match client.query(&q).fetch::<DimRow>() {
            Ok(c) => c,
            // Missing-column / type-error path: behave like Python's bare except.
            Err(_) => return Ok(None),
        };
        match cur.next().await {
            Ok(Some(r)) => Ok(Some(r.d as usize)),
            Ok(None) => Ok(None),
            Err(_) => Ok(None),
        }
    }

    /// Acquire a CH-side DDL serialization lock. CH serializes DDL natively
    /// (single-server) or via Keeper/ZK (replicated) — no app-level lock needed.
    /// Inherent + no-op for symmetry with Python's `with_create_lock`.
    pub async fn with_create_lock(&self, _client: &Client, _key: &str) -> Result<()> {
        Ok(())
    }
}

/// Parse `clickhouse://user:pass@host:port/database` (also `http://`/`https://`
/// aliases) into a fully-configured `Client`. Mirrors Python's
/// `_parse_clickhouse_dsn` in `python/src/chunkshop/backends/clickhouse.py`.
fn build_client_from_dsn(dsn: &str) -> Result<Client> {
    let parsed = url::Url::parse(dsn).with_context(|| format!("parsing CH DSN {dsn:?}"))?;
    let scheme = parsed.scheme();
    let secure = matches!(scheme, "https" | "clickhouse+https");
    if !matches!(
        scheme,
        "clickhouse" | "http" | "https" | "clickhouse+http" | "clickhouse+https"
    ) {
        return Err(anyhow!(
            "expected clickhouse:// or http(s):// DSN for ClickHouse, got {scheme:?}"
        ));
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow!("DSN missing host: {dsn:?}"))?;
    let port = parsed.port().unwrap_or(if secure { 8443 } else { 8123 });
    let url = format!(
        "{}://{}:{}",
        if secure { "https" } else { "http" },
        host,
        port
    );

    let user = match parsed.username() {
        "" => "default".to_string(),
        u => urlencoding::decode(u)
            .map(|c| c.into_owned())
            .unwrap_or_else(|_| u.to_string()),
    };
    let password = parsed
        .password()
        .map(|p| {
            urlencoding::decode(p)
                .map(|c| c.into_owned())
                .unwrap_or_else(|_| p.to_string())
        })
        .unwrap_or_default();
    let database = match parsed.path().trim_start_matches('/') {
        "" => "default".to_string(),
        d => d.to_string(),
    };

    Ok(Client::default()
        .with_url(url)
        .with_user(user)
        .with_password(password)
        .with_database(database))
}

use crate::backends::base::{BackendDialect, ColSpec};

impl BackendDialect for ClickhouseBackend {
    const NAME: &'static str = "clickhouse";
    const SUPPORTS_UPSERT: bool = false;

    fn quote_ident(&self, name: &str) -> String {
        // CH uses backticks. Defense-in-depth: double any embedded backticks even
        // though the config-load identifier regex disallows them. Mirrors
        // python/src/chunkshop/backends/clickhouse.py::quote_ident.
        format!("`{}`", name.replace('`', "``"))
    }

    fn fq_table(&self, db: &str, table: &str) -> String {
        format!("{}.{}", self.quote_ident(db), self.quote_ident(table))
    }

    fn vector_type_ddl(&self, _dim: usize) -> String {
        // CH stores vectors as Array(Float32). The dim is enforced at index time
        // by vector_similarity, not at the column-type level.
        "Array(Float32)".to_string()
    }

    fn json_type_ddl(&self) -> String {
        // String + JSONExtractString. CH has an experimental JSON type but the
        // path ergonomics are equivalent for chunkshop's flat metadata shape.
        "String".to_string()
    }

    fn tags_array_type_ddl(&self) -> String {
        "Array(String)".to_string()
    }

    fn text_pk_type_ddl(&self) -> String {
        "String".to_string()
    }

    fn timestamp_now_default_ddl(&self) -> String {
        // Used as the type_ddl for created_at; default is encoded separately
        // when canonical_cols sets `default = Some("now64()")`.
        "DateTime64(6)".to_string()
    }

    fn vector_literal(&self, arr: &[f32]) -> String {
        // CH array literal text form: [v1,v2,v3]. Used only for SELECT-side
        // injection (e.g. cosineDistance(embedding, [...])); INSERT path uses
        // the typed Vec<f32> binding via the official driver.
        let parts: Vec<String> = arr.iter().map(|x| format!("{x:.6}")).collect();
        format!("[{}]", parts.join(","))
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        // metadata column is String; store as JSON-serialized text. Mirrors
        // python/src/chunkshop/backends/clickhouse.py::json_literal.
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        // CH's JSONExtractString takes positional path segments rather than
        // jsonpath syntax. Returns '' for missing paths — chunkshop callers
        // accept null-ish on missing.
        let segs: Vec<String> = dotted_path.split('.').map(|s| format!("'{s}'")).collect();
        format!("JSONExtractString({col_expr}, {})", segs.join(", "))
    }

    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String {
        // CH has no upsert. Sinks must INSERT-only.
        String::new()
    }

    fn create_database_sql(&self, name: &str) -> String {
        format!("CREATE DATABASE IF NOT EXISTS {}", self.quote_ident(name))
    }

    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        format!(
            "ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {} {type_ddl}",
            self.quote_ident(col)
        )
    }

    fn drop_table_sql(&self, fq: &str) -> String {
        // SYNC blocks until the table is fully dropped. Important for
        // overwrite mode so the subsequent CREATE doesn't race.
        format!("DROP TABLE IF EXISTS {fq} SYNC")
    }

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        _dim: usize,
        engine: Option<&str>,
        _vector_metric: Option<&str>,
    ) -> Vec<String> {
        let mut col_lines: Vec<String> = Vec::with_capacity(cols.len());
        let mut order_by_cols: Vec<&str> = Vec::new();
        for c in cols {
            let mut line = format!("  {} {}", self.quote_ident(c.name), c.type_ddl);
            if let Some(default) = c.default {
                line.push_str(&format!(" DEFAULT {default}"));
            }
            // CH columns are nullable only via Nullable(T); we don't use that
            // here — non-default columns are required-by-convention. Skip the
            // NOT NULL emission that the PG impl does.
            col_lines.push(line);
            if c.is_primary_key {
                order_by_cols.push(c.name);
            }
        }

        if hnsw {
            // CH 24.10+ inline vector_similarity index. The 2-arg form was
            // accepted in 24.10.4 ('hnsw', 'cosineDistance') with GRANULARITY 1.
            // Mirrors python/src/chunkshop/backends/clickhouse.py — see the
            // comment there about the 3-arg form being rejected.
            col_lines.push(
                "  INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance') GRANULARITY 1"
                    .to_string(),
            );
        }

        let body = col_lines.join(",\n");
        let engine_clause = match engine {
            Some(e) => e.to_string(),
            None => {
                let order_by = if order_by_cols.is_empty() {
                    "tuple()".to_string()
                } else {
                    order_by_cols
                        .iter()
                        .map(|c| self.quote_ident(c))
                        .collect::<Vec<_>>()
                        .join(", ")
                };
                format!("MergeTree() ORDER BY ({order_by})")
            }
        };

        vec![format!(
            "CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n) ENGINE = {engine_clause}"
        )]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dsn_parses_clickhouse_scheme_with_credentials() {
        // Pure unit test — does not require a live CH. Just verifies the parser
        // accepts the canonical shape without panicking.
        let _client =
            build_client_from_dsn("clickhouse://default:chpw@localhost:8124/chunkshop_test")
                .expect("parse");
    }

    #[test]
    fn dsn_parses_http_alias() {
        let _client = build_client_from_dsn("http://localhost:8123/test").expect("parse");
    }

    #[test]
    fn dsn_rejects_unknown_scheme() {
        // `clickhouse::Client` doesn't impl `Debug`, so we can't use
        // `.unwrap_err()` (which formats the Ok variant on failure). Pattern-
        // match instead.
        let err = match build_client_from_dsn("postgres://x/y") {
            Ok(_) => panic!("expected error for postgres scheme"),
            Err(e) => e,
        };
        assert!(format!("{err:#}").contains("expected clickhouse://"));
    }
}
