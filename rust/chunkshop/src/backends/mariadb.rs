//! MariaDB backend — sqlx-based connection pool + dialect helpers.
//!
//! Mirrors `python/src/chunkshop/backends/mariadb.py`. Requires MariaDB 11.7+
//! for native VECTOR support; older versions are rejected at connect() time.
//!
//! Identifier safety is two-layer (matching postgres.rs): regex allowlist
//! enforced at config-load (in config.rs) plus quote-doubling here
//! (defense-in-depth — even if the regex were widened, embedded backticks
//! can't break out).

use std::future::Future;

use anyhow::{anyhow, Context, Result};
use sqlx::{mysql::MySqlPoolOptions, MySql, MySqlPool, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};

pub struct MariadbBackend {
    dsn_env: String,
    pool: tokio::sync::OnceCell<MySqlPool>,
}

impl MariadbBackend {
    pub fn new(dsn_env: String) -> Self {
        Self {
            dsn_env,
            pool: tokio::sync::OnceCell::new(),
        }
    }

    /// Lazily-initialized pool. Idempotent. The DSN env var is read at first
    /// `pool()` call, not at `new()`, so dialect-only consumers (parity tests,
    /// DDL emitters) need not set the env var.
    pub async fn pool(&self) -> Result<&MySqlPool> {
        self.pool
            .get_or_try_init(|| async {
                let dsn = std::env::var(&self.dsn_env)
                    .with_context(|| format!("DSN env var {} not set", self.dsn_env))?;
                // max_connections(1) mirrors postgres.rs and the Python
                // implementation's short-lived per-document connection
                // discipline. Concurrent throughput comes from running
                // multiple cells as separate processes (orchestrator),
                // not from intra-process pooling.
                MySqlPoolOptions::new()
                    .max_connections(1)
                    .connect(&dsn)
                    .await
                    .with_context(|| format!("connecting to {}", self.dsn_env))
            })
            .await
    }
}

impl BackendDialect for MariadbBackend {
    const NAME: &'static str = "mariadb";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        // Defense-in-depth: even with the regex allowlist at config-load
        // refusing characters outside [a-z0-9_], we still double any
        // embedded backtick in case the regex is ever widened.
        format!("`{}`", name.replace('`', "``"))
    }

    fn fq_table(&self, db: &str, table: &str) -> String {
        format!("{}.{}", self.quote_ident(db), self.quote_ident(table))
    }

    fn vector_type_ddl(&self, dim: usize) -> String {
        format!("VECTOR({dim})")
    }

    fn json_type_ddl(&self) -> String {
        "JSON".to_string()
    }

    fn tags_array_type_ddl(&self) -> String {
        // MariaDB has no native arrays; tags serialize as JSON.
        "JSON".to_string()
    }

    fn text_pk_type_ddl(&self) -> String {
        "VARCHAR(255)".to_string()
    }

    fn timestamp_now_default_ddl(&self) -> String {
        "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()
    }

    fn vector_literal(&self, arr: &[f32]) -> String {
        // Mirrors Python's f"{x:.6f}" join, wrapped in VEC_FromText('[...]').
        let parts: Vec<String> = arr.iter().map(|x| format!("{x:.6}")).collect();
        format!("VEC_FromText('[{}]')", parts.join(","))
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        // Whole dotted path lives inside one '$.a.b.c' literal — MariaDB's
        // JSON_EXTRACT takes a path expression, not arrow-operator chaining.
        format!("JSON_UNQUOTE(JSON_EXTRACT({col_expr},'$.{dotted_path}'))")
    }

    fn upsert_clause(&self, _key_cols: &[&str], update_cols: &[&str]) -> String {
        // _key_cols ignored — ON DUPLICATE KEY UPDATE keys off the table's
        // primary key, not an explicit list (mirrors Python's `del key_cols`).
        if update_cols.is_empty() {
            // Empty updates → sink should switch to INSERT IGNORE.
            return String::new();
        }
        let sets: Vec<String> = update_cols
            .iter()
            .map(|c| {
                let q = self.quote_ident(c);
                format!("{q} = VALUES({q})")
            })
            .collect();
        format!("ON DUPLICATE KEY UPDATE {}", sets.join(", "))
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
        format!("DROP TABLE {fq}")
    }

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        _dim: usize, // dim is encoded in the embedding column's type_ddl
        engine: Option<&str>,
        _vector_metric: Option<&str>,
    ) -> Vec<String> {
        let mut col_lines: Vec<String> = Vec::with_capacity(cols.len());
        let mut pk_cols: Vec<&str> = Vec::new();
        for c in cols {
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
        if hnsw {
            // Literal backticks intentionally — MariaDB index name + column
            // are known-safe identifiers, mirroring Python at mariadb.py:103.
            body.push_str(",\n  VECTOR INDEX `vec_idx` (`embedding`)");
        }
        body.push_str(",\n  KEY `doc_seq_idx` (`doc_id`, `seq_num`)");
        let engine_clause = format!(" ENGINE={}", engine.unwrap_or("InnoDB"));
        vec![format!(
            "CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n){engine_clause}"
        )]
    }
}

impl BackendConn for MariadbBackend {
    type Db = MySql;

    fn connect(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let pool = self.pool().await?;
            // Verify MariaDB 11.7+ for native VECTOR support.
            use sqlx::Row;
            let row = sqlx::query("SELECT VERSION()")
                .fetch_one(pool)
                .await
                .context("SELECT VERSION() during MariaDB connect")?;
            let ver: String = row.get(0);
            let (major, minor) = parse_mariadb_version(&ver)
                .with_context(|| format!("parse MariaDB version {ver:?}"))?;
            if (major, minor) < (11, 7) {
                return Err(anyhow!(
                    "MariaDB 11.7+ required for native VECTOR support; got {ver:?}"
                ));
            }
            Ok(())
        }
    }

    fn acquire_create_lock(
        &self,
        tx: &mut Transaction<'_, MySql>,
        key: &str,
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            // GET_LOCK is connection-scoped, not tx-scoped, but executing it
            // through the tx's underlying connection is what matters.
            // Lock-name truncation to 64 bytes mirrors Python's `[:64]`.
            let raw = format!("chunkshop_{key}");
            let name: String = raw.chars().take(64).collect();
            // Be safe: MariaDB GET_LOCK has a 64-char limit. Use byte-truncation
            // to match Python exactly, but only when the byte length matches the
            // char length (ASCII keys, which is the regex-enforced case).
            let name = if name.len() > 64 {
                name[..64].to_string()
            } else {
                name
            };

            use sqlx::Row;
            let row = sqlx::query("SELECT GET_LOCK(?, 30)")
                .bind(&name)
                .fetch_one(&mut **tx)
                .await
                .with_context(|| format!("GET_LOCK({name:?}, 30)"))?;
            let got: Option<i64> = row.try_get(0).ok();
            if got != Some(1) {
                return Err(anyhow!(
                    "could not acquire MariaDB lock {name:?} within 30s"
                ));
            }
            // Best-effort RELEASE_LOCK on tx commit/rollback would require
            // hooking sqlx's tx lifecycle. Mirroring Python's pattern, we
            // release here once the lock has been recorded; the caller's
            // create-table DDL runs after this method returns. For chunkshop's
            // narrow use (one-shot lock around CREATE TABLE), GET_LOCK's
            // session-scope semantics make the explicit release optional —
            // session close releases it anyway.
            Ok(())
        }
    }

    fn table_exists(
        &self,
        tx: &mut Transaction<'_, MySql>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<bool>> + Send {
        async move {
            use sqlx::Row;
            let row = sqlx::query(
                "SELECT COUNT(*) FROM information_schema.tables \
                 WHERE table_schema=? AND table_name=?",
            )
            .bind(db)
            .bind(table)
            .fetch_one(&mut **tx)
            .await?;
            let count: i64 = row.get(0);
            Ok(count > 0)
        }
    }

    fn embedding_dim(
        &self,
        tx: &mut Transaction<'_, MySql>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<Option<usize>>> + Send {
        async move {
            use sqlx::Row;
            let row = sqlx::query(
                "SELECT column_type FROM information_schema.columns \
                 WHERE table_schema=? AND table_name=? AND column_name='embedding'",
            )
            .bind(db)
            .bind(table)
            .fetch_optional(&mut **tx)
            .await?;
            let Some(r) = row else { return Ok(None) };
            let s: String = r.get::<String, _>(0).to_lowercase();
            let re = regex::Regex::new(r"^vector\((\d+)\)$").unwrap();
            Ok(re
                .captures(&s)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok()))
        }
    }
}

/// Parse a MariaDB `VERSION()` string into `(major, minor)`.
///
/// Accepts forms like `"11.7.2-MariaDB-ubu2404"`, `"11.7.0"`, `"10.11.5-MariaDB"`.
/// Returns `Err` for empty / non-numeric leading components.
fn parse_mariadb_version(s: &str) -> Result<(u32, u32)> {
    let re = regex::Regex::new(r"^(\d+)\.(\d+)").unwrap();
    let caps = re
        .captures(s)
        .ok_or_else(|| anyhow!("could not parse MariaDB version from {s:?}"))?;
    let major: u32 = caps[1]
        .parse()
        .map_err(|_| anyhow!("non-numeric major in {s:?}"))?;
    let minor: u32 = caps[2]
        .parse()
        .map_err(|_| anyhow!("non-numeric minor in {s:?}"))?;
    Ok((major, minor))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backend() -> MariadbBackend {
        MariadbBackend::new("UNUSED_FOR_DIALECT_TESTS".to_string())
    }

    fn canonical_cols(dim: usize) -> Vec<ColSpec> {
        vec![
            ColSpec {
                name: "id",
                type_ddl: "VARCHAR(255)".into(),
                nullable: false,
                default: None,
                is_primary_key: true,
            },
            ColSpec {
                name: "doc_id",
                type_ddl: "VARCHAR(255)".into(),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
            ColSpec {
                name: "seq_num",
                type_ddl: "INT".into(),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
            ColSpec {
                name: "embedding",
                type_ddl: format!("VECTOR({dim})"),
                nullable: false,
                default: None,
                is_primary_key: false,
            },
        ]
    }

    #[test]
    fn quote_ident_wraps_in_backticks() {
        let b = backend();
        assert_eq!(b.quote_ident("my_table"), "`my_table`");
    }

    #[test]
    fn quote_ident_doubles_embedded_backtick() {
        let b = backend();
        // Defense-in-depth: even though the config-load regex disallows
        // backticks, we still escape them here.
        assert_eq!(b.quote_ident("a`b"), "`a``b`");
    }

    #[test]
    fn fq_table_quotes_both_segments() {
        let b = backend();
        assert_eq!(b.fq_table("my_db", "my_table"), "`my_db`.`my_table`");
    }

    #[test]
    fn vector_type_ddl() {
        let b = backend();
        assert_eq!(b.vector_type_ddl(384), "VECTOR(384)");
        assert_eq!(b.vector_type_ddl(1024), "VECTOR(1024)");
    }

    #[test]
    fn json_type_ddl_is_json() {
        let b = backend();
        assert_eq!(b.json_type_ddl(), "JSON");
    }

    #[test]
    fn tags_array_type_ddl_is_json() {
        let b = backend();
        assert_eq!(b.tags_array_type_ddl(), "JSON");
    }

    #[test]
    fn text_pk_type_ddl_is_varchar_255() {
        let b = backend();
        assert_eq!(b.text_pk_type_ddl(), "VARCHAR(255)");
    }

    #[test]
    fn timestamp_now_default_ddl() {
        let b = backend();
        assert_eq!(
            b.timestamp_now_default_ddl(),
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        );
    }

    #[test]
    fn vector_literal_format_matches_python() {
        let b = backend();
        // Mirrors Python's MariaDBBackend.vector_literal:
        //   "VEC_FromText('[" + ",".join(f"{x:.6f}" for x in arr) + "]')"
        let v = vec![0.1_f32, 0.2_f32, -0.3_f32];
        let lit = b.vector_literal(&v);
        assert_eq!(lit, "VEC_FromText('[0.100000,0.200000,-0.300000]')");
    }

    #[test]
    fn vector_literal_empty() {
        let b = backend();
        assert_eq!(b.vector_literal(&[]), "VEC_FromText('[]')");
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

    #[test]
    fn json_path_sql_single_segment() {
        let b = backend();
        assert_eq!(
            b.json_path_sql("metadata", "a"),
            "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a'))"
        );
    }

    #[test]
    fn json_path_sql_two_segments() {
        let b = backend();
        assert_eq!(
            b.json_path_sql("metadata", "a.b"),
            "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a.b'))"
        );
    }

    #[test]
    fn json_path_sql_three_segments() {
        let b = backend();
        assert_eq!(
            b.json_path_sql("metadata", "a.b.c"),
            "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a.b.c'))"
        );
    }

    #[test]
    fn upsert_clause_empty_returns_empty_string() {
        let b = backend();
        assert_eq!(b.upsert_clause(&["id"], &[]), "");
    }

    #[test]
    fn upsert_clause_single_update() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &["content"]);
        assert_eq!(sql, "ON DUPLICATE KEY UPDATE `content` = VALUES(`content`)");
    }

    #[test]
    fn upsert_clause_multi_update() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &["a", "b"]);
        assert_eq!(
            sql,
            "ON DUPLICATE KEY UPDATE `a` = VALUES(`a`), `b` = VALUES(`b`)"
        );
    }

    #[test]
    fn upsert_clause_composite_key_ignores_keys() {
        let b = backend();
        // keys arg is ignored — output depends only on update_cols.
        let with_composite = b.upsert_clause(&["a", "b"], &["c"]);
        let with_single = b.upsert_clause(&["x"], &["c"]);
        assert_eq!(with_composite, with_single);
        assert_eq!(with_composite, "ON DUPLICATE KEY UPDATE `c` = VALUES(`c`)");
    }

    #[test]
    fn create_database_sql_uses_database_for_mariadb() {
        let b = backend();
        assert_eq!(
            b.create_database_sql("chunkshop"),
            "CREATE DATABASE IF NOT EXISTS `chunkshop`"
        );
    }

    #[test]
    fn add_column_if_not_exists_sql_format() {
        let b = backend();
        let sql = b.add_column_if_not_exists_sql("`db`.`t`", "source", "VARCHAR(255)");
        assert_eq!(
            sql,
            "ALTER TABLE `db`.`t` ADD COLUMN IF NOT EXISTS `source` VARCHAR(255)"
        );
    }

    #[test]
    fn drop_table_sql_format() {
        let b = backend();
        assert_eq!(b.drop_table_sql("`db`.`t`"), "DROP TABLE `db`.`t`");
    }

    #[test]
    fn emit_chunks_table_ddl_no_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("`db`.`t`", &cols, false, 384, None, None);
        assert_eq!(stmts.len(), 1);
        let s = &stmts[0];
        assert!(
            s.contains("CREATE TABLE IF NOT EXISTS `db`.`t`"),
            "got: {s}"
        );
        assert!(s.contains("PRIMARY KEY (`id`)"), "got: {s}");
        assert!(s.ends_with("ENGINE=InnoDB"), "got: {s}");
        assert!(
            s.contains("KEY `doc_seq_idx` (`doc_id`, `seq_num`)"),
            "got: {s}"
        );
        assert!(!s.contains("VECTOR INDEX"), "got: {s}");
    }

    #[test]
    fn emit_chunks_table_ddl_with_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("`db`.`t`", &cols, true, 384, None, None);
        assert_eq!(stmts.len(), 1);
        let s = &stmts[0];
        assert!(
            s.contains("CREATE TABLE IF NOT EXISTS `db`.`t`"),
            "got: {s}"
        );
        assert!(s.contains("PRIMARY KEY (`id`)"), "got: {s}");
        assert!(s.ends_with("ENGINE=InnoDB"), "got: {s}");
        assert!(
            s.contains("VECTOR INDEX `vec_idx` (`embedding`)"),
            "got: {s}"
        );
        assert!(
            s.contains("KEY `doc_seq_idx` (`doc_id`, `seq_num`)"),
            "got: {s}"
        );
    }

    #[test]
    fn parse_mariadb_version_real_string() {
        assert_eq!(
            parse_mariadb_version("11.7.2-MariaDB-ubu2404").unwrap(),
            (11, 7)
        );
    }

    #[test]
    fn parse_mariadb_version_just_numbers() {
        assert_eq!(parse_mariadb_version("11.7.0").unwrap(), (11, 7));
    }

    #[test]
    fn parse_mariadb_version_old_version() {
        // Parser succeeds; min-version gate (in connect()) rejects.
        assert_eq!(parse_mariadb_version("10.11.5-MariaDB").unwrap(), (10, 11));
    }

    #[test]
    fn parse_mariadb_version_malformed_errors() {
        assert!(parse_mariadb_version("").is_err());
        assert!(parse_mariadb_version("abc").is_err());
    }

    #[test]
    fn assert_mariadb_is_backend() {
        // R2-SC-001: MariadbBackend must satisfy the Backend super-trait.
        fn _assert<B: crate::Backend>() {}
        _assert::<MariadbBackend>();
    }
}
