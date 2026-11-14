//! P0 slice 1: `id_from: stem` must fail (not silently overwrite) when two
//! matched files share a stem. Mirrors Python `_check_unique_stems`.
//!
//! The run_cell test skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

use std::env;
use std::path::Path;

use chunkshop::config::FilesSourceConfig;
use chunkshop::sources::FilesSource;
use chunkshop::{load_config, run_cell};

const SCHEMA: &str = "chunkshop_review_p1v_dupstem";

fn corpus(files: &[&str]) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    for f in files {
        let p = dir.path().join(f);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, format!("content of {f}")).unwrap();
    }
    dir
}

fn src(dir: &Path, id_from: &str) -> FilesSource {
    FilesSource::new(FilesSourceConfig {
        glob: format!("{}/**/*.md", dir.display()),
        id_from: id_from.into(),
        encoding: "utf-8".into(),
    })
}

#[test]
fn colliding_stems_error_naming_both_paths() {
    let dir = corpus(&["a/readme.md", "b/readme.md"]);
    let err = src(dir.path(), "stem").iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("id_from='stem' maps multiple files to the same doc id"), "{msg}");
    assert!(msg.contains("a/readme.md") && msg.contains("b/readme.md"), "{msg}");
    assert!(msg.contains("'readme'"), "{msg}");
    assert!(msg.ends_with("Set source.id_from: path to give each file a unique id."), "{msg}");
}

#[test]
fn many_collisions_truncated_to_five_groups() {
    let names: Vec<String> = (0..7)
        .flat_map(|i| [format!("a/s{i}.md"), format!("b/s{i}.md")])
        .collect();
    let refs: Vec<&str> = names.iter().map(String::as_str).collect();
    let dir = corpus(&refs);
    let err = src(dir.path(), "stem").iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("... and 2 more"), "{msg}");
}

#[test]
fn path_mode_yields_distinct_ids_for_same_stem() {
    let dir = corpus(&["a/readme.md", "b/readme.md"]);
    let docs = src(dir.path(), "path").iter_documents().unwrap();
    assert_eq!(docs.len(), 2);
    assert_ne!(docs[0].id, docs[1].id);
}

#[test]
fn unique_stems_unaffected() {
    let dir = corpus(&["a/one.md", "b/two.md"]);
    let docs = src(dir.path(), "stem").iter_documents().unwrap();
    let ids: Vec<&str> = docs.iter().map(|d| d.id.as_str()).collect();
    assert_eq!(ids, vec!["one", "two"]);
}

fn yaml(glob: &str) -> String {
    format!(
        r#"cell_name: p1v_dupstem
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
async fn colliding_stems_error_cell_and_keep_existing_table() -> anyhow::Result<()> {
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

    let good = tempfile::tempdir()?;
    std::fs::write(
        good.path().join("doc.md"),
        "Hello world. This is sentence two. ".repeat(20),
    )?;
    let good_yaml = good.path().join("cell-good.yaml");
    std::fs::write(&good_yaml, yaml(&format!("{}/*.md", good.path().display())))?;
    run_cell(load_config(Path::new(&good_yaml))?).await?;
    let count = || async {
        sqlx::query_scalar::<_, i64>(&format!("SELECT count(*) FROM {SCHEMA}.rows"))
            .fetch_one(&pool)
            .await
    };
    let before = count().await?;
    assert!(before > 0, "first run must populate the table");

    let bad = corpus(&["a/readme.md", "b/readme.md"]);
    let bad_yaml = bad.path().join("cell-bad.yaml");
    std::fs::write(&bad_yaml, yaml(&format!("{}/**/*.md", bad.path().display())))?;
    let err = run_cell(load_config(Path::new(&bad_yaml))?).await;
    let after = count().await;
    sqlx::query(&format!("DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        .execute(&pool)
        .await?;
    let msg = format!("{:#}", err.expect_err("colliding stems must error the cell"));
    assert!(msg.contains("id_from='stem'"), "{msg}");
    assert_eq!(after?, before, "table must still hold its rows after the source error");
    Ok(())
}
