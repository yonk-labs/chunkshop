//! Sinks — chunkshop's per-backend data-model semantics layer.

pub mod base;
pub mod pg;

pub use base::Sink;
pub use pg::PgSink;

// AnySink + load_sink factory land in Phase F (Task 23).
