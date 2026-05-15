//! Cross-language sink-mode parity:
//!   1. Python writes to `chunkshop_rust_sink_parity.rows` with mode=overwrite
//!      and source_tag=py_a, using a hierarchy chunker so `metadata.heading`
//!      is populated (matches the promote_metadata column).
//!   2. Rust appends to the same table with mode=append, source_tag=rs_b,
//!      and a `promote_metadata: [{path: heading, type: text}]` column.
//!   3. Asserts: both rows live, source filter works, promoted column is
//!      populated for Rust rows.
//!
//! Skips cleanly if `CHUNKSHOP_TEST_DSN` is unset.

use std::env;
use std::path::PathBuf;
use std::process::Command;

use chunkshop::{load_config, run_cell};

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn write_yaml(path: &std::path::Path, body: &str) {
    std::fs::write(path, body).unwrap();
}

#[tokio::test]
async fn cross_language_append_with_promote_column() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping append-parity test");
            return;
        }
    };
    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);

    use sqlx::Row;
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");
    // Cleanup any leftover schema from a previous run.
    let _ = sqlx::query("DROP SCHEMA IF EXISTS chunkshop_rust_sink_parity CASCADE")
        .execute(&pool)
        .await;

    let py_glob = format!("{}/handbook-intro.md", fixtures_dir().display());

    // 1. Python cell A: overwrite + source_tag=py_a + hierarchy chunker.
    //    Python's hierarchy chunker stamps metadata.heading on every chunk.
    let py_yaml = format!(
        r#"cell_name: cross_lang_py
source:
  type: files
  glob: "{glob}"
  id_from: stem
  encoding: utf-8

chunker:
  type: hierarchy
  prefix_heading: true
  max_chars: 800

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  batch_size: 1
  threads: 1

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: chunkshop_rust_sink_parity
  table: rows
  mode: overwrite
  source_tag: py_a
  hnsw: false
  promote_metadata:
    - {{ path: heading, type: text }}
"#,
        glob = py_glob
    );
    let py_path = std::env::temp_dir().join("chunkshop-rs-cross-py.yaml");
    write_yaml(&py_path, &py_yaml);

    // Use whichever venv the developer already has bootstrapped — fall back
    // to the main-worktree venv (assumed at ../chunkshop/python/.venv when
    // this test runs from a feature worktree). The Python code path the test
    // exercises (hierarchy chunker + overwrite ingest) doesn't depend on any
    // changes from this brief, so any chunkshop-py install ≥ 0.2.0 works.
    let py_bin = if let Ok(p) = env::var("CHUNKSHOP_PY") {
        PathBuf::from(p)
    } else {
        let mut candidates: Vec<PathBuf> = Vec::new();
        let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let worktree_root = here.ancestors().nth(2).unwrap().to_path_buf();
        candidates.push(worktree_root.join("python").join(".venv").join("bin").join("python"));
        if let Some(parent_of_worktree) = worktree_root.parent() {
            candidates.push(
                parent_of_worktree
                    .join("chunkshop")
                    .join("python")
                    .join(".venv")
                    .join("bin")
                    .join("python"),
            );
        }
        let mut chosen: Option<PathBuf> = None;
        for c in candidates {
            if !c.exists() {
                continue;
            }
            // Confirm chunkshop is importable from this venv — a bare uv-created
            // shell would pass exists() but fail on import.
            let ok = Command::new(&c)
                .args(["-c", "import chunkshop"])
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            if ok {
                chosen = Some(c);
                break;
            }
        }
        match chosen {
            Some(p) => p,
            None => {
                eprintln!("no chunkshop python venv has the package importable; skipping");
                return;
            }
        }
    };
    let py_status = Command::new(&py_bin)
        .args(["-m", "chunkshop.cli", "ingest", "--config", py_path.to_str().unwrap()])
        .status()
        .expect("spawn python -m chunkshop.cli");
    assert!(py_status.success(), "Python cell A ingest failed (using {})", py_bin.display());

    // 2. Rust cell B: append + source_tag=rs_b + same promote_metadata column.
    //    Use a different fixture file so doc_ids don't collide.
    let rs_glob = format!("{}/hierarchy_corpus.txt", fixtures_dir().display());
    let rs_yaml = format!(
        r#"cell_name: cross_lang_rs
source:
  type: files
  glob: "{glob}"
  id_from: sha1
  encoding: utf-8

chunker:
  type: hierarchy
  prefix_heading: true
  max_chars: 800

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  batch_size: 1
  threads: 1

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: chunkshop_rust_sink_parity
  table: rows
  mode: append
  source_tag: rs_b
  hnsw: false
  promote_metadata:
    - {{ path: heading, type: text }}
"#,
        glob = rs_glob
    );
    let rs_path = std::env::temp_dir().join("chunkshop-rs-cross-rs.yaml");
    write_yaml(&rs_path, &rs_yaml);
    let cfg = load_config(&rs_path).expect("load rust cfg");
    let result = run_cell(cfg).await.expect("rust ingest");
    assert!(
        result.chunks_written > 0,
        "Rust ingest must write at least one chunk"
    );

    // 3. Post-conditions.
    let total: i64 = sqlx::query("SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows")
        .fetch_one(&pool)
        .await
        .unwrap()
        .get(0);
    assert!(total >= 2, "expected at least 2 rows total, got {total}");

    let distinct_sources: i64 = sqlx::query(
        "SELECT COUNT(DISTINCT source) FROM chunkshop_rust_sink_parity.rows WHERE source IS NOT NULL",
    )
    .fetch_one(&pool)
    .await
    .unwrap()
    .get(0);
    assert_eq!(
        distinct_sources, 2,
        "expected exactly 2 distinct source_tags (py_a and rs_b), got {distinct_sources}"
    );

    let rs_rows: i64 = sqlx::query(
        "SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows WHERE source = 'rs_b'",
    )
    .fetch_one(&pool)
    .await
    .unwrap()
    .get(0);
    assert_eq!(
        rs_rows as usize, result.chunks_written,
        "rs_b row count {rs_rows} must equal Rust write count {}",
        result.chunks_written
    );

    let rs_with_heading: i64 = sqlx::query(
        "SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows \
         WHERE source = 'rs_b' AND heading IS NOT NULL",
    )
    .fetch_one(&pool)
    .await
    .unwrap()
    .get(0);
    assert!(
        rs_with_heading > 0,
        "expected promote_metadata.heading to be populated for rs_b rows, got {rs_with_heading}"
    );

    let py_with_heading: i64 = sqlx::query(
        "SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows \
         WHERE source = 'py_a' AND heading IS NOT NULL",
    )
    .fetch_one(&pool)
    .await
    .unwrap()
    .get(0);
    assert!(
        py_with_heading > 0,
        "expected promote_metadata.heading to be populated for py_a rows, got {py_with_heading}"
    );

    let _ = sqlx::query("DROP SCHEMA IF EXISTS chunkshop_rust_sink_parity CASCADE")
        .execute(&pool)
        .await;
}
