//! Backend module — connection management + dialect helpers per DB engine.

use anyhow::Result;

use crate::config::TargetConfig;

pub mod base;
pub mod postgres;
pub mod mariadb;
pub mod sqlite;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;
pub use mariadb::MariadbBackend;
pub use sqlite::SQLiteBackend;

/// Transport sum type — used by the loader to hand a backend to load_sink,
/// where it's pattern-matched back to a concrete type. Sinks store concrete
/// backends (PgSink holds PostgresBackend), not AnyBackend. So this enum does
/// NOT impl Backend / BackendDialect / BackendConn — no match-delegate
/// boilerplate. R4 adds Clickhouse.
pub enum AnyBackend {
    Postgres(PostgresBackend),
    Mariadb(MariadbBackend),
    Sqlite(SQLiteBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
        TargetConfig::Mariadb(t) => Ok(AnyBackend::Mariadb(MariadbBackend::new(t.dsn_env.clone()))),
        TargetConfig::Sqlite(t) => Ok(AnyBackend::Sqlite(SQLiteBackend::new(t.dsn_env.clone()))),
    }
}
