//! Cross-language byte-identical chunk parity for NeighborExpandChunker
//! wrapping a HierarchyChunker base. Verifies the recursive ChunkerConfig
//! deserialization, the trait-based dispatch, and the metadata-merge behavior.

use std::path::PathBuf;

use chunkshop::chunker::{HierarchyChunker, NeighborExpandChunker};
use chunkshop::config::HierarchyChunkerConfig;
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
struct RefBaseConfig {
    prefix_heading: bool,
    min_section_chars: usize,
    max_chars: usize,
}

#[derive(Debug, Deserialize)]
struct RefConfig {
    window: usize,
    base: RefBaseConfig,
}

#[derive(Debug, Deserialize)]
struct Reference {
    doc_id: String,
    config: RefConfig,
    chunks: Vec<RefChunk>,
}

#[test]
fn rust_neighbor_expand_chunks_match_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("neighbor_expand_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(fixtures_dir().join("neighbor_expand_reference.json"))
        .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse reference");

    let base = HierarchyChunker::new(HierarchyChunkerConfig {
        prefix_heading: r.config.base.prefix_heading,
        min_section_chars: r.config.base.min_section_chars,
        max_chars: r.config.base.max_chars,
        if_oversize: None,
        heading_pattern: None,
    })
    .expect("build hierarchy chunker");
    let chunker = NeighborExpandChunker::new(r.config.window, Box::new(base), None, None);

    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: None,
        metadata: json!({}),
        fingerprint: None,
    };
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
