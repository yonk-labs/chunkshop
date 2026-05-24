//! SQLite backend (with sqlite-vec extension for vector storage).
//!
//! SQLite has no schema/database namespace concept — chunkshop's YAML `database`
//! field is required by config (loose parity) but ignored at runtime. The DSN
//! env var holds the file path or `:memory:`. Mirrors
//! `python/src/chunkshop/backends/sqlite.py`.
//!
//! `BackendDialect` is impl'd here. Connection-management methods (connect,
//! table_exists, embedding_dim, with_create_lock) live as inherent async methods
//! on `SQLiteBackend` — see Task 7. They do NOT impl the GAT-shaped
//! `BackendConn` trait (R2 lift) because rusqlite is not a `sqlx::Database`
//! (R3 Mission Brief, R3-SC-001).

use std::sync::{Arc, OnceLock};

use anyhow::{Context, Result};
use rusqlite::Connection;
use tokio::sync::Mutex;
use tokio::task::spawn_blocking;

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

/// Registers `sqlite_vec::sqlite3_vec_init` as an auto-extension exactly once
/// per process. After this is called, every new `rusqlite::Connection` opened
/// via `Connection::open(...)` automatically loads sqlite-vec.
fn register_sqlite_vec_once() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        // SAFETY: sqlite-vec's init function is C-callable; the cast matches
        // the expected sqlite3_auto_extension signature. This is the
        // documented integration pattern.
        unsafe {
            let _ = rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
                sqlite_vec::sqlite3_vec_init as *const (),
            )));
        }
    });
}

/// `Arc<Mutex<rusqlite::Connection>>` is the canonical shared-connection shape
/// used by the sink and source. `rusqlite::Connection` is `!Sync` so we wrap it
/// in `tokio::sync::Mutex` rather than `std::sync::Mutex` (we hold across
/// `.await` points in some test scenarios).
pub type SqliteConn = Arc<Mutex<Connection>>;

impl SQLiteBackend {
    /// Open a connection to the configured DB. Reads the path from the env var
    /// at the moment of the call (mirrors Python). Sets WAL on best-effort.
    /// Idempotent at the auto-extension level — sqlite-vec is registered once
    /// per process via `OnceLock`.
    pub async fn connect(&self) -> Result<SqliteConn> {
        let dsn_env = self.dsn_env.clone();
        spawn_blocking(move || -> Result<SqliteConn> {
            register_sqlite_vec_once();
            let path = std::env::var(&dsn_env)
                .with_context(|| format!("DSN env var {dsn_env} not set"))?;
            let conn = if path == ":memory:" {
                Connection::open_in_memory().context("open :memory:")?
            } else {
                Connection::open(&path).with_context(|| format!("opening {path}"))?
            };
            // Best-effort WAL — same as Python (`except sqlite3.DatabaseError: pass`).
            let _ = conn.pragma_update(None, "journal_mode", &"WAL");
            Ok(Arc::new(Mutex::new(conn)))
        })
        .await
        .context("spawn_blocking connect")?
    }

    /// Mirrors Python's `table_exists` — checks `sqlite_master` for table or
    /// virtual table by name. The `db` argument is dropped (no schemas).
    pub async fn table_exists(&self, conn: &SqliteConn, _db: &str, table: &str) -> Result<bool> {
        let conn = conn.clone();
        let table = table.to_string();
        spawn_blocking(move || -> Result<bool> {
            let g = conn.blocking_lock();
            let r: Option<i32> = g
                .query_row(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
                    rusqlite::params![table],
                    |row| row.get(0),
                )
                .ok();
            Ok(r.is_some())
        })
        .await
        .context("spawn_blocking table_exists")?
    }

    /// Read the FLOAT[N] dim from the vec0 partner table's CREATE statement
    /// in `sqlite_master`. Returns None when the partner table doesn't exist
    /// or doesn't have a FLOAT[N] column.
    pub async fn embedding_dim(
        &self,
        conn: &SqliteConn,
        _db: &str,
        table: &str,
    ) -> Result<Option<usize>> {
        let conn = conn.clone();
        let vec_table = format!("{table}_vec");
        spawn_blocking(move || -> Result<Option<usize>> {
            let g = conn.blocking_lock();
            let sql: Option<String> = g
                .query_row(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    rusqlite::params![vec_table],
                    |row| row.get(0),
                )
                .ok();
            let Some(sql) = sql else { return Ok(None) };
            let re = regex::Regex::new(r"(?i)FLOAT\[(\d+)\]").unwrap();
            Ok(re
                .captures(&sql)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok()))
        })
        .await
        .context("spawn_blocking embedding_dim")?
    }

    /// SQLite has no advisory-lock primitive. Mirror Python's no-op.
    pub async fn with_create_lock(&self, _conn: &SqliteConn, _key: &str) -> Result<()> {
        Ok(())
    }
}

impl BackendDialect for SQLiteBackend {
    const NAME: &'static str = "sqlite";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        format!("\"{}\"", name.replace('"', "\"\""))
    }

    fn fq_table(&self, _db: &str, table: &str) -> String {
        // No schemas in SQLite. Mirror Python: `del db; return self.quote_ident(table)`.
        self.quote_ident(table)
    }

    fn vector_type_ddl(&self, dim: usize) -> String {
        format!("FLOAT[{dim}]")
    }
    fn json_type_ddl(&self) -> String {
        "TEXT".to_string()
    }
    fn tags_array_type_ddl(&self) -> String {
        "TEXT".to_string()
    }
    fn text_pk_type_ddl(&self) -> String {
        "TEXT".to_string()
    }
    fn timestamp_now_default_ddl(&self) -> String {
        "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()
    }

    fn vector_literal(&self, arr: &[f32]) -> String {
        // Python: json.dumps([float(x) for x in arr]) — produces a JSON array
        // string like "[0.1, 0.2, -0.3]" (comma+space separator). Match that
        // shape via serde_json so cross-language byte parity holds.
        let v: Vec<f64> = arr.iter().map(|x| *x as f64).collect();
        serde_json::to_string(&v).unwrap_or_else(|_| "[]".to_string())
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        format!("json_extract({col_expr},'$.{dotted_path}')")
    }

    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String {
        let keys: Vec<String> = key_cols.iter().map(|c| self.quote_ident(c)).collect();
        let keys_sql = keys.join(", ");
        if update_cols.is_empty() {
            return format!("ON CONFLICT ({keys_sql}) DO NOTHING");
        }
        let sets: Vec<String> = update_cols
            .iter()
            .map(|c| format!("{q} = excluded.{q}", q = self.quote_ident(c)))
            .collect();
        format!("ON CONFLICT ({keys_sql}) DO UPDATE SET {}", sets.join(", "))
    }

    fn create_database_sql(&self, _name: &str) -> String {
        "SELECT 1 -- chunkshop: SQLite has no database/schema concept".to_string()
    }

    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        // SQLite has no IF NOT EXISTS on ALTER TABLE; the sink catches the
        // duplicate-column error for idempotency.
        format!(
            "ALTER TABLE {fq} ADD COLUMN {} {type_ddl}",
            self.quote_ident(col)
        )
    }

    fn drop_table_sql(&self, fq: &str) -> String {
        format!("DROP TABLE {fq}")
    }

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        _hnsw: bool,
        dim: usize,
        _engine: Option<&str>,
        _vector_metric: Option<&str>,
    ) -> Vec<String> {
        // Python parity: split the embedding column out into a vec0 virtual
        // table joined by id. The main table holds everything else.
        let main_cols: Vec<&ColSpec> = cols.iter().filter(|c| c.name != "embedding").collect();

        let mut col_lines: Vec<String> = Vec::with_capacity(main_cols.len());
        let mut pk_cols: Vec<&str> = Vec::new();
        for c in &main_cols {
            let mut line = format!("  {} {}", self.quote_ident(c.name), c.type_ddl);
            if let Some(default) = c.default {
                line.push_str(&format!(" DEFAULT {default}"));
            }
            if !c.nullable {
                line.push_str(" NOT NULL");
            }
            col_lines.push(line);
            if c.is_primary_key {
                pk_cols.push(c.name);
            }
        }
        let mut body = col_lines.join(",\n");
        if !pk_cols.is_empty() {
            let pk: Vec<String> = pk_cols.iter().map(|c| self.quote_ident(c)).collect();
            body.push_str(&format!(",\n  PRIMARY KEY ({})", pk.join(", ")));
        }
        let create_main = format!("CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n)");

        // Strip outer quotes for index/vec table naming: "chunks" → chunks.
        let bare = fq.trim_matches('"').to_string();

        let create_idx = format!(
            "CREATE INDEX IF NOT EXISTS {} ON {fq} (\"doc_id\", \"seq_num\")",
            self.quote_ident(&format!("{bare}_doc_seq_idx"))
        );

        let vec_fq = self.quote_ident(&format!("{bare}_vec"));
        let create_vec = format!(
            "CREATE VIRTUAL TABLE IF NOT EXISTS {vec_fq} USING vec0(\
             id TEXT PRIMARY KEY, embedding FLOAT[{dim}])"
        );

        vec![create_main, create_idx, create_vec]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backends::base::ColSpec;

    fn backend() -> SQLiteBackend {
        SQLiteBackend::new("UNUSED".to_string())
    }

    #[test]
    fn quote_ident_wraps_in_double_quotes() {
        assert_eq!(backend().quote_ident("my_table"), "\"my_table\"");
    }

    #[test]
    fn quote_ident_doubles_embedded_quote() {
        assert_eq!(backend().quote_ident("a\"b"), "\"a\"\"b\"");
    }

    #[test]
    fn fq_table_returns_table_only_no_schema() {
        // SQLite has no schema concept — db arg is dropped.
        assert_eq!(backend().fq_table("ignored", "my_table"), "\"my_table\"");
    }

    #[test]
    fn vector_type_ddl_uses_float_brackets() {
        assert_eq!(backend().vector_type_ddl(384), "FLOAT[384]");
    }

    #[test]
    fn json_type_is_text() {
        assert_eq!(backend().json_type_ddl(), "TEXT");
    }

    #[test]
    fn tags_array_type_is_text() {
        assert_eq!(backend().tags_array_type_ddl(), "TEXT");
    }

    #[test]
    fn text_pk_type_is_text() {
        assert_eq!(backend().text_pk_type_ddl(), "TEXT");
    }

    #[test]
    fn timestamp_default_is_current_timestamp() {
        assert_eq!(
            backend().timestamp_now_default_ddl(),
            "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        );
    }

    #[test]
    fn vector_literal_matches_python_json_array() {
        let v = vec![0.1_f32, 0.2_f32, -0.3_f32];
        let lit = backend().vector_literal(&v);
        let parsed: serde_json::Value = serde_json::from_str(&lit).unwrap();
        let arr = parsed.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert!((arr[0].as_f64().unwrap() - 0.1).abs() < 1e-6);
        assert!((arr[2].as_f64().unwrap() - (-0.3)).abs() < 1e-6);
    }

    #[test]
    fn json_path_sql_uses_json_extract_with_dollar_dot() {
        assert_eq!(
            backend().json_path_sql("metadata", "a.b.c"),
            "json_extract(metadata,'$.a.b.c')"
        );
    }

    #[test]
    fn upsert_clause_do_nothing_when_no_updates() {
        assert_eq!(
            backend().upsert_clause(&["id"], &[]),
            "ON CONFLICT (\"id\") DO NOTHING"
        );
    }

    #[test]
    fn upsert_clause_excluded_form() {
        assert_eq!(
            backend().upsert_clause(&["id"], &["content", "metadata"]),
            "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = excluded.\"content\", \
             \"metadata\" = excluded.\"metadata\""
        );
    }

    #[test]
    fn create_database_sql_is_noop_select() {
        assert_eq!(
            backend().create_database_sql("ignored"),
            "SELECT 1 -- chunkshop: SQLite has no database/schema concept"
        );
    }

    #[test]
    fn add_column_lacks_if_not_exists() {
        assert_eq!(
            backend().add_column_if_not_exists_sql("\"chunks\"", "source", "TEXT"),
            "ALTER TABLE \"chunks\" ADD COLUMN \"source\" TEXT"
        );
    }

    fn canonical_cols(dim: usize) -> Vec<ColSpec> {
        vec![
            ColSpec {
                name: "id",
                type_ddl: "TEXT".into(),
                nullable: false,
                default: None,
                is_primary_key: true,
            },
            ColSpec {
                name: "doc_id",
                type_ddl: "TEXT".into(),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
            ColSpec {
                name: "seq_num",
                type_ddl: "INTEGER".into(),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
            ColSpec {
                name: "embedding",
                type_ddl: format!("FLOAT[{dim}]"),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
        ]
    }

    #[test]
    fn emit_chunks_table_ddl_returns_three_statements() {
        let stmts = backend().emit_chunks_table_ddl(
            "\"chunks\"",
            &canonical_cols(384),
            false,
            384,
            None,
            None,
        );
        assert_eq!(stmts.len(), 3, "main table + index + vec0 virtual table");
        assert!(stmts[0].starts_with("CREATE TABLE IF NOT EXISTS \"chunks\""));
        assert!(stmts[0].contains("\"id\" TEXT NOT NULL"));
        assert!(stmts[0].contains("PRIMARY KEY (\"id\")"));
        // embedding column is split out — must NOT appear in the main DDL.
        assert!(!stmts[0].contains("\"embedding\" FLOAT"));
        assert!(stmts[1].contains("CREATE INDEX IF NOT EXISTS \"chunks_doc_seq_idx\""));
        assert!(stmts[2].starts_with("CREATE VIRTUAL TABLE IF NOT EXISTS \"chunks_vec\""));
        assert!(stmts[2].contains("USING vec0("));
        assert!(stmts[2].contains("FLOAT[384]"));
    }

    #[test]
    fn emit_chunks_table_ddl_hnsw_does_not_change_output() {
        // SQLite's HNSW is a no-op at the DDL level — same statements as without.
        let no = backend().emit_chunks_table_ddl("\"c\"", &canonical_cols(8), false, 8, None, None);
        let yes = backend().emit_chunks_table_ddl("\"c\"", &canonical_cols(8), true, 8, None, None);
        assert_eq!(no, yes);
    }
}
