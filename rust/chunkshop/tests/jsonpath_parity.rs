//! Cross-language byte-identical parity for JsonPathFramer.

use std::path::PathBuf;

use chunkshop::config::JsonPathFramerConfig;
use chunkshop::framer::{FramerImpl, JsonPathFramer};
use chunkshop::sources::Document;
use serde::Deserialize;
use serde_json::json;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[derive(Debug, Deserialize)]
struct RefFrame {
    id: String,
    content: String,
    title: Option<String>,
    metadata: serde_json::Value,
}

#[derive(Debug, Deserialize)]
struct RefConfig {
    row_path: String,
    title_path: Option<String>,
    body_path: String,
}

#[derive(Debug, Deserialize)]
struct Reference {
    raw_id: String,
    raw_title: Option<String>,
    config: RefConfig,
    frames: Vec<RefFrame>,
}

#[test]
fn rust_jsonpath_matches_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("framer_jsonpath_corpus.json"))
        .expect("read corpus");
    let ref_json =
        std::fs::read_to_string(fixtures_dir().join("framer_jsonpath_reference.json"))
            .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse reference");

    let f = JsonPathFramer::new(JsonPathFramerConfig {
        row_path: r.config.row_path,
        title_path: r.config.title_path,
        body_path: r.config.body_path,
    });
    let raw = Document {
        id: r.raw_id.clone(),
        content: corpus,
        title: r.raw_title.clone(),
        metadata: json!({}),
    };
    let actual = f.frame(&raw).expect("frame");
    assert_eq!(actual.len(), r.frames.len(), "frame count");
    for (i, (got, exp)) in actual.iter().zip(r.frames.iter()).enumerate() {
        assert_eq!(got.id, exp.id, "frame[{i}] id");
        assert_eq!(got.content, exp.content, "frame[{i}] content");
        assert_eq!(got.title, exp.title, "frame[{i}] title");
        assert_eq!(got.metadata, exp.metadata, "frame[{i}] metadata");
    }
}
