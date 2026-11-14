//! RSD-08 regression: a source that fails (here: a glob matching nothing) must
//! not leave an empty table behind the error. `run_cell` has to read the
//! source before `create_table` drops the table in overwrite mode.
//!
//! Skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

use std::env;
use std::path::Path;

use chunkshop::{load_config, run_cell};

const SCHEMA: &str = "chunkshop_review_p1s_srcerr";

fn yaml(glob: &str) -> String {
    format!(
        r#"cell_name: p1s_srcerr
source:
  type: files
  glob: "{glob}"
  id_from: stem
  encoding: utf-8

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
  mode: overwrite
  hnsw: false
"#
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn failing_source_leaves_existing_table_intact() -> anyhow::Result<()> {
    let Ok(dsn) = env::var("CHUNKSHOP_TEST_DSN") else {
        eprintln!("CHUNKSHOP_TEST_DSN not set; skipping");
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
    std::fs::write(
        dir.path().join("doc.md"),
        "Hello world. This is sentence two. ".repeat(20),
    )?;
    let good = dir.path().join("cell-good.yaml");
    std::fs::write(&good, yaml(&format!("{}/*.md", dir.path().display())))?;
    run_cell(load_config(Path::new(&good))?).await?;
    let count = || async {
        sqlx::query_scalar::<_, i64>(&format!("SELECT count(*) FROM {SCHEMA}.rows"))
            .fetch_one(&pool)
            .await
    };
    let before = count().await?;
    assert!(before > 0, "first run must populate the table");

    let bad = dir.path().join("cell-bad.yaml");
    std::fs::write(&bad, yaml(&format!("{}/*.nomatch", dir.path().display())))?;
    let err = run_cell(load_config(Path::new(&bad))?).await;
    let after = count().await;
    sqlx::query(&format!("DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        .execute(&pool)
        .await?;
    assert!(err.is_err(), "a glob matching nothing must error the cell");
    assert_eq!(after?, before, "table must still hold its rows after the source error");
    Ok(())
}
