//! RT — Cross-backend matrix.
//!
//! Mirrors `python/tests/chunkshop/test_cross_backend_matrix.py`. For each of
//! the 4 DB-source kinds (pg_table, mariadb_table, sqlite_table,
//! clickhouse_table) and the 4 DB-sink kinds (postgres, mariadb, sqlite,
//! clickhouse), this file owns one `#[tokio::test]` cell that:
//!
//!   1. Seeds a 1-doc fixture into the source backend.
//!   2. Drives `chunkshop::runner::run_cell` end-to-end through the chunkshop
//!      pipeline (real fastembed Xenova int8 BGE-small, sentence_aware
//!      chunker).
//!   3. Asserts `result.docs_processed == 1`, `result.chunks_written > 0`,
//!      and a sink-side row count equal to `result.chunks_written`.
//!   4. Drops the per-cell databases on exit.
//!
//! Per-DSN skip discipline matches Python: each cell checks both its source
//! and sink DSN env vars at the top; if either is unset, `eprintln!` a
//! "skipping" message and `return`. **Never** `#[ignore]` — that would hide
//! the cell from default `cargo test` runs.
//!
//! 16 cells = 4 sources × 4 sinks. They are written longhand (one
//! `#[tokio::test]` per cell) so a failure points at a specific cell name.
//! Boilerplate (seed/count/drop, build embedder) is factored into private
//! helpers at the top of the file.

use std::env;

use anyhow::Result;
use chunkshop::config::{
    CellConfig, ChunkerConfig, ClickhouseTableSourceConfig, ClickhouseTargetConfig, EmbedderConfig,
    ExtractorConfig, FastembedEmbedderConfig, FramerConfig, IdentityFramerConfig,
    MariadbTableSourceConfig, MariadbTargetConfig, NoneExtractorConfig, PgTableSourceConfig,
    PostgresTargetConfig, RuntimeConfig, SentenceAwareChunkerConfig, SourceConfig,
    SqliteTableSourceConfig, SqliteTargetConfig, TargetConfig,
};
use chunkshop::runner::run_cell;
use tempfile::TempDir;

const PG_DSN: &str = "CHUNKSHOP_TEST_DSN";
const MARIADB_DSN: &str = "CHUNKSHOP_TEST_DSN_MARIADB";
const CH_DSN: &str = "CHUNKSHOP_TEST_DSN_CH";

const SEED_BODY_FRAGMENT: &str = "Hello world. This is sentence two. ";
const SEED_REPETITIONS: usize = 10;

fn seed_body() -> String {
    SEED_BODY_FRAGMENT.repeat(SEED_REPETITIONS)
}

/// Emit a structured skip message and return None when `var` is unset. Caller
/// uses `let Some(_) = skip_if_unset(...) else { return; };` to short-circuit
/// the cell. Mirrors Python's `pytest.mark.skipif` per-test pattern.
fn skip_if_unset(var: &str, cell: &str) -> Option<()> {
    if env::var(var).is_err() {
        eprintln!("skipping {cell}: {var} unset");
        return None;
    }
    Some(())
}

// -----------------------------------------------------------------------------
// PG seed / count / drop
// -----------------------------------------------------------------------------

async fn pg_pool() -> Result<sqlx::PgPool> {
    let dsn = env::var(PG_DSN)?;
    Ok(sqlx::postgres::PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?)
}

async fn seed_pg(schema: &str) -> Result<()> {
    let pool = pg_pool().await?;
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await?;
    sqlx::query(&format!(r#"CREATE SCHEMA "{schema}""#))
        .execute(&pool)
        .await?;
    sqlx::query(&format!(
        r#"CREATE TABLE "{schema}"."docs" (id text PRIMARY KEY, body text NOT NULL)"#
    ))
    .execute(&pool)
    .await?;
    sqlx::query(&format!(
        r#"INSERT INTO "{schema}"."docs" (id, body) VALUES ($1, $2)"#
    ))
    .bind("doc1")
    .bind(seed_body())
    .execute(&pool)
    .await?;
    Ok(())
}

async fn count_pg(schema: &str, table: &str) -> Result<i64> {
    let pool = pg_pool().await?;
    let (n,): (i64,) = sqlx::query_as(&format!(r#"SELECT COUNT(*) FROM "{schema}"."{table}""#))
        .fetch_one(&pool)
        .await?;
    Ok(n)
}

async fn drop_pg(schema: &str) {
    if let Ok(pool) = pg_pool().await {
        let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
            .execute(&pool)
            .await;
    }
}

// -----------------------------------------------------------------------------
// MariaDB seed / count / drop
// -----------------------------------------------------------------------------

async fn mariadb_pool() -> Result<sqlx::MySqlPool> {
    let dsn = env::var(MARIADB_DSN)?;
    Ok(sqlx::mysql::MySqlPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?)
}

async fn seed_mariadb(database: &str) -> Result<()> {
    let pool = mariadb_pool().await?;
    sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`"))
        .execute(&pool)
        .await?;
    sqlx::query(&format!("CREATE DATABASE `{database}`"))
        .execute(&pool)
        .await?;
    sqlx::query(&format!(
        "CREATE TABLE `{database}`.`docs` (id VARCHAR(64) PRIMARY KEY, body LONGTEXT NOT NULL)"
    ))
    .execute(&pool)
    .await?;
    sqlx::query(&format!(
        "INSERT INTO `{database}`.`docs` (id, body) VALUES (?, ?)"
    ))
    .bind("doc1")
    .bind(seed_body())
    .execute(&pool)
    .await?;
    Ok(())
}

async fn count_mariadb(database: &str, table: &str) -> Result<i64> {
    let pool = mariadb_pool().await?;
    let (n,): (i64,) = sqlx::query_as(&format!("SELECT COUNT(*) FROM `{database}`.`{table}`"))
        .fetch_one(&pool)
        .await?;
    Ok(n)
}

async fn drop_mariadb(database: &str) {
    if let Ok(pool) = mariadb_pool().await {
        let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`"))
            .execute(&pool)
            .await;
    }
}

// -----------------------------------------------------------------------------
// SQLite seed / count
// -----------------------------------------------------------------------------

fn seed_sqlite(path: &std::path::Path) -> Result<()> {
    let conn = rusqlite::Connection::open(path)?;
    conn.execute_batch(
        r#"CREATE TABLE IF NOT EXISTS "docs" (id TEXT PRIMARY KEY, body TEXT NOT NULL)"#,
    )?;
    conn.execute(
        r#"INSERT INTO "docs" (id, body) VALUES (?, ?)"#,
        rusqlite::params!["doc1", seed_body()],
    )?;
    Ok(())
}

fn count_sqlite(path: &std::path::Path, table: &str) -> Result<i64> {
    let conn = rusqlite::Connection::open(path)?;
    let n: i64 = conn.query_row(&format!(r#"SELECT COUNT(*) FROM "{table}""#), [], |r| {
        r.get(0)
    })?;
    Ok(n)
}

// -----------------------------------------------------------------------------
// ClickHouse seed / count / drop
// -----------------------------------------------------------------------------
//
// CH is not exposed through sqlx. We use the `clickhouse` crate's HTTP client
// directly via `ClickhouseBackend::client()` to keep the test self-contained
// without depending on internal helpers.

async fn ch_client() -> Result<clickhouse::Client> {
    let backend = chunkshop::backends::ClickhouseBackend::new(CH_DSN.to_string());
    backend.client().await
}

async fn seed_ch(database: &str) -> Result<()> {
    let client = ch_client().await?;
    client
        .query(&format!("DROP DATABASE IF EXISTS `{database}` SYNC"))
        .execute()
        .await?;
    client
        .query(&format!("CREATE DATABASE `{database}`"))
        .execute()
        .await?;
    client
        .query(&format!(
            "CREATE TABLE `{database}`.`docs` (id String, body String) \
             ENGINE = MergeTree() ORDER BY id"
        ))
        .execute()
        .await?;
    let body = seed_body();
    client
        .query(&format!(
            "INSERT INTO `{database}`.`docs` (id, body) VALUES (?, ?)"
        ))
        .bind("doc1")
        .bind(body.as_str())
        .execute()
        .await?;
    Ok(())
}

async fn count_ch(database: &str, table: &str) -> Result<i64> {
    let client = ch_client().await?;
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct CountRow {
        c: u64,
    }
    let mut cur = client
        .query(&format!("SELECT count() AS c FROM `{database}`.`{table}`"))
        .fetch::<CountRow>()?;
    let row = cur
        .next()
        .await?
        .ok_or_else(|| anyhow::anyhow!("count() returned no rows"))?;
    Ok(row.c as i64)
}

async fn drop_ch(database: &str) {
    if let Ok(client) = ch_client().await {
        let _ = client
            .query(&format!("DROP DATABASE IF EXISTS `{database}` SYNC"))
            .execute()
            .await;
    }
}

// -----------------------------------------------------------------------------
// Pipeline-config builders shared across all 16 cells
// -----------------------------------------------------------------------------

fn embedder_cfg() -> EmbedderConfig {
    EmbedderConfig::Fastembed(FastembedEmbedderConfig {
        model_name: "Xenova/bge-small-en-v1.5-int8".to_string(),
        dim: 384,
        batch_size: 8,
        threads: Some(2),
        hf_repo: None,
        onnx_path: None,
        pooling: "cls".to_string(),
        additional_files: vec![
            "tokenizer.json".to_string(),
            "tokenizer_config.json".to_string(),
            "special_tokens_map.json".to_string(),
            "config.json".to_string(),
        ],
    })
}

fn chunker_cfg() -> ChunkerConfig {
    ChunkerConfig::SentenceAware(SentenceAwareChunkerConfig {
        doc_type: "default".to_string(),
        max_chars: 200,
        min_chars: 50,
        if_oversize: None,
    })
}

fn cell_config(cell_name: &str, source: SourceConfig, target: TargetConfig) -> CellConfig {
    CellConfig {
        cell_name: cell_name.to_string(),
        source,
        chunker: chunker_cfg(),
        embedder: embedder_cfg(),
        target,
        runtime: RuntimeConfig {
            omp_num_threads: Some(2),
            doc_limit: None,
            log_path: None,
            heartbeat_every: None,
            log_format: "text".to_string(),
        },
        framer: FramerConfig::Identity(IdentityFramerConfig {}),
        extractor: ExtractorConfig::None(NoneExtractorConfig::default()),
    }
}

// -----------------------------------------------------------------------------
// Source-config builders. PG is `schema_name` per the Rust config (Python uses
// `database` aliased to `database_name`); the other three use `database_name`.
// -----------------------------------------------------------------------------

fn pg_source(name: &str) -> SourceConfig {
    SourceConfig::PgTable(PgTableSourceConfig {
        dsn_env: PG_DSN.to_string(),
        schema_name: name.to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
        updated_at_column: None,
    })
}

fn mariadb_source(name: &str) -> SourceConfig {
    SourceConfig::MariadbTable(MariadbTableSourceConfig {
        dsn_env: MARIADB_DSN.to_string(),
        database_name: name.to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
    })
}

fn sqlite_source(env_var: &str) -> SourceConfig {
    SourceConfig::SqliteTable(SqliteTableSourceConfig {
        dsn_env: env_var.to_string(),
        database_name: "ignored".to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
    })
}

fn clickhouse_source(name: &str) -> SourceConfig {
    SourceConfig::ClickhouseTable(ClickhouseTableSourceConfig {
        dsn_env: CH_DSN.to_string(),
        database_name: name.to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
    })
}

// -----------------------------------------------------------------------------
// Target-config builders. `mode = overwrite`, `source_tag = "xbm"`,
// `hnsw = false`. Mirrors Python's _build_target.
// -----------------------------------------------------------------------------

fn pg_target(schema: &str) -> TargetConfig {
    TargetConfig::Postgres(PostgresTargetConfig {
        dsn_env: PG_DSN.to_string(),
        database_name: schema.to_string(),
        table: "chunks".to_string(),
        overwrite: false,
        hnsw: false,
        vector_metric: "cosine".to_string(),
        mode: "overwrite".to_string(),
        source_tag: Some("xbm".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        memory: None,
        documents: None,
    })
}

fn mariadb_target(database: &str) -> TargetConfig {
    TargetConfig::Mariadb(MariadbTargetConfig {
        dsn_env: MARIADB_DSN.to_string(),
        database_name: database.to_string(),
        table: "chunks".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("xbm".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    })
}

fn sqlite_target(env_var: &str) -> TargetConfig {
    TargetConfig::Sqlite(SqliteTargetConfig {
        dsn_env: env_var.to_string(),
        database_name: "ignored".to_string(),
        table: "chunks".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("xbm".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    })
}

fn clickhouse_target(database: &str) -> TargetConfig {
    TargetConfig::Clickhouse(ClickhouseTargetConfig {
        dsn_env: CH_DSN.to_string(),
        database_name: database.to_string(),
        table: "chunks".to_string(),
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("xbm".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        engine: None,
        documents: None,
    })
}

// -----------------------------------------------------------------------------
// Per-cell helpers for SQLite source/sink env wiring
// -----------------------------------------------------------------------------

/// Allocate a temp dir + per-cell env var pointing at `<dir>/src.db`. The
/// `TempDir` is returned so the cell can hold it; dropping it removes the
/// file. Each cell uses a process-id + cell-name suffix on the env var to
/// avoid collisions when multiple cells run in parallel.
fn sqlite_src_env(cell: &str) -> (TempDir, String, std::path::PathBuf) {
    let dir = tempfile::tempdir().expect("tempdir for sqlite src");
    let path = dir.path().join("src.db");
    let var = format!("XBM_SRC_SQLITE_{}_{}", cell, std::process::id());
    env::set_var(&var, path.to_str().unwrap());
    (dir, var, path)
}

fn sqlite_sink_env(cell: &str) -> (TempDir, String, std::path::PathBuf) {
    let dir = tempfile::tempdir().expect("tempdir for sqlite sink");
    let path = dir.path().join("sink.db");
    let var = format!("XBM_SINK_SQLITE_{}_{}", cell, std::process::id());
    env::set_var(&var, path.to_str().unwrap());
    (dir, var, path)
}

// =============================================================================
// 16 cells. Each is its own #[tokio::test] so a failure points at the exact
// (source, sink) pair. Naming convention: cell_<source>_to_<sink>.
// =============================================================================

// --- pg_table → * --------------------------------------------------------

#[tokio::test]
async fn cell_pg_table_to_postgres() {
    let cell = "pg_table_to_postgres";
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_pg(&src_db).await.expect("seed pg src");
    let cfg = cell_config(cell, pg_source(&src_db), pg_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_pg(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_pg(&src_db).await;
    drop_pg(&sink_db).await;
}

#[tokio::test]
async fn cell_pg_table_to_mariadb() {
    let cell = "pg_table_to_mariadb";
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_pg(&src_db).await.expect("seed pg src");
    let cfg = cell_config(cell, pg_source(&src_db), mariadb_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_mariadb(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_pg(&src_db).await;
    drop_mariadb(&sink_db).await;
}

#[tokio::test]
async fn cell_pg_table_to_sqlite() {
    let cell = "pg_table_to_sqlite";
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let (_sink_dir, sink_env, sink_path) = sqlite_sink_env(cell);
    seed_pg(&src_db).await.expect("seed pg src");
    let cfg = cell_config(cell, pg_source(&src_db), sqlite_target(&sink_env));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_sqlite(&sink_path, "chunks").expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_pg(&src_db).await;
}

#[tokio::test]
async fn cell_pg_table_to_clickhouse() {
    let cell = "pg_table_to_clickhouse";
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_pg(&src_db).await.expect("seed pg src");
    let cfg = cell_config(cell, pg_source(&src_db), clickhouse_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_ch(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_pg(&src_db).await;
    drop_ch(&sink_db).await;
}

// --- mariadb_table → * ---------------------------------------------------

#[tokio::test]
async fn cell_mariadb_table_to_postgres() {
    let cell = "mariadb_table_to_postgres";
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_mariadb(&src_db).await.expect("seed mariadb src");
    let cfg = cell_config(cell, mariadb_source(&src_db), pg_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_pg(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_mariadb(&src_db).await;
    drop_pg(&sink_db).await;
}

#[tokio::test]
async fn cell_mariadb_table_to_mariadb() {
    let cell = "mariadb_table_to_mariadb";
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_mariadb(&src_db).await.expect("seed mariadb src");
    let cfg = cell_config(cell, mariadb_source(&src_db), mariadb_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_mariadb(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_mariadb(&src_db).await;
    drop_mariadb(&sink_db).await;
}

#[tokio::test]
async fn cell_mariadb_table_to_sqlite() {
    let cell = "mariadb_table_to_sqlite";
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let (_sink_dir, sink_env, sink_path) = sqlite_sink_env(cell);
    seed_mariadb(&src_db).await.expect("seed mariadb src");
    let cfg = cell_config(cell, mariadb_source(&src_db), sqlite_target(&sink_env));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_sqlite(&sink_path, "chunks").expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_mariadb(&src_db).await;
}

#[tokio::test]
async fn cell_mariadb_table_to_clickhouse() {
    let cell = "mariadb_table_to_clickhouse";
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_mariadb(&src_db).await.expect("seed mariadb src");
    let cfg = cell_config(cell, mariadb_source(&src_db), clickhouse_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_ch(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_mariadb(&src_db).await;
    drop_ch(&sink_db).await;
}

// --- sqlite_table → * ----------------------------------------------------

#[tokio::test]
async fn cell_sqlite_table_to_postgres() {
    let cell = "sqlite_table_to_postgres";
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    let (_src_dir, src_env, src_path) = sqlite_src_env(cell);
    let sink_db = format!("xbm_sink_{cell}");
    seed_sqlite(&src_path).expect("seed sqlite src");
    let cfg = cell_config(cell, sqlite_source(&src_env), pg_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_pg(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_pg(&sink_db).await;
}

#[tokio::test]
async fn cell_sqlite_table_to_mariadb() {
    let cell = "sqlite_table_to_mariadb";
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    let (_src_dir, src_env, src_path) = sqlite_src_env(cell);
    let sink_db = format!("xbm_sink_{cell}");
    seed_sqlite(&src_path).expect("seed sqlite src");
    let cfg = cell_config(cell, sqlite_source(&src_env), mariadb_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_mariadb(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_mariadb(&sink_db).await;
}

#[tokio::test]
async fn cell_sqlite_table_to_sqlite() {
    let cell = "sqlite_table_to_sqlite";
    let (_src_dir, src_env, src_path) = sqlite_src_env(cell);
    let (_sink_dir, sink_env, sink_path) = sqlite_sink_env(cell);
    seed_sqlite(&src_path).expect("seed sqlite src");
    let cfg = cell_config(cell, sqlite_source(&src_env), sqlite_target(&sink_env));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_sqlite(&sink_path, "chunks").expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
}

#[tokio::test]
async fn cell_sqlite_table_to_clickhouse() {
    let cell = "sqlite_table_to_clickhouse";
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    let (_src_dir, src_env, src_path) = sqlite_src_env(cell);
    let sink_db = format!("xbm_sink_{cell}");
    seed_sqlite(&src_path).expect("seed sqlite src");
    let cfg = cell_config(cell, sqlite_source(&src_env), clickhouse_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_ch(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_ch(&sink_db).await;
}

// --- clickhouse_table → * ------------------------------------------------

#[tokio::test]
async fn cell_clickhouse_table_to_postgres() {
    let cell = "clickhouse_table_to_postgres";
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(PG_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_ch(&src_db).await.expect("seed ch src");
    let cfg = cell_config(cell, clickhouse_source(&src_db), pg_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_pg(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_ch(&src_db).await;
    drop_pg(&sink_db).await;
}

#[tokio::test]
async fn cell_clickhouse_table_to_mariadb() {
    let cell = "clickhouse_table_to_mariadb";
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    if skip_if_unset(MARIADB_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_ch(&src_db).await.expect("seed ch src");
    let cfg = cell_config(cell, clickhouse_source(&src_db), mariadb_target(&sink_db));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_mariadb(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_ch(&src_db).await;
    drop_mariadb(&sink_db).await;
}

#[tokio::test]
async fn cell_clickhouse_table_to_sqlite() {
    let cell = "clickhouse_table_to_sqlite";
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let (_sink_dir, sink_env, sink_path) = sqlite_sink_env(cell);
    seed_ch(&src_db).await.expect("seed ch src");
    let cfg = cell_config(cell, clickhouse_source(&src_db), sqlite_target(&sink_env));
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_sqlite(&sink_path, "chunks").expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_ch(&src_db).await;
}

#[tokio::test]
async fn cell_clickhouse_table_to_clickhouse() {
    let cell = "clickhouse_table_to_clickhouse";
    if skip_if_unset(CH_DSN, cell).is_none() {
        return;
    }
    let src_db = format!("xbm_src_{cell}");
    let sink_db = format!("xbm_sink_{cell}");
    seed_ch(&src_db).await.expect("seed ch src");
    let cfg = cell_config(
        cell,
        clickhouse_source(&src_db),
        clickhouse_target(&sink_db),
    );
    let res = run_cell(cfg).await.expect("run_cell");
    assert_eq!(res.docs_processed, 1, "{cell}: docs_processed");
    assert!(res.chunks_written > 0, "{cell}: chunks_written>0");
    let n = count_ch(&sink_db, "chunks").await.expect("count sink");
    assert_eq!(n as usize, res.chunks_written, "{cell}: sink count");
    drop_ch(&src_db).await;
    drop_ch(&sink_db).await;
}
