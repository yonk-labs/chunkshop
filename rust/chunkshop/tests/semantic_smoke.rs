//! Smoke test: SemanticChunker runs end-to-end on a sample corpus and
//! produces non-empty, well-formed chunks. Does NOT assert byte-identical
//! cross-language parity (algorithmically infeasible — see brief).

use std::path::PathBuf;

use chunkshop::chunker::SemanticChunker;
use chunkshop::config::SemanticChunkerConfig;
use chunkshop::source::Document;
use serde_json::json;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[test]
fn semantic_chunker_runs_end_to_end() {
    let path = fixtures_dir().join("framer_heading_corpus.md");
    let content = std::fs::read_to_string(&path).expect("read corpus");

    let cfg = SemanticChunkerConfig {
        boundary_model: "sentence-transformers/all-MiniLM-L6-v2-int8".to_string(),
        breakpoint_percentile: 95,
        min_sentences_per_chunk: 3,
        max_chunk_chars: 2000,
        sentence_splitter: "naive".to_string(),
    };

    // Construction triggers boundary-model download. If HF is unreachable,
    // skip the test rather than fail.
    let chunker = match SemanticChunker::new(cfg) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("skipping semantic_smoke: boundary embedder init failed: {e:#}");
            return;
        }
    };

    let doc = Document {
        id: "smoke".into(),
        content,
        title: None,
        metadata: json!({}),
    };
    let chunks = chunker.chunk(&doc);
    assert!(!chunks.is_empty(), "semantic chunker emitted zero chunks");
    for (i, c) in chunks.iter().enumerate() {
        assert_eq!(c.metadata["strategy"], "semantic", "chunk[{i}] strategy");
        assert!(
            c.original_content.chars().count() <= 2000,
            "chunk[{i}] over max_chunk_chars: {} chars",
            c.original_content.chars().count()
        );
        assert!(!c.original_content.is_empty(), "chunk[{i}] empty content");
    }
    eprintln!("semantic_smoke: produced {} chunks", chunks.len());
}
