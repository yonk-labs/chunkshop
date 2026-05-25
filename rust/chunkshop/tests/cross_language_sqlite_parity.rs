//! R3-SC-007: cross-language vector parity. Python writes a known doc with
//! known vectors to a temp .db; Rust opens the file, runs query_top_k,
//! asserts results match within 1e-5.
//!
//! Skips when `uv` is not on PATH, or Python's `sqlite_vec` is unavailable.
//! Skip messages are logged via eprintln so CI can surface them.

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::{Sink, SqliteSink};
use std::process::Command;
use tempfile::tempdir;

fn skip(reason: &str) {
    eprintln!("SKIPPING cross_language_sqlite_parity: {reason}");
}

fn uv_available() -> bool {
    Command::new("uv")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn python_has_sqlite_vec(python_dir: &str) -> bool {
    Command::new("uv")
        .args(["run", "python", "-c", "import sqlite_vec; print('ok')"])
        .current_dir(python_dir)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[tokio::test]
async fn rust_reads_python_written_db() {
    // Default: relative to the rust crate at compile time.
    let manifest = env!("CARGO_MANIFEST_DIR"); // .../rust/chunkshop
    let python_dir = std::env::var("CHUNKSHOP_PY_DIR").unwrap_or_else(|_| {
        std::path::Path::new(manifest)
            .parent()
            .unwrap() // .../rust
            .parent()
            .unwrap() // worktree root
            .join("python")
            .to_string_lossy()
            .to_string()
    });

    if !uv_available() {
        skip("uv not on PATH");
        return;
    }
    if !python_has_sqlite_vec(&python_dir) {
        skip("python sqlite_vec unavailable");
        return;
    }

    let dir = tempdir().unwrap();
    let db_path = dir.path().join("xlang.db");
    let env = format!("R3_XLANG_{}", std::process::id());

    // Python script: write 5 chunks with known orthogonal-ish vectors.
    let py = r#"
import os, sys, numpy as np
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks.sqlite import SqliteSink

dsn_env = "X_DSN"
os.environ[dsn_env] = sys.argv[1]
cfg = TargetConfig(type="sqlite", dsn_env=dsn_env, database="ignored",
                   table="chunks", mode="overwrite", hnsw=False, source_tag="t1")
backend = SQLiteBackend(dsn_env=dsn_env)
sink = SqliteSink(cfg, backend, embed_dim=4)
sink.create_table()

chunks = [Chunk(doc_id="d1", seq_num=i, original_content=f"c{i}",
                embedded_content=f"c{i}", metadata={}) for i in range(5)]
embs = np.array([
    [1.0, 0.0, 0.0, 0.0],
    [0.9, 0.1, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
], dtype=np.float32)
sink.write_document("d1", chunks, embs, [[]] * 5)
print("OK")
"#;

    let py_file = dir.path().join("write.py");
    std::fs::write(&py_file, py).unwrap();
    let output = Command::new("uv")
        .args([
            "run",
            "python",
            py_file.to_str().unwrap(),
            db_path.to_str().unwrap(),
        ])
        .current_dir(&python_dir)
        .output()
        .expect("spawn python");
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        panic!("python writer failed:\n{stderr}");
    }

    // Rust opens the same file and queries.
    std::env::set_var(&env, db_path.to_str().unwrap());
    let backend = SQLiteBackend::new(env.clone());
    let cfg = SqliteTargetConfig {
        dsn_env: env,
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: "create_if_missing".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
        documents: None,
    };
    let sink = SqliteSink::new(cfg, backend, 4);
    sink.create_table().await.unwrap();
    let results = sink.query_top_k(&[1.0, 0.0, 0.0, 0.0], 3).await.unwrap();
    assert_eq!(results.len(), 3);
    assert_eq!(results[0].1, 0, "top-1 must be chunk 0 (exact match)");

    // sqlite-vec's default distance metric for FLOAT[N] is L2. Top-1 should be ~0.0.
    assert!(
        results[0].2 < 1e-5,
        "exact match distance: {}",
        results[0].2
    );
    assert!(results[1].2 > results[0].2, "second is farther");
}
