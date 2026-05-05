//! Backend module — connection management + dialect helpers per DB engine.

pub mod base;
pub mod postgres;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;

// AnyBackend + load_backend factory land in Phase F (Task 22).
