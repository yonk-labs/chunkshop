//! Tests for v0.4.0 legacy-form YAML rejection. Each must produce a friendly
//! error message that names the new field/value (V4-SC-006).

use std::io::Write;
use tempfile::NamedTempFile;

use chunkshop::load_config;

fn write_yaml(yaml: &str) -> NamedTempFile {
    let mut f = NamedTempFile::new().expect("create temp file");
    f.write_all(yaml.as_bytes()).expect("write yaml");
    f
}

const VALID_REST: &str = r#"
cell_name: test_cell
source:
  type: inline
chunker:
  type: fixed_overlap
  chunk_size: 200
  overlap: 50
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
extractor:
  type: none
framer:
  type: none
"#;

#[test]
fn rejects_legacy_pgvector_type() {
    let yaml = format!(
        r#"target:
  type: pgvector
  dsn_env: X
  database: y
  table: z
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("pgvector") && msg.contains("postgres") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}

#[test]
fn rejects_legacy_schema_field() {
    let yaml = format!(
        r#"target:
  type: postgres
  dsn_env: X
  schema: y
  table: z
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("schema") && msg.contains("database") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}

#[test]
fn rejects_legacy_overwrite_bool() {
    let yaml = format!(
        r#"target:
  type: postgres
  dsn_env: X
  database: y
  table: z
  overwrite: true
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("overwrite") && msg.contains("mode") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}
