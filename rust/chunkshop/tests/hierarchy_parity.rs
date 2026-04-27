//! Cross-language byte-identical chunk parity for HierarchyChunker.
//!
//! Loads `tests/parity-fixtures/hierarchy_corpus.txt` (markdown source) and
//! `hierarchy_reference.json` (Python's chunked output produced by
//! `scripts/produce_rust_hierarchy_reference.py`), runs the Rust
//! `HierarchyChunker`, and asserts every chunk's `original_content`,
//! `embedded_content`, `heading` metadata, and `section_part` match exactly.

use std::path::PathBuf;

use chunkshop::chunker::HierarchyChunker;
use chunkshop::config::HierarchyChunkerConfig;
use chunkshop::source::Document;
use serde::Deserialize;
use serde_json::json;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[derive(Debug, Deserialize)]
struct RefChunk {
    seq_num: usize,
    original_content: String,
    embedded_content: String,
    heading: String,
    section_part: usize,
}

#[derive(Debug, Deserialize)]
struct RefConfig {
    prefix_heading: bool,
    min_section_chars: usize,
    max_chars: usize,
}

#[derive(Debug, Deserialize)]
struct Reference {
    doc_id: String,
    #[serde(default)]
    doc_title: Option<String>,
    config: RefConfig,
    chunks: Vec<RefChunk>,
}

#[test]
fn rust_hierarchy_chunks_match_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("hierarchy_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(fixtures_dir().join("hierarchy_reference.json"))
        .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse reference");

    let cfg = HierarchyChunkerConfig {
        prefix_heading: r.config.prefix_heading,
        min_section_chars: r.config.min_section_chars,
        max_chars: r.config.max_chars,
    };
    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: r.doc_title.clone(),
        metadata: json!({}),
    };
    let chunker = HierarchyChunker::new(cfg);
    let actual = chunker.chunk(&doc);

    assert_eq!(
        actual.len(),
        r.chunks.len(),
        "chunk count mismatch: rust={}, python={}",
        actual.len(),
        r.chunks.len()
    );
    for (i, (got, exp)) in actual.iter().zip(r.chunks.iter()).enumerate() {
        assert_eq!(got.seq_num, exp.seq_num, "chunk[{i}] seq_num");
        assert_eq!(
            got.original_content, exp.original_content,
            "chunk[{i}] original_content mismatch"
        );
        assert_eq!(
            got.embedded_content, exp.embedded_content,
            "chunk[{i}] embedded_content mismatch"
        );
        let h = got
            .metadata
            .get("heading")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let sp = got
            .metadata
            .get("section_part")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as usize;
        assert_eq!(h, exp.heading, "chunk[{i}] heading");
        assert_eq!(sp, exp.section_part, "chunk[{i}] section_part");
    }
}
