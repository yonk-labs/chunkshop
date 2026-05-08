//! chunkshop-rs — Rust port of chunkshop.

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
pub mod sinks;
pub mod sources;
pub mod summarizer;

pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ClickhouseBackend, ColSpec, PostgresBackend};
pub use bakeoff::{run_bakeoff, run_bakeoff_with_base, BakeoffConfig, BakeoffResults};
pub use chunker::{Chunk, SentenceAwareChunker};
pub use config::{load_config, CellConfig};
pub use embedder::FastembedEmbedder;
pub use pipeline::Pipeline;
pub use runner::{run_cell, CellResult};
pub use sinks::{AnySink, ClickhouseSink, PgSink, Sink};
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source};
