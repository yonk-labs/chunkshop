//! SQLite source. Mirrors `python/src/chunkshop/sources/sqlite_table.py`.

use anyhow::{Context, Result};
use serde_json::json;

use crate::backends::base::BackendDialect;
use crate::backends::sqlite::SQLiteBackend;
use crate::config::SqliteTableSourceConfig;
use crate::sources::base::Document;

#[derive(Clone)]
pub struct SqliteTableSource {
    pub(crate) cfg: SqliteTableSourceConfig,
    pub(crate) backend: SQLiteBackend,
}

impl SqliteTableSource {
    pub fn new(cfg: SqliteTableSourceConfig) -> Self {
        let backend = SQLiteBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Build column list: [id, content, optional title, *metadata...]
        let mut cols: Vec<&str> = vec![&self.cfg.id_column, &self.cfg.content_column];
        let title_idx: Option<usize> = if let Some(tc) = &self.cfg.title_column {
            cols.push(tc);
            Some(2)
        } else {
            None
        };
        let meta_start = if title_idx.is_some() { 3 } else { 2 };
        for col in &self.cfg.metadata_columns {
            cols.push(col);
        }

        let cols_sql: String = cols
            .iter()
            .map(|c| self.backend.quote_ident(c))
            .collect::<Vec<_>>()
            .join(", ");
        let fq = self
            .backend
            .fq_table(&self.cfg.database_name, &self.cfg.table);
        let mut select = format!("SELECT {cols_sql} FROM {fq}");
        if let Some(w) = &self.cfg.where_clause {
            select.push_str(&format!(" WHERE {w}"));
        }

        let conn = self.backend.connect().await?;
        let metadata_columns = self.cfg.metadata_columns.clone();
        tokio::task::spawn_blocking(move || -> Result<Vec<Document>> {
            let g = conn.blocking_lock();
            let mut q = g.prepare(&select).context("prepare source query")?;
            let n_cols = q.column_count();
            let rows = q
                .query_map([], |r| {
                    let id_v: rusqlite::types::Value = r.get(0)?;
                    let id = match id_v {
                        rusqlite::types::Value::Integer(i) => i.to_string(),
                        rusqlite::types::Value::Real(f) => f.to_string(),
                        rusqlite::types::Value::Text(s) => s,
                        rusqlite::types::Value::Null => String::new(),
                        rusqlite::types::Value::Blob(_) => "<blob>".to_string(),
                    };
                    let content: String = r.get(1)?;
                    let title: Option<String> = match title_idx {
                        Some(i) => r.get::<_, Option<String>>(i).ok().flatten(),
                        None => None,
                    };
                    let mut meta = serde_json::Map::new();
                    for (i, col) in metadata_columns.iter().enumerate() {
                        let idx = meta_start + i;
                        if idx >= n_cols {
                            break;
                        }
                        let v: rusqlite::types::Value = r.get(idx)?;
                        let jv = match v {
                            rusqlite::types::Value::Null => serde_json::Value::Null,
                            rusqlite::types::Value::Integer(i) => json!(i),
                            rusqlite::types::Value::Real(f) => json!(f),
                            rusqlite::types::Value::Text(s) => serde_json::Value::String(s),
                            rusqlite::types::Value::Blob(_) => serde_json::Value::Null,
                        };
                        meta.insert(col.clone(), jv);
                    }
                    Ok(Document {
                        id,
                        content,
                        title,
                        metadata: serde_json::Value::Object(meta),
                        fingerprint: None,
                    })
                })
                .context("query source rows")?;
            let out: rusqlite::Result<Vec<Document>> = rows.collect();
            Ok(out.context("collect source rows")?)
        })
        .await
        .context("spawn_blocking iter_documents")?
    }
}
