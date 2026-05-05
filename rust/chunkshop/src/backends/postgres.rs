//! Postgres backend — sqlx-based connection pool + dialect helpers.
//!
//! Mirrors `python/src/chunkshop/backends/postgres.py`. Identifier safety
//! is two-layer: regex allowlist enforced at config-load (in config.rs)
//! plus quote-doubling here (defense-in-depth — even if the regex were
//! widened, embedded `"` characters can't break out).

use std::future::Future;

use anyhow::{Context, Result};
use sqlx::{postgres::PgPoolOptions, PgPool, Postgres, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};

pub struct PostgresBackend {
    dsn_env: String,
    pool: tokio::sync::OnceCell<PgPool>,
}

impl PostgresBackend {
    pub fn new(dsn_env: String) -> Self {
        Self {
            dsn_env,
            pool: tokio::sync::OnceCell::new(),
        }
    }

    /// Lazily-initialized pool. Idempotent.
    pub async fn pool(&self) -> Result<&PgPool> {
        self.pool
            .get_or_try_init(|| async {
                let dsn = std::env::var(&self.dsn_env).with_context(|| {
                    format!("DSN env var {} not set", self.dsn_env)
                })?;
                // max_connections(1) mirrors the Python implementation's
                // short-lived per-document connection discipline (see
                // CLAUDE.md). PgSink opens one short transaction per
                // write_document, so concurrent throughput comes from
                // running multiple cells as separate processes (orchestrator),
                // not from pooling within a single process. Revisit if the
                // sink layer ever wants intra-process write concurrency.
                PgPoolOptions::new()
                    .max_connections(1)
                    .connect(&dsn)
                    .await
                    .with_context(|| format!("connecting to {}", self.dsn_env))
            })
            .await
    }
}

impl BackendDialect for PostgresBackend {
    const NAME: &'static str = "postgres";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        // Defense-in-depth: even with the regex allowlist at config-load
        // refusing characters outside [a-z0-9_], we still double-quote any
        // embedded `"` in case the regex is ever widened.
        format!("\"{}\"", name.replace('"', "\"\""))
    }

    fn fq_table(&self, db: &str, table: &str) -> String {
        format!("{}.{}", self.quote_ident(db), self.quote_ident(table))
    }

    // --- remaining methods land in Tasks 5–9. Stubs below to keep crate compiling. ---

    fn vector_type_ddl(&self, dim: usize) -> String {
        format!("vector({dim})")
    }
    fn json_type_ddl(&self) -> String {
        "jsonb".to_string()
    }
    fn tags_array_type_ddl(&self) -> String {
        "text[]".to_string()
    }
    fn text_pk_type_ddl(&self) -> String {
        "text".to_string()
    }
    fn timestamp_now_default_ddl(&self) -> String {
        "timestamptz NOT NULL DEFAULT now()".to_string()
    }
    fn vector_literal(&self, arr: &[f32]) -> String {
        let parts: Vec<String> = arr.iter().map(|x| format!("{x:.6}")).collect();
        format!("[{}]", parts.join(","))
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }
    fn json_path_sql(&self, _col_expr: &str, _dotted_path: &str) -> String { unimplemented!("Task 7") }
    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String { unimplemented!("Task 7") }
    fn create_database_sql(&self, _name: &str) -> String { unimplemented!("Task 8") }
    fn add_column_if_not_exists_sql(&self, _fq: &str, _col: &str, _type_ddl: &str) -> String { unimplemented!("Task 8") }
    fn drop_table_sql(&self, _fq: &str) -> String { unimplemented!("Task 8") }
    fn emit_chunks_table_ddl(
        &self, _fq: &str, _cols: &[ColSpec], _hnsw: bool, _dim: usize, _engine: Option<&str>,
    ) -> Vec<String> { unimplemented!("Task 9") }
}

impl BackendConn for PostgresBackend {
    fn connect(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let _ = self.pool().await?;
            Ok(())
        }
    }

    fn acquire_create_lock(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _key: &str,
    ) -> impl Future<Output = Result<()>> + Send {
        async move { unimplemented!("Task 10") }
    }

    fn table_exists(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _db: &str,
        _table: &str,
    ) -> impl Future<Output = Result<bool>> + Send {
        async move { unimplemented!("Task 10") }
    }

    fn embedding_dim(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _db: &str,
        _table: &str,
    ) -> impl Future<Output = Result<Option<usize>>> + Send {
        async move { unimplemented!("Task 10") }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backend() -> PostgresBackend {
        PostgresBackend::new("UNUSED_FOR_DIALECT_TESTS".to_string())
    }

    #[test]
    fn quote_ident_wraps_in_double_quotes() {
        let b = backend();
        assert_eq!(b.quote_ident("my_table"), "\"my_table\"");
    }

    #[test]
    fn quote_ident_doubles_embedded_double_quote() {
        let b = backend();
        // Defense-in-depth: even though the config-load regex disallows `"`,
        // we still escape it here.
        assert_eq!(b.quote_ident("a\"b"), "\"a\"\"b\"");
    }

    #[test]
    fn fq_table_quotes_both_segments() {
        let b = backend();
        assert_eq!(b.fq_table("my_db", "my_table"), "\"my_db\".\"my_table\"");
    }

    #[test]
    fn vector_type_ddl() {
        let b = backend();
        assert_eq!(b.vector_type_ddl(384), "vector(384)");
        assert_eq!(b.vector_type_ddl(1024), "vector(1024)");
    }

    #[test]
    fn json_type_ddl_is_jsonb() {
        let b = backend();
        assert_eq!(b.json_type_ddl(), "jsonb");
    }

    #[test]
    fn tags_array_type_ddl_is_text_array() {
        let b = backend();
        assert_eq!(b.tags_array_type_ddl(), "text[]");
    }

    #[test]
    fn text_pk_type_ddl_is_text() {
        let b = backend();
        assert_eq!(b.text_pk_type_ddl(), "text");
    }

    #[test]
    fn timestamp_now_default_ddl() {
        let b = backend();
        assert_eq!(
            b.timestamp_now_default_ddl(),
            "timestamptz NOT NULL DEFAULT now()"
        );
    }

    #[test]
    fn vector_literal_format_matches_python() {
        let b = backend();
        // Mirrors Python's PostgresBackend.vector_literal:
        //   "[" + ",".join(f"{x:.6f}" for x in arr) + "]"
        let v = vec![0.1_f32, 0.2_f32, -0.3_f32];
        let lit = b.vector_literal(&v);
        assert_eq!(lit, "[0.100000,0.200000,-0.300000]");
    }

    #[test]
    fn vector_literal_empty() {
        let b = backend();
        assert_eq!(b.vector_literal(&[]), "[]");
    }

    #[test]
    fn json_literal_canonical_form() {
        let b = backend();
        let v = serde_json::json!({"k": "v", "n": 1});
        let lit = b.json_literal(&v);
        // Order is implementation-defined; assert structure via re-parse.
        let reparsed: serde_json::Value = serde_json::from_str(&lit).unwrap();
        assert_eq!(reparsed["k"], "v");
        assert_eq!(reparsed["n"], 1);
    }
}
