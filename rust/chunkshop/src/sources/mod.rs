//! Sources — input document iterators per backing store.
//!
//! The `Document` struct (in `base`) is always available — chunkers consume
//! `&Document` regardless of which features are enabled. The fetcher impls are
//! gated:
//! - file / HTTP / S3 / json_corpus fetchers require the `source` feature.
//! - DB-table sources (PG / MariaDB / SQLite / ClickHouse) additionally reuse
//!   the backend connection layer, so they require `all(source, sink)`.

// `Document` is always available.
pub mod base;
pub use base::Document;

#[cfg(all(feature = "source", feature = "sink"))]
use anyhow::anyhow;
#[cfg(feature = "source")]
use anyhow::Result;

#[cfg(all(feature = "source", feature = "sink"))]
use crate::config::SourceConfig;

#[cfg(feature = "source")]
pub mod files;
#[cfg(feature = "source")]
pub mod http;
#[cfg(feature = "source")]
pub mod json_corpus;
#[cfg(feature = "source")]
pub mod s3;

#[cfg(feature = "source")]
pub use files::FilesSource;
#[cfg(feature = "source")]
pub use http::HttpSource;
#[cfg(feature = "source")]
pub use json_corpus::JsonCorpusSource;
#[cfg(feature = "source")]
pub use s3::S3Source;

// DB-table sources reuse `crate::backends::*`, which is `sink`-gated.
#[cfg(all(feature = "source", feature = "sink"))]
pub mod clickhouse_table;
#[cfg(all(feature = "source", feature = "sink"))]
pub mod mariadb_table;
#[cfg(all(feature = "source", feature = "sink"))]
pub mod pg_table;
#[cfg(all(feature = "source", feature = "sink"))]
pub mod sqlite_table;

#[cfg(all(feature = "source", feature = "sink"))]
pub use clickhouse_table::ClickhouseTableSource;
#[cfg(all(feature = "source", feature = "sink"))]
pub use mariadb_table::MariadbTableSource;
#[cfg(all(feature = "source", feature = "sink"))]
pub use pg_table::PgTableSource;
#[cfg(all(feature = "source", feature = "sink"))]
pub use sqlite_table::SqliteTableSource;

/// Sum type for runtime polymorphism. R1 shipped the original 5 sources.
/// R2/R3/R4 added MariadbTable, SqliteTable, ClickhouseTable respectively.
///
/// `AnySource` enumerates DB-table variants, which require the backend layer.
/// It is therefore only built when both `source` and `sink` are enabled —
/// the same gate that `run_cell` / `Pipeline` live behind.
#[cfg(all(feature = "source", feature = "sink"))]
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    MariadbTable(MariadbTableSource),
    SqliteTable(SqliteTableSource),
    Http(HttpSource),
    S3(S3Source),
    ClickhouseTable(ClickhouseTableSource),
}

#[cfg(all(feature = "source", feature = "sink"))]
impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::MariadbTable(s) => s.iter_documents().await,
            AnySource::SqliteTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
            AnySource::ClickhouseTable(s) => s.iter_documents().await,
        }
    }
}

#[cfg(all(feature = "source", feature = "sink"))]
pub fn load_source(cfg: &SourceConfig) -> Result<AnySource> {
    match cfg {
        SourceConfig::Files(c) => Ok(AnySource::Files(FilesSource::new(c.clone()))),
        SourceConfig::JsonCorpus(c) => Ok(AnySource::JsonCorpus(JsonCorpusSource::new(c.clone()))),
        SourceConfig::PgTable(c) => Ok(AnySource::PgTable(PgTableSource::new(c.clone()))),
        SourceConfig::MariadbTable(c) => {
            Ok(AnySource::MariadbTable(MariadbTableSource::new(c.clone())))
        }
        SourceConfig::SqliteTable(c) => {
            Ok(AnySource::SqliteTable(SqliteTableSource::new(c.clone())))
        }
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::ClickhouseTable(c) => Ok(AnySource::ClickhouseTable(
            ClickhouseTableSource::new(c.clone()),
        )),
        SourceConfig::SessionStaging(_) => Err(anyhow!(
            "session_staging source not yet implemented in this build \
             (RM-A Task 5; tracking: chunkshop#9)"
        )),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
