//! Sources — input document iterators per backing store.

pub mod base;
pub mod files;
pub mod http;
pub mod json_corpus;
pub mod pg_table;
pub mod s3;

pub use base::Document;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;
pub use pg_table::PgTableSource;
pub use s3::S3Source;

// Other source modules + AnySource land in subsequent tasks.
