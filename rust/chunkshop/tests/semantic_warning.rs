//! Brief SC-007: Rust semantic chunker logs WARN on hard-split (parity with Python).
//!
//! When `split_if_too_large` returns more than one sub-chunk for a single
//! span, `SemanticChunker` should emit a `tracing::warn!` event with fields
//! `max_chunk_chars`, `span_start`, `span_end`, `body_len`, `sub_chunks` —
//! matching Python `chunkers/semantic.py:120`.

use chunkshop::chunker::SemanticChunker;
use chunkshop::config::SemanticChunkerConfig;
use chunkshop::sources::Document;
use serde_json::json;
use tracing_test::traced_test;

#[traced_test]
#[test]
fn semantic_oversize_logs_warning() {
    // Tiny `max_chunk_chars` ensures the single-sentence body trips the
    // hard-split branch. `min_sentences_per_chunk: 1` keeps spans intact.
    let cfg = SemanticChunkerConfig {
        boundary_model: "sentence-transformers/all-MiniLM-L6-v2-int8".to_string(),
        breakpoint_percentile: 95,
        min_sentences_per_chunk: 1,
        max_chunk_chars: 200,
        sentence_splitter: "naive".to_string(),
        if_oversize: None,
    };

    // Construction triggers a boundary-model download. Skip on init failure
    // (matches the existing `semantic_smoke` test pattern — see
    // `tests/semantic_smoke.rs`).
    let chunker = match SemanticChunker::new(cfg) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("skipping semantic_warning: boundary embedder init failed: {e:#}");
            return;
        }
    };

    // Single very long sentence → hits the `sentences.len() == 1` branch
    // → `split_if_too_large` hard-splits → warning fires.
    let body = "lorem ipsum dolor sit amet ".repeat(200);
    let doc = Document {
        id: "doc1".into(),
        content: body,
        title: None,
        metadata: json!({}),
    };
    let chunks = chunker.chunk(&doc);
    eprintln!("semantic_warning: produced {} chunks", chunks.len());

    assert!(
        logs_contain("semantic chunk exceeded max_chunk_chars"),
        "expected hard-split warning to be emitted by SemanticChunker; got {} chunks",
        chunks.len()
    );
}
