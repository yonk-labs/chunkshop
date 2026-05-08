//! SQLite backend (placeholder — Tasks 4 + 7 fill in BackendDialect bodies and
//! the inherent connection methods). Stubs are intentionally minimal but
//! type-checking so the workspace compiles.
//!
//! `SQLiteBackend` impls `BackendDialect` only. Connection-management methods
//! (`connect`, `table_exists`, `embedding_dim`, `with_create_lock`) live as
//! inherent `async` methods — NOT on the GAT-shaped `BackendConn` trait
//! introduced by R2 — because rusqlite is not a sqlx::Database.
//! See R3 Mission Brief, R3-SC-001.

use crate::backends::base::{BackendDialect, ColSpec};

#[derive(Clone)]
pub struct SQLiteBackend {
    pub(crate) dsn_env: String,
}

impl SQLiteBackend {
    pub fn new(dsn_env: String) -> Self {
        Self { dsn_env }
    }
}

impl BackendDialect for SQLiteBackend {
    const NAME: &'static str = "sqlite";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        format!("\"{}\"", name.replace('"', "\"\""))
    }
    fn fq_table(&self, _db: &str, table: &str) -> String { self.quote_ident(table) }
    fn vector_type_ddl(&self, dim: usize) -> String { format!("FLOAT[{dim}]") }
    fn json_type_ddl(&self) -> String { "TEXT".to_string() }
    fn tags_array_type_ddl(&self) -> String { "TEXT".to_string() }
    fn text_pk_type_ddl(&self) -> String { "TEXT".to_string() }
    fn timestamp_now_default_ddl(&self) -> String {
        "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()
    }
    fn vector_literal(&self, _arr: &[f32]) -> String { String::new() }
    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }
    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        format!("json_extract({col_expr},'$.{dotted_path}')")
    }
    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String {
        // Task 4 fills this in.
        String::new()
    }
    fn create_database_sql(&self, _name: &str) -> String {
        "SELECT 1 -- chunkshop: SQLite has no database/schema concept".to_string()
    }
    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        format!("ALTER TABLE {fq} ADD COLUMN {} {type_ddl}", self.quote_ident(col))
    }
    fn drop_table_sql(&self, fq: &str) -> String { format!("DROP TABLE {fq}") }
    fn emit_chunks_table_ddl(
        &self, _fq: &str, _cols: &[ColSpec],
        _hnsw: bool, _dim: usize, _engine: Option<&str>,
    ) -> Vec<String> {
        // Task 4 fills this in.
        Vec::new()
    }
}
