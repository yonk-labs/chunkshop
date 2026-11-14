//! recommended.yaml must be a valid cell config: the explanatory note is a
//! real YAML comment, not a data key (cell configs forbid unknown keys).

use std::collections::BTreeMap;

use chunkshop::bakeoff::config::{BakeoffConfig, BakeoffResults, ComboResult};
use chunkshop::bakeoff::output::write_recommended_yaml;

const TARGETS_YAML: &str = r#"
targets:
  - type: postgres
    dsn_env: CHUNKSHOP_TEST_DSN
    database: chunkshop_review_p1t
"#;

const LEGACY_YAML: &str = r#"
target:
  dsn_env: CHUNKSHOP_TEST_DSN
  schema: chunkshop_review_p1t
"#;

fn check(target_block: &str) {
    let yaml = format!(
        r#"
name: p1t
source:
  type: files
  glob: "/tmp/does-not-matter/*.md"
gold_queries: []
matrix:
  embedders:
    - model_name: BAAI/bge-small-en-v1.5
      dim: 384
  chunkers:
    - type: hierarchy
{target_block}"#
    );
    let cfg: BakeoffConfig = serde_yaml_ng::from_str(&yaml).expect("bakeoff cfg");
    let mut aggregate = BTreeMap::new();
    aggregate.insert("mrr".to_string(), 0.5);
    aggregate.insert("recall_at_1".to_string(), 0.25);
    let results = BakeoffResults {
        run_name: "p1t".into(),
        started_at: "now".into(),
        corpus_label: "c".into(),
        n_queries: 0,
        n_combos: 1,
        combos: vec![ComboResult {
            backend: "postgres".into(),
            chunker_key: "hierarchy".into(),
            embedder_key: "bge_small_en_v1_5".into(),
            chunker_label: "hierarchy".into(),
            embedder_label: "bge".into(),
            table: "t".into(),
            ingest_chunks: 0,
            ingest_wall_seconds: 0.0,
            query_wall_ms_mean: 0.0,
            ingest_embed_seconds: 0.0,
            aggregate,
            per_query: vec![],
        }],
        gold_queries: vec![],
        query_embed_seconds_by_embedder: BTreeMap::new(),
    };
    let dir = tempfile::tempdir().unwrap();
    let out = write_recommended_yaml(&cfg, &results, dir.path()).unwrap();
    let text = std::fs::read_to_string(&out).unwrap();

    assert!(text.starts_with("# "), "note must be a leading YAML comment:\n{text}");
    let comment_lines: Vec<&str> = text.lines().take_while(|l| l.starts_with("# ")).collect();
    assert_eq!(comment_lines.len(), 2, "two comment lines expected:\n{text}");
    assert!(comment_lines[0].starts_with("# Top combo from"), "{text}");
    assert!(comment_lines[1].starts_with("# Point `source` at your real corpus"), "{text}");

    let v: serde_yaml_ng::Value = serde_yaml_ng::from_str(&text).unwrap();
    assert!(v.get("# NOTE").is_none(), "no '# NOTE' data key:\n{text}");

    // Must load through the crate's own (extra=forbid) cell-config loader.
    chunkshop::config::load_config(&out).expect("recommended.yaml must load as a cell config");
}

#[test]
fn recommended_yaml_multi_target_note_is_a_comment() {
    check(TARGETS_YAML);
}

#[test]
fn recommended_yaml_legacy_target_note_is_a_comment() {
    check(LEGACY_YAML);
}
