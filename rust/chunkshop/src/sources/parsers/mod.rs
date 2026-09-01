//! Per-extension file derivers. Mirrors `python/src/chunkshop/sources/parsers/`.
//! `Deriver::derive(path) -> ParseResult` extracts a file's content plus any
//! metadata the extraction produced. `FilesSource` wraps the result into a
//! `Document`. Unlike Python's `FileParser`/`Deriver` split (kept separate
//! there for backward compatibility with pre-1.0 third-party parsers), Rust
//! has no such compatibility surface to preserve — one trait, from day one.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use thiserror::Error;

// `registry` (extension -> Deriver dispatch) and `text` (the always-available
// TextDeriver) are not `media`-gated — every text-only YAML must keep
// working with no media feature involved at all.
pub mod registry;
pub mod text;

// `docx`, `xlsx` are declared by the task that creates each file (Tasks
// 4-5) — same convention as `registry`/`text` above. Declaring either
// ahead of its file existing would break every `--features media` build
// unconditionally, media-gated or not: `#[cfg(feature = "media")]` only
// skips a `pub mod X;` line when the feature is off, but Rust still
// requires `X.rs` to exist at name-resolution time whenever the feature is
// on. `html` is Task 2's and `pdf` is Task 3's, so both are declared now.
#[cfg(feature = "media")]
pub mod html;
#[cfg(feature = "media")]
pub mod pdf;

/// What a deriver returns: extracted text plus whatever metadata the
/// extraction produced (dimensions, sheet names, model provenance, ...).
#[derive(Debug, Clone, PartialEq)]
pub struct ParseResult {
    pub text: String,
    pub metadata: Map<String, Value>,
}

impl ParseResult {
    pub fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            metadata: Map::new(),
        }
    }

    pub fn with_metadata(text: impl Into<String>, metadata: Map<String, Value>) -> Self {
        Self {
            text: text.into(),
            metadata,
        }
    }
}

/// Mirrors Python's `EmptyDerivationError` — a recognized file that produced
/// no text. Raised instead of silently indexing an empty document.
#[derive(Debug, Error)]
#[error("{path} produced no text via {parser}")]
pub struct EmptyDerivationError {
    pub path: PathBuf,
    pub parser: String,
}

/// Mirrors Python's `RuntimeError` "install the extra" convention (NN-6): a
/// known format was matched but the binary wasn't built with the feature
/// that provides it.
#[derive(Debug, Error)]
#[error("{format} parsing requires the `{feature}` Cargo feature; rebuild with --features {feature}")]
pub struct MissingFeatureError {
    pub format: &'static str,
    pub feature: &'static str,
}

/// A file recognized but not yet parseable — distinct from `MissingFeatureError`
/// (which names a feature flag that WOULD fix it). Used for PPTX (Task 2):
/// there is no Rust crate committed to yet, so this is not a build-config fix.
#[derive(Debug, Error)]
#[error("{format} parsing is not yet implemented in the Rust port (see docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md); falling back to text would silently corrupt binary content, so this file was refused instead")]
pub struct UnsupportedFormatError {
    pub format: &'static str,
}

pub trait Deriver: Send + Sync {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_result_new_has_empty_metadata() {
        let r = ParseResult::new("hello");
        assert_eq!(r.text, "hello");
        assert!(r.metadata.is_empty());
    }

    #[test]
    fn parse_result_with_metadata_holds_both() {
        let mut md = Map::new();
        md.insert("width".to_string(), Value::from(8));
        let r = ParseResult::with_metadata("hi", md.clone());
        assert_eq!(r.text, "hi");
        assert_eq!(r.metadata, md);
    }

    #[test]
    fn empty_derivation_error_names_path_and_parser() {
        let e = EmptyDerivationError {
            path: PathBuf::from("scanned.pdf"),
            parser: "PdfParser".to_string(),
        };
        let msg = e.to_string();
        assert!(msg.contains("scanned.pdf"), "{msg}");
        assert!(msg.contains("PdfParser"), "{msg}");
    }
}
