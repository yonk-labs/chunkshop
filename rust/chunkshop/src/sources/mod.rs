//! Sources — input document iterators per backing store.

use anyhow::{anyhow, Result};

use crate::config::SourceConfig;

pub mod base;
pub mod files;
pub mod http;
pub mod json_corpus;
pub mod mariadb_table;
pub mod pg_table;
pub mod s3;

pub use base::Document;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;
pub use mariadb_table::MariadbTableSource;
pub use pg_table::PgTableSource;
pub use s3::S3Source;

/// Sum type for runtime polymorphism. R2 adds MariadbTable. R3/R4 add
/// SqliteTable. ClickhouseTable is deferred to v4.1.
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    MariadbTable(MariadbTableSource),
    Http(HttpSource),
    S3(S3Source),
}

impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::MariadbTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
        }
    }
}

pub fn load_source(cfg: &SourceConfig) -> Result<AnySource> {
    match cfg {
        SourceConfig::Files(c) => Ok(AnySource::Files(FilesSource::new(c.clone()))),
        SourceConfig::JsonCorpus(c) => Ok(AnySource::JsonCorpus(JsonCorpusSource::new(c.clone()))),
        SourceConfig::PgTable(c) => Ok(AnySource::PgTable(PgTableSource::new(c.clone()))),
        SourceConfig::MariadbTable(c) => Ok(AnySource::MariadbTable(MariadbTableSource::new(c.clone()))),
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
