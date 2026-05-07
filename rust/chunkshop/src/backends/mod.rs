//! Backend module — connection management + dialect helpers per DB engine.

use anyhow::Result;

use crate::config::TargetConfig;

pub mod base;
pub mod postgres;
pub mod mariadb;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;
pub use mariadb::MariadbBackend;

/// Transport sum type — used by the loader to hand a backend to load_sink,
/// where it's pattern-matched back to a concrete type. Sinks store concrete
/// backends (PgSink holds PostgresBackend), not AnyBackend. So this enum does
/// NOT impl Backend / BackendDialect / BackendConn — no match-delegate
/// boilerplate. R2/R3/R4 add new variants.
pub enum AnyBackend {
    Postgres(PostgresBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
    }
}
