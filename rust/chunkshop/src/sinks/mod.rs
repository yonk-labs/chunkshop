//! Sinks — chunkshop's per-backend data-model semantics layer.

pub mod base;

pub use base::Sink;

// AnySink + load_sink factory land in Phase F (Task 23) once PgSink is implemented.
