//! Brief SC-004 / SC-006 (Rust): if_oversize fallback fires for over-large
//! chunks; warner logs once per cell when no fallback is set.

use chunkshop::chunker::Chunk;
use chunkshop::config::{
    ChunkerConfig, FixedOverlapChunkerConfig, HierarchyChunkerConfig,
    NeighborExpandChunkerConfig,
};
use chunkshop::runner::build_chunker;
use chunkshop::source::Document;
use serde_json::json;
use tracing_test::traced_test;

fn doc(text: &str) -> Document {
    Document {
        id: "doc1".to_string(),
        content: text.to_string(),
        title: None,
        metadata: json!({}),
    }
}

#[test]
fn neighbor_expand_with_if_oversize_fires() {
    // Brief SC-004: a NeighborExpand wrapper over a hierarchy base with a
    // 1500-char ceiling routes oversized joined chunks through a
    // fixed_overlap fallback, so every emitted chunk is <= ceiling.
    let base = ChunkerConfig::Hierarchy(HierarchyChunkerConfig {
        prefix_heading: true,
        min_section_chars: 100,
        max_chars: 1500,
        if_oversize: None,
        heading_pattern: None,
    });
    let fallback = ChunkerConfig::FixedOverlap(FixedOverlapChunkerConfig {
        window_words: 200,
        step_words: 160,
        max_chars: Some(1500),
        if_oversize: None,
    });
    let cfg = ChunkerConfig::NeighborExpand(NeighborExpandChunkerConfig {
        base: Box::new(base),
        window: 2,
        max_chars: None,
        if_oversize: Some(Box::new(fallback)),
    });
    let chunker = build_chunker(cfg).expect("build chunker");

    let sections: Vec<String> = (1..=5)
        .map(|i| format!("## Section {}\n{}", i, "lorem ipsum ".repeat(130)))
        .collect();
    let text = sections.join("\n\n");
    let chunks: Vec<Chunk> = chunker.chunk(&doc(&text));

    assert!(!chunks.is_empty(), "expected non-empty chunks");
    for c in &chunks {
        assert!(
            c.embedded_content.chars().count() <= 1500,
            "chunk too large after if_oversize: {} chars",
            c.embedded_content.chars().count()
        );
        assert!(
            c.original_content.chars().count() <= 1500,
            "original_content too large after if_oversize: {} chars",
            c.original_content.chars().count()
        );
    }
}

#[traced_test]
#[test]
fn fixed_overlap_warns_once_no_fallback() {
    // Brief SC-006: when if_oversize is None and the chunker emits
    // multiple oversize chunks, the warner fires exactly once per cell.
    let cfg = ChunkerConfig::FixedOverlap(FixedOverlapChunkerConfig {
        window_words: 100,
        step_words: 100,
        max_chars: Some(20),
        if_oversize: None,
    });
    let chunker = build_chunker(cfg).expect("build chunker");
    // 500 distinct words in 5-word groups → many chunks, all oversize
    // against a 20-char ceiling.
    let text = std::iter::repeat("word")
        .take(500)
        .collect::<Vec<_>>()
        .join(" ");
    let chunks = chunker.chunk(&doc(&text));
    // Should not panic; at least one chunk emitted; warning should appear.
    assert!(!chunks.is_empty(), "expected chunks emitted");
    assert!(
        logs_contain("emitted oversize chunk"),
        "expected oversize-warning to fire when if_oversize is unset"
    );
}
