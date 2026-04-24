//! Integration test: runs a full ingest against Postgres when
//! `CHUNKSHOP_TEST_DSN` is set. Skips cleanly otherwise.

use std::env;
use std::path::PathBuf;

use chunkshop::{load_config, run_cell};

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn write_temp_config(dsn_env: &str, glob_path: &str) -> PathBuf {
    let dir = env::temp_dir().join("chunkshop-rs-parity");
    std::fs::create_dir_all(&dir).unwrap();
    let cfg = format!(
        r#"cell_name: rust_parity_handbook
source:
  type: files
  glob: {glob}
  id_from: stem
  encoding: utf-8

chunker:
  type: sentence_aware
  max_chars: 2000
  min_chars: 0

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  batch_size: 8

target:
  dsn_env: {env_var}
  schema: chunkshop_rust_parity
  table: rust_out
  mode: overwrite
  hnsw: false
"#,
        glob = glob_path,
        env_var = dsn_env,
    );
    let path = dir.join("parity.yaml");
    std::fs::write(&path, cfg).unwrap();
    path
}

#[tokio::test]
async fn ingest_fixture_to_pgvector() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping parity test");
            return;
        }
    };
    // Point at the per-test fixture dir via a glob that only matches our file.
    let fixture_glob = format!("{}/handbook-intro.md", fixtures_dir().display());
    // Runner reads `target.dsn_env` and looks up env var by name.
    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);
    let cfg_path = write_temp_config("CHUNKSHOP_TEST_DSN", &fixture_glob);

    let cfg = load_config(&cfg_path).expect("config loads");
    let result = run_cell(cfg).await.expect("ingest succeeds");
    assert_eq!(result.docs_processed, 1, "expected 1 doc");
    assert!(
        result.chunks_written > 0,
        "expected at least 1 chunk written"
    );

    // Sanity-check the table: row count > 0, embedding dim = 768.
    use sqlx::Row;
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");
    let row_count: i64 =
        sqlx::query("SELECT COUNT(*) FROM chunkshop_rust_parity.rust_out")
            .fetch_one(&pool)
            .await
            .expect("count")
            .get(0);
    assert!(row_count > 0, "expected rows in output table");

    let first_embedded: String =
        sqlx::query("SELECT embedded_content FROM chunkshop_rust_parity.rust_out LIMIT 1")
            .fetch_one(&pool)
            .await
            .expect("select first row")
            .get(0);
    assert!(
        !first_embedded.is_empty(),
        "first chunk embedded_content must be non-empty"
    );

    // format_type(atttypid, atttypmod) renders as "vector(768)".
    let dim_str: String = sqlx::query(
        "SELECT format_type(atttypid, atttypmod) \
         FROM pg_attribute a \
         JOIN pg_class c ON c.oid = a.attrelid \
         JOIN pg_namespace n ON n.oid = c.relnamespace \
         WHERE n.nspname = 'chunkshop_rust_parity' \
           AND c.relname = 'rust_out' \
           AND a.attname = 'embedding'",
    )
    .fetch_one(&pool)
    .await
    .expect("pg_attribute lookup")
    .get(0);
    assert_eq!(dim_str, "vector(768)", "unexpected embedding dim");
}
