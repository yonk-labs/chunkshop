//! Sinks — chunkshop's per-backend data-model semantics layer.

use std::future::Future;

use anyhow::{anyhow, Result};

use crate::backends::AnyBackend;
use crate::chunker::Chunk;
use crate::config::TargetConfig;

pub mod base;
pub mod mariadb;
pub mod pg;
pub mod sqlite;

pub use base::Sink;
pub use mariadb::MariadbSink;
pub use pg::PgSink;
pub use sqlite::SqliteSink;

/// Sum type for runtime polymorphism. Pipeline holds `AnySink` and calls
/// trait methods through the match-delegate impl below.
pub enum AnySink {
    Pg(PgSink),
    Mariadb(MariadbSink),
    Sqlite(SqliteSink),
}

impl Sink for AnySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.create_table().await,
                AnySink::Mariadb(s) => s.create_table().await,
                AnySink::Sqlite(s) => s.create_table().await,
            }
        }
    }

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
                AnySink::Mariadb(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
                AnySink::Sqlite(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
            }
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.delete_document(doc_id).await,
                AnySink::Mariadb(s) => s.delete_document(doc_id).await,
                AnySink::Sqlite(s) => s.delete_document(doc_id).await,
            }
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.count_docs().await,
                AnySink::Mariadb(s) => s.count_docs().await,
                AnySink::Sqlite(s) => s.count_docs().await,
            }
        }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.query_top_k(query_vec, k).await,
                AnySink::Mariadb(s) => s.query_top_k(query_vec, k).await,
                AnySink::Sqlite(s) => s.query_top_k(query_vec, k).await,
            }
        }
    }
}

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) => {
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim)))
        }
        (TargetConfig::Mariadb(t), AnyBackend::Mariadb(b)) => {
            Ok(AnySink::Mariadb(MariadbSink::new(t.clone(), b, dim)))
        }
        (TargetConfig::Sqlite(t), AnyBackend::Sqlite(b)) => {
            Ok(AnySink::Sqlite(SqliteSink::new(t.clone(), b, dim)))
        }
        // Cross-variant mismatches are programming errors (load_backend +
        // load_sink are always called paired with the same TargetConfig).
        #[allow(unreachable_patterns)]
        _ => Err(anyhow!("backend / target type mismatch — programming error in load_sink dispatch")),
    }
}
