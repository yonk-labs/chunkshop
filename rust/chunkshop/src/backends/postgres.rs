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
    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        let segs: Vec<&str> = dotted_path.split('.').collect();
        if segs.len() == 1 {
            return format!("{col_expr}->>'{}'", segs[0]);
        }
        let mut s = String::from(col_expr);
        for seg in &segs[..segs.len() - 1] {
            s.push_str(&format!("->'{seg}'"));
        }
        s.push_str(&format!("->>'{}'", segs[segs.len() - 1]));
        s
    }

    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String {
        let keys: Vec<String> = key_cols.iter().map(|c| self.quote_ident(c)).collect();
        let keys_sql = keys.join(", ");
        if update_cols.is_empty() {
            return format!("ON CONFLICT ({keys_sql}) DO NOTHING");
        }
        let sets: Vec<String> = update_cols
            .iter()
            .map(|c| format!("{q} = EXCLUDED.{q}", q = self.quote_ident(c)))
            .collect();
        format!("ON CONFLICT ({keys_sql}) DO UPDATE SET {}", sets.join(", "))
    }
    fn create_database_sql(&self, name: &str) -> String {
        format!("CREATE SCHEMA IF NOT EXISTS {}", self.quote_ident(name))
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
        _dim: usize,            // dim is encoded in the embedding column's type_ddl
        _engine: Option<&str>,  // engine clause is not applicable on PG
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
        let create = format!("CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n)");

        // Strip schema prefix from fq for index naming: "db"."t" → t
        let bare = fq
            .rsplit('.')
            .next()
            .unwrap_or(fq)
            .trim_matches('"')
            .to_string();

        let mut stmts = vec![create];
        stmts.push(format!(
            "CREATE INDEX IF NOT EXISTS {} ON {fq} (\"doc_id\", \"seq_num\")",
            self.quote_ident(&format!("{bare}_doc_seq_idx"))
        ));
        if hnsw {
            stmts.push(format!(
                "CREATE INDEX IF NOT EXISTS {} ON {fq} USING hnsw (\"embedding\" vector_cosine_ops)",
                self.quote_ident(&format!("{bare}_emb_hnsw_idx"))
            ));
        }
        stmts
    }
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
        tx: &mut Transaction<'_, Postgres>,
        key: &str,
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            // Deterministic 64-bit signed int from BLAKE2b-8 of the schema name.
            // Mirrors Python's PostgresBackend._advisory_lock_key.
            use blake2::{digest::consts::U8, Blake2b, Digest};
            let mut hasher = Blake2b::<U8>::new();
            hasher.update(key.as_bytes());
            let digest = hasher.finalize();
            let lock_key = i64::from_be_bytes(digest.into());
            sqlx::query("SELECT pg_advisory_xact_lock($1)")
                .bind(lock_key)
                .execute(&mut **tx)
                .await
                .with_context(|| format!("acquire advisory lock for {key}"))?;
            Ok(())
        }
    }

    fn table_exists(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<bool>> + Send {
        async move {
            use sqlx::Row;
            let row = sqlx::query(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename=$2)",
            )
            .bind(db)
            .bind(table)
            .fetch_one(&mut **tx)
            .await?;
            Ok(row.get::<bool, _>(0))
        }
    }

    fn embedding_dim(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<Option<usize>>> + Send {
        async move {
            use sqlx::Row;
            let row = sqlx::query(
                r#"
                SELECT format_type(atttypid, atttypmod) AS t
                FROM pg_attribute
                WHERE attrelid = (
                    SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = $1 AND n.nspname = $2
                ) AND attname = 'embedding'
                "#,
            )
            .bind(table)
            .bind(db)
            .fetch_optional(&mut **tx)
            .await?;
            let Some(r) = row else { return Ok(None) };
            let s: String = r.get("t");
            let re = regex::Regex::new(r"^vector\((\d+)\)$").unwrap();
            Ok(re
                .captures(&s)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backend() -> PostgresBackend {
        PostgresBackend::new("UNUSED_FOR_DIALECT_TESTS".to_string())
    }

    fn canonical_cols(dim: usize) -> Vec<ColSpec> {
        vec![
            ColSpec { name: "id", type_ddl: "text".into(), nullable: false, default: None, is_primary_key: true },
            ColSpec { name: "doc_id", type_ddl: "text".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "seq_num", type_ddl: "int".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "embedding", type_ddl: format!("vector({dim})"), nullable: false, default: None, is_primary_key: false },
        ]
    }

    #[test]
    fn emit_chunks_table_ddl_no_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("\"db\".\"t\"", &cols, false, 384, None);
        assert_eq!(stmts.len(), 2);
        assert!(stmts[0].starts_with("CREATE TABLE IF NOT EXISTS \"db\".\"t\""));
        assert!(stmts[0].contains("\"id\" text NOT NULL"));
        assert!(stmts[0].contains("PRIMARY KEY (\"id\")"));
        assert!(stmts[1].contains("CREATE INDEX IF NOT EXISTS \"t_doc_seq_idx\""));
        assert!(stmts[1].contains("ON \"db\".\"t\" (\"doc_id\", \"seq_num\")"));
    }

    #[test]
    fn emit_chunks_table_ddl_with_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("\"db\".\"t\"", &cols, true, 384, None);
        assert_eq!(stmts.len(), 3);
        assert!(stmts[2].contains("USING hnsw (\"embedding\" vector_cosine_ops)"));
        assert!(stmts[2].contains("\"t_emb_hnsw_idx\""));
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

    #[test]
    fn json_path_sql_single_segment() {
        let b = backend();
        assert_eq!(b.json_path_sql("metadata", "a"), "metadata->>'a'");
    }

    #[test]
    fn json_path_sql_two_segments() {
        let b = backend();
        assert_eq!(b.json_path_sql("metadata", "a.b"), "metadata->'a'->>'b'");
    }

    #[test]
    fn json_path_sql_three_segments() {
        let b = backend();
        assert_eq!(
            b.json_path_sql("metadata", "a.b.c"),
            "metadata->'a'->'b'->>'c'"
        );
    }

    #[test]
    fn upsert_clause_do_nothing_when_no_update_cols() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &[]);
        assert_eq!(sql, "ON CONFLICT (\"id\") DO NOTHING");
    }

    #[test]
    fn upsert_clause_do_update_set() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &["content", "metadata"]);
        assert_eq!(
            sql,
            "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = EXCLUDED.\"content\", \
             \"metadata\" = EXCLUDED.\"metadata\""
        );
    }

    #[test]
    fn upsert_clause_composite_key() {
        let b = backend();
        let sql = b.upsert_clause(&["a", "b"], &["c"]);
        assert_eq!(
            sql,
            "ON CONFLICT (\"a\", \"b\") DO UPDATE SET \"c\" = EXCLUDED.\"c\""
        );
    }

    #[test]
    fn create_database_sql_uses_schema_for_postgres() {
        let b = backend();
        assert_eq!(
            b.create_database_sql("chunkshop"),
            "CREATE SCHEMA IF NOT EXISTS \"chunkshop\""
        );
    }

    #[test]
    fn add_column_if_not_exists_sql_format() {
        let b = backend();
        let sql = b.add_column_if_not_exists_sql("\"db\".\"t\"", "source", "text");
        assert_eq!(
            sql,
            "ALTER TABLE \"db\".\"t\" ADD COLUMN IF NOT EXISTS \"source\" text"
        );
    }

    #[test]
    fn drop_table_sql_format() {
        let b = backend();
        assert_eq!(b.drop_table_sql("\"db\".\"t\""), "DROP TABLE \"db\".\"t\"");
    }
}
