//! Source implementations (legacy monolith — being split into `sources/` in Phase E).
//! All concrete sources now live in `sources/`. This file is a thin re-export shim
//! during the R1 transition and is removed in Phase G (Task 27).

pub use crate::sources::base::Document;
pub use crate::sources::files::FilesSource;
pub use crate::sources::http::HttpSource;
pub use crate::sources::json_corpus::JsonCorpusSource;
pub use crate::sources::pg_table::PgTableSource;
pub use crate::sources::s3::S3Source;
