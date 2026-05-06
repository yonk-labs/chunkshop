//! Sources — input document iterators per backing store.

pub mod base;
pub mod files;
pub mod http;
pub mod json_corpus;

pub use base::Document;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;

// Other source modules + AnySource land in subsequent tasks.
