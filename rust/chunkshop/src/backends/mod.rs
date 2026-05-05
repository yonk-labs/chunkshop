//! Backend module — connection management + dialect helpers per DB engine.
//!
//! AnyBackend is a TRANSPORT sum type — used by the loader to hand a backend
//! to load_sink, where it's pattern-matched back to a concrete type. Sinks
//! store concrete backends (PgSink holds PostgresBackend), not AnyBackend.
//! So this enum does NOT impl Backend / BackendDialect / BackendConn — no
//! match-delegate boilerplate.

pub mod base;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};

// load_backend factory + AnyBackend enum land in Phase F (Task 22) once
// PostgresBackend is implemented.
