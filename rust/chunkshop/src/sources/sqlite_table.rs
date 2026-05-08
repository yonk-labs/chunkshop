//! SQLite source (placeholder — Task 14 fills in real iter_documents).

use anyhow::{anyhow, Result};
use crate::config::SqliteTableSourceConfig;
use crate::sources::base::Document;

#[derive(Clone)]
pub struct SqliteTableSource {
    pub(crate) cfg: SqliteTableSourceConfig,
}

impl SqliteTableSource {
    pub fn new(cfg: SqliteTableSourceConfig) -> Self { Self { cfg } }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        Err(anyhow!("SqliteTableSource::iter_documents not yet implemented"))
    }
}
