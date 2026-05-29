//! Symbol-aware chunker — splits source-code documents at symbol boundaries
//! via the per-language extractors in `chunkshop::codeparse::langs`.
//!
//! Mirrors `python/src/chunkshop/chunkers/symbol_aware.py`. Each emitted
//! chunk's `original_content` is the raw source slice for that symbol;
//! `embedded_content` is the same slice (no import-block prefix in v1 —
//! Python's import_block framing is a follow-up).
//!
//! Chunk metadata stamps `fqn`, `node_id`, `language`, `parent_name`,
//! `symbol_name`, `symbol_type`, `line_start`, `line_end`, and
//! `strategy = "symbol_aware"`. On syntax error / no symbols / unknown
//! language, v1 returns an empty Vec; Task 14 wires the real fallback to
//! `sentence_aware` and stamps `strategy = "symbol_aware_fallback"`.

use crate::chunker::{Chunk, ChunkerImpl};
use crate::codeparse::{code_symbol_node_id, Symbol};
use crate::config::SymbolAwareChunkerConfig;
use crate::sources::Document;
use serde_json::json;

pub struct SymbolAwareChunker {
    cfg: SymbolAwareChunkerConfig,
}

impl SymbolAwareChunker {
    pub fn new(cfg: SymbolAwareChunkerConfig) -> Self {
        Self { cfg }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let Some(language) = detect_language(doc) else {
            return self.fallback(doc, "language_undetected");
        };

        let content = doc.content.as_str();
        let symbols = extract_symbols_for_language(&language, &doc.id, content);

        if symbols.is_empty() {
            return self.fallback(doc, "no_symbols");
        }

        let project_id = self
            .cfg
            .project_id
            .clone()
            .unwrap_or_else(|| "default".to_string());

        let mut chunks: Vec<Chunk> = Vec::with_capacity(symbols.len());
        let lines: Vec<&str> = content.lines().collect();

        for (seq_idx, sym) in symbols.iter().enumerate() {
            let source_slice = slice_lines(&lines, sym.line_start, sym.line_end);
            let node_id = code_symbol_node_id(&project_id, &language, &doc.id, &sym.fqn);

            let metadata = json!({
                "strategy": "symbol_aware",
                "fqn": sym.fqn,
                "node_id": node_id,
                "language": language,
                "parent_name": sym.parent_name,
                "symbol_name": sym.name,
                "symbol_type": sym.symbol_type,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
            });

            chunks.push(Chunk {
                doc_id: doc.id.clone(),
                seq_num: seq_idx,
                original_content: source_slice.clone(),
                embedded_content: source_slice,
                metadata,
            });
        }

        chunks
    }

    /// Placeholder fallback — returns empty Vec for now. Task 14 wires the
    /// real fallback to `SentenceAwareChunker` with
    /// `strategy = "symbol_aware_fallback"`.
    fn fallback(&self, _doc: &Document, _reason: &str) -> Vec<Chunk> {
        Vec::new()
    }
}

impl ChunkerImpl for SymbolAwareChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

fn detect_language(doc: &Document) -> Option<String> {
    // Try metadata `path` / `source_path` first, then doc.id. Mirrors Python
    // symbol_aware._detect_language_from_meta at lines 58-78.
    for key in ["path", "source_path"] {
        if let Some(val) = doc.metadata.get(key).and_then(|v| v.as_str()) {
            if let Some(lang) = lang_from_extension(val) {
                return Some(lang);
            }
        }
    }
    lang_from_extension(&doc.id)
}

fn lang_from_extension(path: &str) -> Option<String> {
    let ext = std::path::Path::new(path).extension()?.to_str()?;
    match ext {
        "py" => Some("python".to_string()),
        "java" => Some("java".to_string()),
        _ => None, // Go/TS/JS/Rust added in follow-up tasks
    }
}

fn extract_symbols_for_language(language: &str, file_path: &str, source: &str) -> Vec<Symbol> {
    match language {
        #[cfg(feature = "code-aware-python")]
        "python" => crate::codeparse::langs::python::extract_symbols(file_path, source),
        #[cfg(feature = "code-aware-java")]
        "java" => crate::codeparse::langs::java::extract_symbols(file_path, source),
        _ => {
            // Silences unused-variable warnings when no grammar feature is on.
            let _ = (file_path, source);
            Vec::new()
        }
    }
}

fn slice_lines(lines: &[&str], start_1based: u32, end_1based: u32) -> String {
    let s = (start_1based.saturating_sub(1)) as usize;
    let e = std::cmp::min(end_1based as usize, lines.len());
    if s >= e {
        return String::new();
    }
    lines[s..e].join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_doc(id: &str, content: &str) -> Document {
        Document {
            id: id.to_string(),
            content: content.to_string(),
            title: None,
            metadata: serde_json::json!({}),
            fingerprint: None,
        }
    }

    #[cfg(feature = "code-aware-python")]
    #[test]
    fn chunks_python_at_symbol_boundaries() {
        let doc = make_doc(
            "test.py",
            "def hello():\n    pass\n\nclass Foo:\n    def bar(self):\n        pass\n",
        );
        let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
        let chunks = chunker.chunk(&doc);

        // 3 symbols: hello (function), Foo (class), bar (method)
        assert_eq!(
            chunks.len(),
            3,
            "expected 3 symbols, got {}: {:?}",
            chunks.len(),
            chunks
                .iter()
                .map(|c| c.metadata.get("symbol_name").cloned())
                .collect::<Vec<_>>()
        );
        for c in &chunks {
            assert_eq!(
                c.metadata.get("strategy").and_then(|s| s.as_str()),
                Some("symbol_aware")
            );
            assert!(c.metadata.get("fqn").and_then(|s| s.as_str()).is_some());
            assert!(c.metadata.get("node_id").and_then(|s| s.as_str()).is_some());
        }
    }

    #[test]
    fn empty_doc_falls_back_to_empty() {
        // No language detected from extension-less id → fallback (empty in v1)
        let doc = make_doc("plain-text", "hello world");
        let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
        let chunks = chunker.chunk(&doc);
        assert!(
            chunks.is_empty(),
            "v1 fallback returns empty; Task 14 wires real fallback"
        );
    }
}
