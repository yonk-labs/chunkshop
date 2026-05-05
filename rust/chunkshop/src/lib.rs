//! chunkshop-rs — minimal Rust port of chunkshop.
//!
//! Implements one source (files), one chunker (sentence_aware), one embedder
//! (fastembed), and one pgvector sink. The YAML config schema and target table
//! shape match the Python reference so vectors are interchangeable across
//! implementations.

pub mod backends;
pub mod bakeoff;
pub mod chunker;
pub mod config;
pub mod embedder;
pub mod extractor;
pub mod framer;
pub(crate) mod hf_cache;
pub mod pipeline;
pub mod runner;
pub mod sentence_split;
pub mod sink;
pub mod source;
pub mod summarizer;

pub use bakeoff::{run_bakeoff, run_bakeoff_with_base, BakeoffConfig, BakeoffResults};
pub use chunker::{Chunk, SentenceAwareChunker};
pub use config::{load_config, CellConfig};
pub use embedder::FastembedEmbedder;
pub use pipeline::Pipeline;
pub use runner::{run_cell, CellResult};
pub use sink::PgVectorSink;
pub use source::{Document, FilesSource};
