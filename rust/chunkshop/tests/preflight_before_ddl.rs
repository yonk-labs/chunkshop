//! A cell that cannot possibly succeed must not destroy its target table.
//!
//! `run_cell` calls `sink.create_table()` — a DROP+CREATE under
//! `mode: overwrite` — and used to do so BEFORE the errors a `derive:` block
//! can raise: an ambiguous extension routing (stashed by `FilesSource::new`
//! and only returned from `iter_documents()`) and a rung whose Cargo feature
//! wasn't built (a `MissingFeatureDeriver` that fails at point of use). Both
//! are knowable from config alone, and Python raises both in
//! `FilesSource.__init__`, before `runner.py` reaches `create_table()`.
//!
//! Concretely: a default-features build (`full`, which deliberately excludes
//! `media`) running the shipped `docs/samples/images-keyless.yaml` dropped
//! and recreated the target table empty, then failed.

use chunkshop::{load_config, run_cell};
use tempfile::tempdir;

/// Seed a table with one row so a DROP is observable.
///
/// The `source` column matters: `create_table()`'s overwrite path first runs
/// `SELECT DISTINCT source ... LIMIT 10` to refuse dropping another cell's
/// rows. Without that column the sink errors on the probe query and the DROP
/// never happens for an unrelated reason, which would make this test pass
/// vacuously. With it (and a NULL value, i.e. no foreign `source_tag`) the
/// DROP is genuinely reachable, so surviving rows prove the preflight ran
/// first.
fn seed(db: &std::path::Path, table: &str) {
    let conn = rusqlite::Connection::open(db).unwrap();
    conn.execute_batch(&format!(
        "CREATE TABLE {table} (id TEXT PRIMARY KEY, source TEXT); \
         INSERT INTO {table} VALUES ('pre-existing', NULL);"
    ))
    .unwrap();
}

fn row_count(db: &std::path::Path, table: &str) -> Option<i64> {
    let conn = rusqlite::Connection::open(db).unwrap();
    conn.query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
        .ok()
}

fn write_cell(dir: &std::path::Path, dsn_env: &str, derive_block: &str) -> std::path::PathBuf {
    // A file the glob actually matches, so a regression cannot short-circuit
    // on "no files matched glob" before reaching the error under test.
    std::fs::write(dir.join("a.png"), b"not really a png").unwrap();
    let path = dir.join("cell.yaml");
    std::fs::write(
        &path,
        format!(
            r#"cell_name: preflight_probe
source:
  type: files
  glob: "{glob}/*.png"
  id_from: stem
{derive_block}
chunker: {{ type: fixed_overlap, window_words: 512, step_words: 512 }}
embedder: {{ type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768 }}
target:
  type: sqlite
  dsn_env: {dsn_env}
  database: ignored
  table: images
  mode: overwrite
  hnsw: false
"#,
            glob = dir.display(),
        ),
    )
    .unwrap();
    path
}

/// Ambiguous `derive:` routing — feature-independent, so this case runs in
/// every build configuration.
#[tokio::test]
async fn ambiguous_derive_keys_leave_the_target_table_intact() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("t.db");
    seed(&db, "images");
    let env_var = format!("CHUNKSHOP_PREFLIGHT_AMB_{}", std::process::id());
    std::env::set_var(&env_var, db.to_str().unwrap());

    let cfg_path = write_cell(
        dir.path(),
        &env_var,
        "  derive:\n    \"png|jpg\":\n      rung: image_meta\n    \"jpg|webp\":\n      rung: image_meta\n",
    );
    let cfg = load_config(&cfg_path).expect("config itself is well-formed");
    let err = format!("{:#}", run_cell(cfg).await.unwrap_err());

    assert!(
        err.contains("reachable from two different `derive` keys"),
        "must fail on the ambiguity, not on something later: {err}"
    );
    assert_eq!(
        row_count(&db, "images"),
        Some(1),
        "target table was dropped by a cell that could never have run"
    );
}

/// The shipped-sample scenario: `rung: clip_tags` in a build without
/// `media` + `embedder-hub`.
#[tokio::test]
#[cfg(not(all(feature = "media", feature = "embedder-hub")))]
async fn missing_cargo_feature_leaves_the_target_table_intact() {
    let dir = tempdir().unwrap();
    let db = dir.path().join("t.db");
    seed(&db, "images");
    let env_var = format!("CHUNKSHOP_PREFLIGHT_FEAT_{}", std::process::id());
    std::env::set_var(&env_var, db.to_str().unwrap());

    let cfg_path = write_cell(
        dir.path(),
        &env_var,
        "  derive:\n    png:\n      rung: clip_tags\n      vocabulary: [\"a chart or diagram\"]\n",
    );
    let cfg = load_config(&cfg_path).expect("config itself is well-formed");
    let err = format!("{:#}", run_cell(cfg).await.unwrap_err());

    // Asserting on the MESSAGE matters: without it, an offline CI box whose
    // embedder download fails would make this test pass for the wrong
    // reason (the table also survives when `FastembedEmbedder::new` errors).
    assert!(
        err.contains("requires the `media,embedder-hub` Cargo feature"),
        "must fail on the missing feature, before the embedder and the DDL: {err}"
    );
    assert_eq!(
        row_count(&db, "images"),
        Some(1),
        "target table was dropped by a cell that could never have run"
    );
}
