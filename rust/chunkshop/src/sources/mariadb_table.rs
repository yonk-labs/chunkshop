//! MariaDB source. Mirrors `python/src/chunkshop/sources/mariadb_table.py`.

use anyhow::{Context, Result};
use serde_json::json;

use crate::backends::base::{BackendConn, BackendDialect};
use crate::backends::mariadb::MariadbBackend;
use crate::config::MariadbTableSourceConfig;
use crate::sources::base::Document;

pub struct MariadbTableSource {
    cfg: MariadbTableSourceConfig,
    backend: MariadbBackend,
}

impl MariadbTableSource {
    pub fn new(cfg: MariadbTableSourceConfig) -> Self {
        let backend = MariadbBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut select = format!(
            "SELECT {id_col}, {content_col}",
            id_col = self.backend.quote_ident(&self.cfg.id_column),
            content_col = self.backend.quote_ident(&self.cfg.content_column),
        );
        let mut title_idx: Option<usize> = None;
        if let Some(tc) = &self.cfg.title_column {
            title_idx = Some(2);
            select.push_str(&format!(", {}", self.backend.quote_ident(tc)));
        }
        let meta_start = if title_idx.is_some() { 3 } else { 2 };
        for col in &self.cfg.metadata_columns {
            select.push_str(&format!(", {}", self.backend.quote_ident(col)));
        }
        select.push_str(&format!(
            " FROM {fq}",
            fq = self
                .backend
                .fq_table(&self.cfg.database_name, &self.cfg.table)
        ));
        if let Some(w) = &self.cfg.where_clause {
            select.push_str(&format!(" WHERE {w}"));
        }

        self.backend.connect().await?;
        let pool = self.backend.pool().await?;
        let rows = sqlx::query(&select)
            .fetch_all(pool)
            .await
            .with_context(|| format!("running query: {select}"))?;

        let mut out = Vec::with_capacity(rows.len());
        for row in rows {
            use sqlx::Row;
            let id: String = row
                .try_get::<String, _>(0)
                .or_else(|_| row.try_get::<i64, _>(0).map(|n| n.to_string()))
                .or_else(|_| row.try_get::<i32, _>(0).map(|n| n.to_string()))
                .with_context(|| "reading id column from row".to_string())?;
            let content: String = row.try_get(1).context("reading content column")?;
            let title: Option<String> = match title_idx {
                Some(i) => row.try_get::<Option<String>, _>(i).unwrap_or(None),
                None => None,
            };
            let mut meta = serde_json::Map::new();
            for (i, col) in self.cfg.metadata_columns.iter().enumerate() {
                let idx = meta_start + i;
                let v = read_meta_value(&row, idx);
                meta.insert(col.clone(), v);
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
                fingerprint: None,
            });
        }
        Ok(out)
    }
}

fn read_meta_value(row: &sqlx::mysql::MySqlRow, idx: usize) -> serde_json::Value {
    use sqlx::Row;
    if let Ok(v) = row.try_get::<Option<String>, _>(idx) {
        return v
            .map(serde_json::Value::String)
            .unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i32>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<f64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<bool>, _>(idx) {
        return v.map(|b| json!(b)).unwrap_or(serde_json::Value::Null);
    }
    serde_json::Value::Null
}
