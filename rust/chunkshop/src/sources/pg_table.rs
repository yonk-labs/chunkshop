//! Postgres source. Mirrors `python/src/chunkshop/sources/pg_table.py`.
//! Uses PostgresBackend for connection + identifier quoting (v0.4.0 modular shape).
//!
//! RM-B Task 2: adds `IncrementalSource` impl with tuple cursor
//! `(updated_at, id::text)` for boundary-row safety. Mirror of Python commit
//! `ff01268`. Activated by setting `updated_at_column` in the config.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::future::Future;

use crate::backends::base::{BackendConn, BackendDialect};
use crate::backends::postgres::PostgresBackend;
use crate::config::PgTableSourceConfig;
use crate::sources::base::{Document, IncrementalSource};

pub struct PgTableSource {
    cfg: PgTableSourceConfig,
    backend: PostgresBackend,
}

/// Cursor for `PgTableSource::iter_changes_since`.
///
/// Tuple cursor `(after_ts, after_id)` mirrors Python commit `ff01268`. The
/// `(updated_at, id::text)` comparison defends against silent row loss when
/// multiple rows commit at the same boundary timestamp:
///
/// 1. Row `c1@T` arrives → cursor advances to `{after_ts: T, after_id: c1}`.
/// 2. Row `c2@T` arrives (same timestamp).
/// 3. Next sync emits `c2` because `(T, "c2") > (T, "c1")` is true.
///
/// Without the tuple cursor, a single-column `updated_at > T` predicate would
/// skip `c2` forever.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PgTableCursor {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub after_ts: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub after_id: Option<String>,
}

impl PgTableSource {
    pub fn new(cfg: PgTableSourceConfig) -> Self {
        let backend = PostgresBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Column order matches Python's pg_table.py:
        //   [id, content, optional title, *metadata_columns...]
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
                .fq_table(&self.cfg.schema_name, &self.cfg.table)
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

    /// Internal: the cursor-aware SELECT path. Mirrors Python's
    /// `iter_changes_since` exactly. The caller decides whether to dispatch
    /// here or to `iter_documents` based on `updated_at_column` presence.
    async fn iter_changes_since_inner(&self, cursor: &PgTableCursor) -> Result<Vec<Document>> {
        // Caller guarantees `updated_at_column` is Some when calling this.
        let ua_col_name = self
            .cfg
            .updated_at_column
            .as_ref()
            .expect("iter_changes_since_inner called without updated_at_column");
        let id_col = self.backend.quote_ident(&self.cfg.id_column);
        let content_col = self.backend.quote_ident(&self.cfg.content_column);
        let ua_col = self.backend.quote_ident(ua_col_name);

        // Column order: id, content, [title,] updated_at, *metadata_columns
        let mut select = format!("SELECT {id_col}, {content_col}");
        let mut title_idx: Option<usize> = None;
        if let Some(tc) = &self.cfg.title_column {
            title_idx = Some(2);
            select.push_str(&format!(", {}", self.backend.quote_ident(tc)));
        }
        // updated_at always comes next, so the metadata columns start at its
        // index + 1.
        let ua_idx = if title_idx.is_some() { 3 } else { 2 };
        select.push_str(&format!(", {ua_col}"));
        let meta_start = ua_idx + 1;
        for col in &self.cfg.metadata_columns {
            select.push_str(&format!(", {}", self.backend.quote_ident(col)));
        }
        select.push_str(&format!(
            " FROM {fq}",
            fq = self
                .backend
                .fq_table(&self.cfg.schema_name, &self.cfg.table)
        ));

        // Compose WHERE: optional `cfg.where_clause` AND optional tuple cursor.
        let mut where_parts: Vec<String> = Vec::new();
        if let Some(w) = &self.cfg.where_clause {
            where_parts.push(format!("({w})"));
        }
        let have_cursor = cursor.after_ts.is_some() && cursor.after_id.is_some();
        if have_cursor {
            // (updated_at, id::text) > ($1, $2)
            where_parts.push(format!(
                "({ua_col}, {id_col}::text) > ($1::timestamptz, $2)"
            ));
        }
        if !where_parts.is_empty() {
            select.push_str(" WHERE ");
            select.push_str(&where_parts.join(" AND "));
        }
        // Canonical order = (updated_at, id::text) ascending. Same as Python.
        select.push_str(&format!(" ORDER BY {ua_col}, {id_col}::text"));

        self.backend.connect().await?;
        let pool = self.backend.pool().await?;

        let mut q = sqlx::query(&select);
        if have_cursor {
            let ts = cursor.after_ts.as_deref().unwrap();
            let id = cursor.after_id.as_deref().unwrap();
            // Bind timestamp as text + cast in the SQL via $1::timestamptz so
            // we don't need chrono parsing client-side. Postgres parses ISO
            // strings consistently in any sane locale.
            q = q.bind(ts).bind(id);
        }
        let rows = q
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
            let updated_at_iso = read_timestamp_as_iso(&row, ua_idx);
            let mut meta = serde_json::Map::new();
            for (i, col) in self.cfg.metadata_columns.iter().enumerate() {
                let idx = meta_start + i;
                let v = read_meta_value(&row, idx);
                meta.insert(col.clone(), v);
            }
            // Mirror Python: stamp `_updated_at` so `cursor_from` can read it.
            if let Some(iso) = updated_at_iso {
                meta.insert("_updated_at".to_string(), serde_json::Value::String(iso));
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

impl IncrementalSource for PgTableSource {
    type Cursor = PgTableCursor;

    fn empty_cursor(&self) -> Self::Cursor {
        PgTableCursor::default()
    }

    fn iter_changes_since(
        &self,
        cursor: &Self::Cursor,
    ) -> impl Future<Output = Result<Vec<Document>>> + Send {
        let cursor = cursor.clone();
        async move {
            if self.cfg.updated_at_column.is_none() {
                // No cursor column → fall back to full-resync semantics, same
                // as Python's behavior. The cursor argument is ignored.
                return self.iter_documents().await;
            }
            self.iter_changes_since_inner(&cursor).await
        }
    }

    fn cursor_from(&self, last_document: &Document) -> Self::Cursor {
        let after_ts = last_document
            .metadata
            .get("_updated_at")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        PgTableCursor {
            after_ts,
            after_id: Some(last_document.id.clone()),
        }
    }
}

fn read_meta_value(row: &sqlx::postgres::PgRow, idx: usize) -> serde_json::Value {
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
    if let Ok(v) = row.try_get::<Option<Vec<String>>, _>(idx) {
        return v.map(|a| json!(a)).unwrap_or(serde_json::Value::Null);
    }
    serde_json::Value::Null
}

/// Read a Postgres `timestamptz` column and emit ISO-8601 UTC text.
///
/// Format matches Python `datetime.fromisoformat` round-trip-safe output
/// (`2026-05-25T12:00:00+00:00`), which is what consumers persist and what
/// `iter_changes_since` accepts back via `$1::timestamptz`.
fn read_timestamp_as_iso(row: &sqlx::postgres::PgRow, idx: usize) -> Option<String> {
    use sqlx::Row;
    if let Ok(v) =
        row.try_get::<Option<sqlx::types::chrono::DateTime<sqlx::types::chrono::Utc>>, _>(idx)
    {
        return v.map(|dt| dt.to_rfc3339());
    }
    if let Ok(v) = row.try_get::<Option<sqlx::types::chrono::NaiveDateTime>, _>(idx) {
        return v.map(|dt| dt.and_utc().to_rfc3339());
    }
    None
}
