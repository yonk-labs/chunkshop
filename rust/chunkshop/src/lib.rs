//! chunkshop-rs — Rust port of chunkshop.
//!
//! Implements sources (files / HTTP / S3 / DB tables), chunkers, a fastembed
//! embedder, and a modular sink/backend layer (PG / MariaDB / SQLite /
//! ClickHouse). The YAML config schema and target table shape match the
//! Python reference so vectors are interchangeable across implementations.
//!
//! ## Cargo features
//!
//! `default = ["full"]` — preserves backward compatibility with `chunkshop = "0.3"`.
//!
//! Library consumers who want only the chunker structs (e.g. an embedded
//! Postgres extension) can opt into the slim build:
//!
//! ```toml
//! chunkshop = { version = "0.4", default-features = false, features = ["chunkers"] }
//! ```
//!
//! Available features:
//! - `chunkers` — chunker structs + their config types (no fastembed/ort/sqlx).
//! - `embedder-core` — fastembed (BYO `try_new_from_user_defined`) + ORT.
//!   No `hf-hub`, no auto-download. Caller supplies model bytes directly via
//!   [`embedder::FastembedEmbedder::from_user_defined_files`].
//! - `embedder-hub` — adds `hf-hub` for runtime auto-download. Enables
//!   [`embedder::FastembedEmbedder::new`] (stock variants + Xenova int8 BGE
//!   bit-near-exact) and the [`chunker::SemanticChunker::new`] convenience.
//! - `embedder` — historical alias = `embedder-core` + `embedder-hub`.
//!   Existing consumers see no change.
//! - `extractor` — language detection + entity extractor.
//! - `source` — files / HTTP / S3 source loaders.
//! - `sink` — the full modular sink/backend layer (PG/MariaDB/SQLite/ClickHouse).
//! - `pipeline` — high-level Pipeline + run_cell glue.
//! - `bakeoff` — chunker × embedder matrix evaluator.
//! - `full` — all of the above (default).

// The entire modular sink/backend layer is folded under the `sink` feature
// (deliberate v4 design decision — no per-backend features). DB-table sources
// reuse this backend layer, so their fetchers are additionally gated.
#[cfg(feature = "sink")]
pub mod backends;
#[cfg(feature = "bakeoff")]
pub mod bakeoff;
#[cfg(feature = "chunkers")]
pub mod chunker;
pub mod config;
#[cfg(feature = "embedder-core")]
pub mod embedder;
#[cfg(feature = "extractor")]
pub mod extractor;
#[cfg(feature = "pipeline")]
pub mod framer;
// `hf_cache` is the network-fetch path (HuggingFace download via hf-hub).
// Slim consumers on `embedder-core` alone never compile this module.
#[cfg(feature = "embedder-hub")]
pub(crate) mod hf_cache;
#[cfg(feature = "pipeline")]
pub mod pipeline;
#[cfg(feature = "pipeline")]
pub mod runner;
#[cfg(feature = "chunkers")]
pub mod sentence_split;
#[cfg(feature = "sink")]
pub mod sinks;
// `sources` is always declared so the `Document` struct is always available
// (chunkers consume `&Document`). The heavy fetcher impls inside this module
// are themselves cfg-gated behind the `source` (and, for DB-table sources,
// `sink`) features.
pub mod sources;
#[cfg(feature = "chunkers")]
pub mod summarizer;

#[cfg(feature = "sink")]
pub use backends::{
    AnyBackend, Backend, BackendConn, BackendDialect, ClickhouseBackend, ColSpec, MariadbBackend,
    PostgresBackend, SQLiteBackend,
};
#[cfg(feature = "bakeoff")]
pub use bakeoff::{run_bakeoff, run_bakeoff_with_base, BakeoffConfig, BakeoffResults};
#[cfg(feature = "chunkers")]
pub use chunker::{Chunk, SentenceAwareChunker};
pub use config::{load_config, CellConfig};
#[cfg(feature = "embedder-core")]
pub use embedder::FastembedEmbedder;
#[cfg(feature = "pipeline")]
pub use pipeline::Pipeline;
#[cfg(feature = "pipeline")]
pub use runner::{run_cell, CellResult};
#[cfg(feature = "sink")]
pub use sinks::{AnySink, ClickhouseSink, MariadbSink, PgSink, Sink, SqliteSink};
// `Document` is always available; the fetcher sources are gated.
pub use sources::Document;
#[cfg(feature = "source")]
pub use sources::{FilesSource, HttpSource, JsonCorpusSource, S3Source};
#[cfg(all(feature = "source", feature = "sink"))]
pub use sources::{
    AnySource, ClickhouseTableSource, MariadbTableSource, PgTableSource, SqliteTableSource,
};
