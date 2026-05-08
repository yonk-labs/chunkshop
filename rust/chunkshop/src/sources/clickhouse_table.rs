//! ClickHouse table source. Mirrors `python/src/chunkshop/sources/clickhouse_table.py`.
//!
//! Streams rows via the official `clickhouse` crate's HTTP transport using
//! `FORMAT JSONEachRow`. Path A (per the R4 plan): the column count is
//! config-driven (`metadata_columns` is `Vec<String>`), so the typed `Row`
//! derive — which expects a fixed shape — can't be used. Instead we pull each
//! row as a JSON object and project into `Document`.
//!
//! Metadata columns are coerced via `toString({col})` in the SELECT so they
//! come back as JSON strings regardless of underlying CH type. This trades
//! Python's recursive `_json_safe` (which preserves nested list/tuple/dict
//! structure) for SQL-side stringification — acceptable for chunkshop's flat
//! metadata model and avoids a second JSON parse pass per row.
//!
//! API confirmed against `clickhouse = "0.15"`: `Query::fetch_bytes(format)`
//! returns `Result<BytesCursor>` (sync) and `BytesCursor::collect(&mut self)`
//! is async, so we bind the cursor to a `mut` local before collecting.

use anyhow::{anyhow, Context, Result};
use serde_json::json;

use crate::backends::base::BackendDialect;
use crate::backends::clickhouse::ClickhouseBackend;
use crate::config::ClickhouseTableSourceConfig;
use crate::sources::base::Document;

pub struct ClickhouseTableSource {
    cfg: ClickhouseTableSourceConfig,
    backend: ClickhouseBackend,
}

impl ClickhouseTableSource {
    pub fn new(cfg: ClickhouseTableSourceConfig) -> Self {
        let backend = ClickhouseBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Column order matches Python's clickhouse_table.py:
        //   [id, content, optional title, *metadata_columns...]
        let mut select_cols: Vec<String> = vec![
            self.backend.quote_ident(&self.cfg.id_column),
            self.backend.quote_ident(&self.cfg.content_column),
        ];
        if let Some(tc) = &self.cfg.title_column {
            select_cols.push(self.backend.quote_ident(tc));
        }
        for col in &self.cfg.metadata_columns {
            // Coerce metadata columns to String for stable JSON transport.
            // CH's toString() works for scalars; nested types stringify as
            // their CH text representation. See module docstring for trade-off.
            let q = self.backend.quote_ident(col);
            select_cols.push(format!("toString({q}) AS {q}"));
        }
        let cols_sql = select_cols.join(", ");
        let fq = self
            .backend
            .fq_table(&self.cfg.database_name, &self.cfg.table);
        let mut q = format!("SELECT {cols_sql} FROM {fq}");
        if let Some(w) = &self.cfg.where_clause {
            q.push_str(&format!(" WHERE {w}"));
        }

        let client = self.backend.client().await?;
        let mut cursor = client
            .query(&q)
            .fetch_bytes("JSONEachRow")
            .with_context(|| format!("running CH source query: {q}"))?;
        let bytes = cursor
            .collect()
            .await
            .with_context(|| format!("collecting CH source response: {q}"))?;
        let body = std::str::from_utf8(&bytes)
            .context("CH JSONEachRow response was not valid UTF-8")?;

        let mut out = Vec::new();
        for line in body.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let row: serde_json::Value = serde_json::from_str(line)
                .with_context(|| format!("parsing JSONEachRow line: {line}"))?;
            let id = row
                .get(&self.cfg.id_column)
                .and_then(|v| match v {
                    serde_json::Value::String(s) => Some(s.clone()),
                    serde_json::Value::Number(n) => Some(n.to_string()),
                    serde_json::Value::Bool(b) => Some(b.to_string()),
                    _ => None,
                })
                .ok_or_else(|| {
                    anyhow!(
                        "id_column {:?} missing or not string/number in row",
                        self.cfg.id_column
                    )
                })?;
            let content = row
                .get(&self.cfg.content_column)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let title = self
                .cfg
                .title_column
                .as_ref()
                .and_then(|tc| row.get(tc).and_then(|v| v.as_str()).map(str::to_string));
            let mut meta = serde_json::Map::new();
            for mc in &self.cfg.metadata_columns {
                meta.insert(mc.clone(), row.get(mc).cloned().unwrap_or(json!(null)));
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
            });
        }
        Ok(out)
    }
}
