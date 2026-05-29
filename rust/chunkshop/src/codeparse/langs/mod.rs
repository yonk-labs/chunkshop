//! Per-language tree-sitter symbol extractors.
//!
//! Each `langs::<lang>` module is feature-gated. The umbrella `code-aware`
//! feature is implied by any per-grammar feature; turning on
//! `code-aware-python` pulls in `tree-sitter` + `tree-sitter-python` + the
//! Python extractor.

#[cfg(feature = "code-aware-python")]
pub mod python;

#[cfg(feature = "code-aware-java")]
pub mod java;
