//! Cross-language byte-identical parity for HierarchicalSummaryChunker.
//! Two scenarios: fixed_n grouping and section_aware grouping. Both use
//! the passthrough summarizer.

use std::path::PathBuf;

use chunkshop::chunker::{
    ChunkerImpl, HierarchicalGrouping, HierarchicalSummaryChunker, HierarchyChunker,
};
use chunkshop::config::{
    HierarchyChunkerConfig, PassthroughSummarizerConfig, SummarizerConfig,
};
use chunkshop::source::Document;
use chunkshop::summarizer::build_summarizer;
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
    doc_id: String,
    seq_num: usize,
    original_content: String,
    embedded_content: String,
    metadata: serde_json::Value,
}

#[derive(Debug, Deserialize)]
struct Reference {
    doc_id: String,
    chunks: Vec<RefChunk>,
}

fn assert_chunks_match(actual: &[chunkshop::chunker::Chunk], expected: &[RefChunk]) {
    assert_eq!(
        actual.len(),
        expected.len(),
        "chunk count mismatch: rust={}, python={}",
        actual.len(),
        expected.len()
    );
    for (i, (got, exp)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got.doc_id, exp.doc_id, "chunk[{i}] doc_id");
        assert_eq!(got.seq_num, exp.seq_num, "chunk[{i}] seq_num");
        assert_eq!(
            got.original_content, exp.original_content,
            "chunk[{i}] original_content"
        );
        assert_eq!(
            got.embedded_content, exp.embedded_content,
            "chunk[{i}] embedded_content"
        );
        assert_eq!(got.metadata, exp.metadata, "chunk[{i}] metadata");
    }
}

fn make_base() -> Box<dyn ChunkerImpl + Send + Sync> {
    Box::new(
        HierarchyChunker::new(HierarchyChunkerConfig {
            prefix_heading: true,
            min_section_chars: 100,
            max_chars: 400,
            if_oversize: None,
            heading_pattern: None,
        })
        .expect("build hierarchy chunker"),
    )
}

fn make_chunker(grouping: HierarchicalGrouping) -> HierarchicalSummaryChunker {
    let summarizer_cfg = SummarizerConfig::Passthrough(PassthroughSummarizerConfig::default());
    let summarizer = build_summarizer(&summarizer_cfg).expect("build summarizer");
    HierarchicalSummaryChunker::new(make_base(), summarizer, "passthrough", grouping, None, None)
}

#[test]
fn rust_hierarchical_summary_fixed_n_matches_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("hierarchy_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(
        fixtures_dir().join("hierarchical_summary_fixed_n_reference.json"),
    )
    .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse");

    let chunker = make_chunker(HierarchicalGrouping::FixedN(3));
    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: None,
        metadata: json!({}),
    };
    let actual = chunker.chunk(&doc);
    assert_chunks_match(&actual, &r.chunks);
}

#[test]
fn rust_hierarchical_summary_section_aware_matches_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("hierarchy_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(
        fixtures_dir().join("hierarchical_summary_section_aware_reference.json"),
    )
    .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse");

    let chunker = make_chunker(HierarchicalGrouping::SectionAware);
    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: None,
        metadata: json!({}),
    };
    let actual = chunker.chunk(&doc);
    assert_chunks_match(&actual, &r.chunks);
}
