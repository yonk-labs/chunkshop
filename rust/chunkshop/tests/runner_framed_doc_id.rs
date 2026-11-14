//! RSD-06 regression: `run_cell` must hand the FRAMED document id to
//! `Sink::write_document`, so `delete_orphans` (keyed on that id) cleans up
//! stale chunks when a framed section shrinks. Before the fix the runner
//! passed the raw id (`doc`) while rows carried `doc#N`, so nothing matched.
//!
//! Skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

use std::env;
use std::path::Path;

use chunkshop::{load_config, run_cell};
use sqlx::Row;

const SCHEMA: &str = "chunkshop_review_p0rs_framed_orphans";

fn yaml(glob: &str) -> String {
    format!(
        r#"cell_name: p0rs_framed_orphans
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
  min_chars: 50

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 8
  threads: 2

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: {SCHEMA}
  table: rows
  mode: create_if_missing
  hnsw: false
  delete_orphans: true
"#
    )
}

fn section(reps: usize) -> String {
    "Hello world. This is sentence two. ".repeat(reps)
}

#[tokio::test(flavor = "multi_thread")]
async fn framed_doc_shrink_deletes_orphans() -> anyhow::Result<()> {
    let Ok(dsn) = env::var("CHUNKSHOP_TEST_DSN") else {
        eprintln!("CHUNKSHOP_TEST_DSN not set; skipping framed orphan test");
        return Ok(());
    };
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(2)
        .connect(&dsn)
        .await?;
    sqlx::query(&format!("DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        .execute(&pool)
        .await?;

    let dir = tempfile::tempdir()?;
    let md = dir.path().join("doc.md");
    let glob = format!("{}/*.md", dir.path().display());
    let cfg_path = dir.path().join("cell.yaml");
    std::fs::write(&cfg_path, yaml(&glob))?;

    // Run 1: two sections, the first long.
    std::fs::write(
        &md,
        format!("# Alpha\n\n{}\n\n# Beta\n\n{}\n", section(60), section(10)),
    )?;
    run_cell(load_config(Path::new(&cfg_path))?).await?;
    let max_seq = |pool: sqlx::PgPool| async move {
        let r = sqlx::query(&format!(
            "SELECT doc_id, max(seq_num) AS m FROM {SCHEMA}.rows GROUP BY doc_id ORDER BY doc_id"
        ))
        .fetch_all(&pool)
        .await?;
        anyhow::Ok(
            r.iter()
                .map(|x| (x.get::<String, _>("doc_id"), x.get::<i32, _>("m")))
                .collect::<Vec<_>>(),
        )
    };
    let before = max_seq(pool.clone()).await?;
    eprintln!("after run 1: {before:?}");
    let alpha = before
        .iter()
        .find(|(d, _)| d.starts_with("doc#") && *d != "doc")
        .cloned()
        .expect("framed doc ids doc#N present");
    assert!(alpha.1 >= 3, "need several chunks in run 1, got {before:?}");

    // Run 2: shrink the first section.
    std::fs::write(
        &md,
        format!("# Alpha\n\n{}\n\n# Beta\n\n{}\n", section(10), section(10)),
    )?;
    run_cell(load_config(Path::new(&cfg_path))?).await?;
    let after = max_seq(pool.clone()).await?;
    eprintln!("after run 2: {after:?}");
    let alpha_after = after.iter().find(|(d, _)| *d == alpha.0).expect("alpha id");
    assert!(
        alpha_after.1 < alpha.1,
        "stale orphan chunks remain for framed doc {}: max seq_num was {} and is still {}",
        alpha.0,
        alpha.1,
        alpha_after.1
    );

    sqlx::query(&format!("DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        .execute(&pool)
        .await?;
    Ok(())
}
