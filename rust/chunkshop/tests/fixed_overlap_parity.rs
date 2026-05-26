//! Cross-language byte-identical chunk parity for FixedOverlapChunker.

use std::path::PathBuf;

use chunkshop::chunker::FixedOverlapChunker;
use chunkshop::config::FixedOverlapChunkerConfig;
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
struct RefChunk {
    seq_num: usize,
    original_content: String,
    embedded_content: String,
    metadata: serde_json::Value,
}

#[derive(Debug, Deserialize)]
struct RefConfig {
    window_words: usize,
    step_words: usize,
}

#[derive(Debug, Deserialize)]
struct Reference {
    doc_id: String,
    config: RefConfig,
    chunks: Vec<RefChunk>,
}

#[test]
fn rust_fixed_overlap_chunks_match_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("fixed_overlap_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(fixtures_dir().join("fixed_overlap_reference.json"))
        .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse reference");

    let cfg = FixedOverlapChunkerConfig {
        window_words: r.config.window_words,
        step_words: r.config.step_words,
        max_chars: None,
        if_oversize: None,
    };
    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: None,
        metadata: json!({}),
        fingerprint: None,
    };
    let chunker = FixedOverlapChunker::new(cfg).expect("valid cfg");
    let actual = chunker.chunk(&doc);

    assert_eq!(actual.len(), r.chunks.len(), "chunk count");
    for (i, (got, exp)) in actual.iter().zip(r.chunks.iter()).enumerate() {
        assert_eq!(got.seq_num, exp.seq_num, "chunk[{i}] seq_num");
        assert_eq!(
            got.original_content, exp.original_content,
            "chunk[{i}] original"
        );
        assert_eq!(
            got.embedded_content, exp.embedded_content,
            "chunk[{i}] embedded"
        );
        assert_eq!(got.metadata, exp.metadata, "chunk[{i}] metadata");
    }
}
