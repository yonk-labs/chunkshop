//! Sources — input document iterators per backing store.

pub mod base;
pub mod files;

pub use base::Document;
pub use files::FilesSource;

// Other source modules + AnySource land in subsequent tasks.
