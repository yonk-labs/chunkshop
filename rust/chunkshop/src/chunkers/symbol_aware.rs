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

        // Python-specific: tree-sitter is error-tolerant, so check
        // has_error() explicitly. Matches python/src/chunkshop/chunkers/
        // symbol_aware.py:120-132 which uses ast.parse to detect SyntaxError.
        #[cfg(feature = "code-aware-python")]
        if language == "python"
            && crate::codeparse::langs::python::has_syntax_errors(content)
        {
            return self.fallback(doc, "python_syntax_error");
        }

        // FQN / node_id are derived from the document's *logical* path so the
        // same file always mints the same node_id across runs. Prefer
        // metadata.path / source_path over doc.id (which `id_from: stem`
        // collapses to the file stem and would lose the directory prefix
        // Python's build_fqn relies on). Mirrors Python's
        // `_doc_file_path` at symbol_aware.py:395-408.
        let file_path = doc_file_path(doc);

        let symbols = extract_symbols_for_language(&language, &file_path, content);

        if symbols.is_empty() {
            return self.fallback(doc, "no_symbols");
        }

        let project_id = self
            .cfg
            .project_id
            .clone()
            .unwrap_or_else(|| "default".to_string());

        // Python emits top-level symbols only — methods (parent_name=Some(_))
        // bundle into their parent class chunk via the class's full line span.
        // Mirrors python/src/chunkshop/chunkers/symbol_aware.py:240
        // (`top_level = [s for s in result.symbols if s.parent_name is None]`).
        let top_level: Vec<&Symbol> = symbols.iter().filter(|s| s.parent_name.is_none()).collect();
        if top_level.is_empty() {
            return self.fallback(doc, "no_symbols");
        }

        let mut chunks: Vec<Chunk> = Vec::with_capacity(top_level.len());
        let lines: Vec<&str> = content.lines().collect();

        for (seq_idx, sym) in top_level.iter().enumerate() {
            let source_slice = slice_lines(&lines, sym.line_start, sym.line_end);
            let node_id = code_symbol_node_id(&project_id, &language, &file_path, &sym.fqn);

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

    fn fallback(&self, doc: &Document, reason: &str) -> Vec<Chunk> {
        use crate::chunker::SentenceAwareChunker;
        use crate::config::SentenceAwareChunkerConfig;

        let inner = SentenceAwareChunker::new(SentenceAwareChunkerConfig {
            doc_type: "prose".to_string(),
            max_chars: 2000,
            min_chars: 200,
            if_oversize: None,
        });
        let mut chunks = inner.chunk(doc);
        for c in &mut chunks {
            // Stamp strategy override + reason for downstream observability.
            // Mirror of Python's strategy='symbol_aware_fallback' semantics
            // at python/src/chunkshop/chunkers/symbol_aware.py:120-132.
            if let Some(obj) = c.metadata.as_object_mut() {
                obj.insert("strategy".to_string(), json!("symbol_aware_fallback"));
                obj.insert("fallback_reason".to_string(), json!(reason));
            }
        }
        chunks
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

/// Resolve the logical file path used for FQN / node_id derivation.
/// Order: `metadata.path` > `metadata.source_path` > `doc.id`.
/// Mirrors Python's `_doc_file_path` at symbol_aware.py:395-408.
fn doc_file_path(doc: &Document) -> String {
    for key in ["path", "source_path"] {
        if let Some(val) = doc.metadata.get(key).and_then(|v| v.as_str()) {
            if !val.is_empty() {
                return val.to_string();
            }
        }
    }
    doc.id.clone()
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

        // Top-level only: hello (function) + Foo (class). The `bar` method
        // bundles into the Foo chunk via Foo's line span, matching Python
        // symbol_aware.py:240 top_level filter.
        assert_eq!(
            chunks.len(),
            2,
            "expected 2 top-level symbols, got {}: {:?}",
            chunks.len(),
            chunks
                .iter()
                .map(|c| c.metadata.get("symbol_name").cloned())
                .collect::<Vec<_>>()
        );
        let names: Vec<&str> = chunks
            .iter()
            .filter_map(|c| c.metadata.get("symbol_name").and_then(|v| v.as_str()))
            .collect();
        assert!(names.contains(&"hello"), "missing hello: {:?}", names);
        assert!(names.contains(&"Foo"), "missing Foo: {:?}", names);
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
    fn unknown_language_falls_back_to_sentence_aware() {
        let doc = make_doc("mystery.xyz", "some random content here that should still chunk\n");
        let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
        let chunks = chunker.chunk(&doc);
        assert!(!chunks.is_empty(), "unknown language should still produce chunks via sentence_aware");
        let strategy = chunks[0]
            .metadata
            .get("strategy")
            .and_then(|s| s.as_str())
            .unwrap_or("");
        assert_eq!(strategy, "symbol_aware_fallback");
        let reason = chunks[0]
            .metadata
            .get("fallback_reason")
            .and_then(|s| s.as_str())
            .unwrap_or("");
        assert_eq!(reason, "language_undetected");
    }

    #[cfg(feature = "code-aware-python")]
    #[test]
    fn python_syntax_error_falls_back() {
        // Missing close paren, no body — tree-sitter returns a tree with ERROR nodes
        let doc = make_doc(
            "broken.py",
            "def hello(\n    # missing close paren\n",
        );
        let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
        let chunks = chunker.chunk(&doc);
        assert!(!chunks.is_empty(), "syntax error should fall back, not produce empty");
        let strategy = chunks[0]
            .metadata
            .get("strategy")
            .and_then(|s| s.as_str())
            .unwrap_or("");
        assert_eq!(strategy, "symbol_aware_fallback");
        let reason = chunks[0]
            .metadata
            .get("fallback_reason")
            .and_then(|s| s.as_str())
            .unwrap_or("");
        assert_eq!(reason, "python_syntax_error");
    }
}
