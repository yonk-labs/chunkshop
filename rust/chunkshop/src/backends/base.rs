//! Backend traits + ColSpec.
//!
//! Backends own everything that MUST be different per backend, including DDL
//! sequencing. Sinks own chunkshop-specific data-model semantics (modes,
//! metadata promotion, delete_orphans, source-tag write-once).
//!
//! Two traits:
//! - `BackendDialect` — pure helpers, no I/O, no async. Returns String / Vec<String>.
//!   Trivially unit-testable without a tokio runtime.
//! - `BackendConn` — I/O surface. AFIT (Rust ≥1.75 stable). No `async-trait` macro,
//!   no `dyn`. Generic dispatch via `<B: Backend>`.
//!
//! R1 caveat (now discharged in R2): `BackendConn` originally took a PG-concrete
//! `&mut sqlx::Transaction<'_, sqlx::Postgres>`. R2 lifts this to a GAT
//! (`type Db: sqlx::Database`) so each backend names its own sqlx Database.
//! Sinks hold concrete backends (PgSink → PostgresBackend, MariadbSink →
//! MariadbBackend), so `<PostgresBackend as BackendConn>::Db = sqlx::Postgres`
//! resolves at the call site without sinks needing to be generic over `<B>`.

use std::future::Future;

#[derive(Debug, Clone)]
pub struct ColSpec {
    /// Compile-time constant — canonical chunkshop columns are always known
    /// at build time (`"id"`, `"doc_id"`, `"embedding"`, etc.). Promoted-
    /// metadata columns flow through `add_column_if_not_exists_sql`, not
    /// through `ColSpec`, so this never needs to be runtime-derived.
    pub name: &'static str,
    /// Backend-specific. May be runtime-computed (e.g., `format!("vector({dim})")`),
    /// hence `String` rather than `&'static str`.
    pub type_ddl: String,
    pub nullable: bool,
    pub default: Option<&'static str>,
    pub is_primary_key: bool,
}

/// Pure dialect helpers. No I/O, no async.
pub trait BackendDialect {
    const NAME: &'static str;
    const SUPPORTS_UPSERT: bool;

    fn quote_ident(&self, name: &str) -> String;
    fn fq_table(&self, db: &str, table: &str) -> String;

    fn vector_type_ddl(&self, dim: usize) -> String;
    fn json_type_ddl(&self) -> String;
    fn tags_array_type_ddl(&self) -> String;
    fn text_pk_type_ddl(&self) -> String;
    fn timestamp_now_default_ddl(&self) -> String;

    fn vector_literal(&self, arr: &[f32]) -> String;
    fn json_literal(&self, obj: &serde_json::Value) -> String;

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String;
    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String;

    fn create_database_sql(&self, name: &str) -> String;
    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String;
    fn drop_table_sql(&self, fq: &str) -> String;

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        dim: usize,
        engine: Option<&str>,
    ) -> Vec<String>;
}

/// I/O surface. R2 lifts this to a GAT (`type Db: sqlx::Database`) so each
/// backend names its own sqlx Database. PgSink/MariadbSink hold concrete
/// backends, so `<PostgresBackend as BackendConn>::Db = sqlx::Postgres`
/// resolves at the call site without sinks needing to be generic over `<B>`.
pub trait BackendConn {
    type Db: sqlx::Database;

    /// Force-initialize the connection pool. Idempotent — second call is a no-op.
    /// The DSN is sourced from the backend struct's configuration (set when the
    /// backend is constructed), not from arguments to this method.
    fn connect(&self) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn acquire_create_lock(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        key: &str,
    ) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn table_exists(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<bool>> + Send;

    fn embedding_dim(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<Option<usize>>> + Send;
}

/// Convenience super-trait: `<B: Backend>` for ergonomic generic bounds.
pub trait Backend: BackendDialect + BackendConn {}
impl<T: BackendDialect + BackendConn> Backend for T {}
