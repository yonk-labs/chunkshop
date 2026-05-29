//! Code-aware chunkers — feature-gated. Existing prose/summarization
//! chunkers remain in the flat `crate::chunker` module (additive split
//! per RM-C plan: new code-aware module under chunkers/ avoids touching
//! the existing 2000+ line chunker.rs structurally).

#[cfg(all(feature = "code-aware", feature = "chunkers"))]
pub mod symbol_aware;
