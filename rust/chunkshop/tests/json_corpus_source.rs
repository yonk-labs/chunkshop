//! Integration test for JsonCorpusSource. No Postgres needed.

use std::path::PathBuf;

use chunkshop::config::JsonCorpusSourceConfig;
use chunkshop::source::JsonCorpusSource;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn default_cfg(path: PathBuf) -> JsonCorpusSourceConfig {
    JsonCorpusSourceConfig {
        path: path.to_string_lossy().to_string(),
        documents_key: "documents".to_string(),
        id_field: "id".to_string(),
        content_field: "content".to_string(),
        title_field: Some("title".to_string()),
    }
}

#[test]
fn reads_3_documents_with_metadata() {
    let path = fixtures_dir().join("json_corpus_sample.json");
    let source = JsonCorpusSource::new(default_cfg(path));
    let docs = source.iter_documents().expect("iter");
    assert_eq!(docs.len(), 3);

    assert_eq!(docs[0].id, "doc-1");
    assert_eq!(docs[0].content, "First document body.");
    assert_eq!(docs[0].title.as_deref(), Some("First"));
    let meta0 = docs[0].metadata.as_object().expect("object");
    // id/content/title removed from metadata; tags + score survive.
    assert!(meta0.get("id").is_none());
    assert!(meta0.get("content").is_none());
    assert!(meta0.get("title").is_none());
    let tags = meta0.get("tags").and_then(|v| v.as_array()).expect("tags");
    assert_eq!(tags.len(), 2);
    assert_eq!(tags[0].as_str(), Some("a"));
    let score = meta0.get("score").and_then(|v| v.as_f64()).expect("score");
    assert!((score - 0.91).abs() < 1e-9);

    assert_eq!(docs[1].id, "doc-2");
    assert_eq!(docs[1].title.as_deref(), Some("Second"));

    // Third row has no title field present in the JSON — title must be None.
    assert_eq!(docs[2].id, "doc-3");
    assert_eq!(docs[2].title, None);
    let tags2 = docs[2]
        .metadata
        .as_object()
        .and_then(|m| m.get("tags").and_then(|v| v.as_array()))
        .expect("tags");
    assert_eq!(tags2.len(), 0);
}

#[test]
fn errors_when_documents_key_missing() {
    let path = fixtures_dir().join("json_corpus_sample.json");
    let mut cfg = default_cfg(path);
    cfg.documents_key = "rows_typo".to_string();
    let source = JsonCorpusSource::new(cfg);
    let err = source.iter_documents().unwrap_err().to_string();
    assert!(err.contains("rows_typo"), "expected key name in error: {err}");
}
