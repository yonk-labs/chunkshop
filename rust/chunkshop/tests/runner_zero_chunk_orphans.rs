//! P0 slice 2: with `delete_orphans: true`, a framed document that now yields
//! zero chunks must have its stale rows deleted. Mirrors Python
//! `test_zero_chunk_orphans.py`.
//!
//! Skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

use std::env;
use std::path::Path;

use chunkshop::{load_config, run_cell};

/// Distinct schema per scenario: the two tests run in parallel.
fn schema(orphans: bool) -> String {
    format!("chunkshop_review_p1v_zc_{orphans}")
}

fn yaml(glob: &str, orphans: bool) -> String {
    let schema = schema(orphans);
    format!(
        r#"cell_name: p1v_zc
source:
  type: files
  glob: "{glob}"
  id_from: stem
  encoding: utf-8

framer:
  type: heading_boundary

chunker:
  type: sentence_aware
  max_chars: 200
  min_chars: 5

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 8
  threads: 2

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: {schema}
  table: rows
  mode: create_if_missing
  hnsw: false
  delete_orphans: {orphans}
"#
    )
}

async fn doc_ids(pool: &sqlx::PgPool, schema: &str) -> anyhow::Result<Vec<String>> {
    Ok(sqlx::query_scalar::<_, String>(&format!(
        "SELECT DISTINCT doc_id FROM {schema}.rows ORDER BY doc_id"
    ))
    .fetch_all(pool)
    .await?)
}

/// Run 1 ingests a.md (headingless -> doc id `a`) and b.md (`b#0`); run 2 empties a.md.
async fn scenario(orphans: bool) -> anyhow::Result<Option<(Vec<String>, Vec<String>)>> {
    let Ok(dsn) = env::var("CHUNKSHOP_TEST_DSN") else {
        eprintln!("CHUNKSHOP_TEST_DSN not set; skipping");
        return Ok(None);
    };
    let schema = schema(orphans);
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    sqlx::query(&format!("DROP SCHEMA IF EXISTS {schema} CASCADE"))
        .execute(&pool)
        .await?;

    let dir = tempfile::tempdir()?;
    let a = dir.path().join("a.md");
    std::fs::write(&a, "Plain words with no headings at all here, long enough.\n")?;
    std::fs::write(dir.path().join("b.md"), "# Only\n\nDelta delta delta delta delta.\n")?;
    let cfg = dir.path().join("cell.yaml");
    std::fs::write(&cfg, yaml(&format!("{}/*.md", dir.path().display()), orphans))?;

    let r = run_cell(load_config(Path::new(&cfg))?).await;
    let first = doc_ids(&pool, &schema).await;
    std::fs::write(&a, "   \n")?;
    let r2 = run_cell(load_config(Path::new(&cfg))?).await;
    let second = doc_ids(&pool, &schema).await;
    sqlx::query(&format!("DROP SCHEMA IF EXISTS {schema} CASCADE"))
        .execute(&pool)
        .await?;
    r?;
    r2?;
    Ok(Some((first?, second?)))
}

#[tokio::test(flavor = "multi_thread")]
async fn zero_chunk_doc_rows_removed_with_delete_orphans() -> anyhow::Result<()> {
    let Some((first, second)) = scenario(true).await? else {
        return Ok(());
    };
    assert_eq!(first, vec!["a", "b#0"]);
    assert_eq!(second, vec!["b#0"], "stale rows of the emptied doc must be gone");
    Ok(())
}

#[tokio::test(flavor = "multi_thread")]
async fn zero_chunk_doc_rows_kept_without_delete_orphans() -> anyhow::Result<()> {
    let Some((first, second)) = scenario(false).await? else {
        return Ok(());
    };
    assert_eq!(first, vec!["a", "b#0"]);
    assert_eq!(second, vec!["a", "b#0"], "current semantics: rows are kept");
    Ok(())
}
