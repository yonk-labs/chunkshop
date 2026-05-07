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
                let dsn = std::env::var(&self.dsn_env).with_context(|| {
                    format!("DSN env var {} not set", self.dsn_env)
                })?;
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
    let url = format!("{}://{}:{}", if secure { "https" } else { "http" }, host, port);

    let user = match parsed.username() {
        "" => "default".to_string(),
        u => urlencoding::decode(u).map(|c| c.into_owned()).unwrap_or_else(|_| u.to_string()),
    };
    let password = parsed
        .password()
        .map(|p| urlencoding::decode(p).map(|c| c.into_owned()).unwrap_or_else(|_| p.to_string()))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dsn_parses_clickhouse_scheme_with_credentials() {
        // Pure unit test — does not require a live CH. Just verifies the parser
        // accepts the canonical shape without panicking.
        let _client = build_client_from_dsn(
            "clickhouse://default:chpw@localhost:8124/chunkshop_test",
        )
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
