//! Append-only session staging table. Mirror of Python
//! `chunkshop.memory.staging`. The `event_id` derivation is byte-identical
//! across languages so re-staging a Python-written event from Rust (or vice
//! versa) hits the same row and `ON CONFLICT (event_id) DO NOTHING` cleanly
//! deduplicates.
//!
//! Schema (per Python SP-A spec D3):
//!
//! ```sql
//! event_id    text PRIMARY KEY,
//! session_id  text NOT NULL,
//! seq         bigint,
//! role        text,
//! content     text NOT NULL,
//! tool        text,
//! outcome     text,
//! event_ts    timestamptz,
//! staged_at   timestamptz NOT NULL DEFAULT now(),
//! consumed    jsonb NOT NULL DEFAULT '{}'::jsonb,
//! metadata    jsonb NOT NULL DEFAULT '{}'::jsonb
//! ```
//! Indices: `(session_id, seq)`, `(staged_at)`.

use std::sync::OnceLock;

use anyhow::{anyhow, Result};
use regex::Regex;
use sha1::{Digest, Sha1};
use sqlx::PgPool;

fn ident_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[a-z_][a-z0-9_]*$").unwrap())
}

fn validate_ident(v: &str) -> Result<()> {
    if !ident_re().is_match(v) {
        return Err(anyhow!(
            "identifier must match ^[a-z_][a-z0-9_]*$, got {v:?}"
        ));
    }
    Ok(())
}

/// Deterministic event_id derivation. Byte-identical to Python's
/// `_event_id`: SHA-1 of `session_id\x00disambig\x00content`, where
/// `disambig` is `seq` (stringified) if Some, else `ts` if Some, else `""`.
///
/// Cross-implementation equivalent so a Python-staged event and a
/// Rust-staged event with the same (session_id, seq|ts, content) tuple
/// resolve to the same row.
pub fn derive_event_id(
    session_id: &str,
    seq: Option<i64>,
    ts: Option<&str>,
    content: &str,
) -> String {
    let disambig = match (seq, ts) {
        (Some(s), _) => s.to_string(),
        (None, Some(t)) => t.to_string(),
        (None, None) => String::new(),
    };
    let key = format!("{session_id}\x00{disambig}\x00{content}");
    let mut hasher = Sha1::new();
    hasher.update(key.as_bytes());
    let bytes = hasher.finalize();
    hex_lower(&bytes)
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0x0f) as usize] as char);
    }
    out
}

/// Idempotent DDL: creates schema, table, and the two indices Python's
/// `ensure_staging_table` declares. Safe to call on every run.
pub async fn ensure_staging_table(pool: &PgPool, schema: &str, table: &str) -> Result<()> {
    validate_ident(schema)?;
    validate_ident(table)?;
    let fq = format!("\"{schema}\".\"{table}\"");
    let session_seq_ix = format!("\"{table}_session_seq\"");
    let staged_at_ix = format!("\"{table}_staged_at\"");

    sqlx::query(&format!(r#"CREATE SCHEMA IF NOT EXISTS "{schema}""#))
        .execute(pool)
        .await?;
    sqlx::query(&format!(
        r#"CREATE TABLE IF NOT EXISTS {fq} (
            event_id    text PRIMARY KEY,
            session_id  text NOT NULL,
            seq         bigint,
            role        text,
            content     text NOT NULL,
            tool        text,
            outcome     text,
            event_ts    timestamptz,
            staged_at   timestamptz NOT NULL DEFAULT now(),
            consumed    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            metadata    jsonb NOT NULL DEFAULT '{{}}'::jsonb
        )"#
    ))
    .execute(pool)
    .await?;
    sqlx::query(&format!(
        "CREATE INDEX IF NOT EXISTS {session_seq_ix} ON {fq} (session_id, seq)"
    ))
    .execute(pool)
    .await?;
    sqlx::query(&format!(
        "CREATE INDEX IF NOT EXISTS {staged_at_ix} ON {fq} (staged_at)"
    ))
    .execute(pool)
    .await?;
    Ok(())
}

/// One session event to stage. `event_id` overrides the deterministic
/// derivation when set; leave `None` to let the staging API derive it.
/// `event_ts` is a string (ISO-8601 typically) — preserves Python's
/// f-string stringification behavior in the event_id hash and gets cast
/// to `timestamptz` by Postgres at INSERT.
#[derive(Debug, Clone, Default)]
pub struct StagedEvent {
    pub event_id: Option<String>,
    pub session_id: String,
    pub seq: Option<i64>,
    pub role: Option<String>,
    pub content: String,
    pub tool: Option<String>,
    pub outcome: Option<String>,
    pub event_ts: Option<String>,
    pub metadata: Option<serde_json::Value>,
}

/// Stage one event. Returns the (possibly derived) `event_id`. Idempotent
/// on `event_id` via `ON CONFLICT DO NOTHING` — calling twice with the
/// same canonical event yields a single row.
pub async fn stage_event(
    pool: &PgPool,
    schema: &str,
    table: &str,
    ev: &StagedEvent,
) -> Result<String> {
    validate_ident(schema)?;
    validate_ident(table)?;
    let eid = ev
        .event_id
        .clone()
        .unwrap_or_else(|| derive_event_id(&ev.session_id, ev.seq, ev.event_ts.as_deref(), &ev.content));
    let fq = format!("\"{schema}\".\"{table}\"");
    let metadata_json = ev.metadata.clone().unwrap_or(serde_json::json!({}));
    sqlx::query(&format!(
        r#"INSERT INTO {fq}
           (event_id, session_id, seq, role, content, tool, outcome, event_ts, metadata)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz, $9::jsonb)
           ON CONFLICT (event_id) DO NOTHING"#
    ))
    .bind(&eid)
    .bind(&ev.session_id)
    .bind(ev.seq)
    .bind(ev.role.as_deref())
    .bind(&ev.content)
    .bind(ev.tool.as_deref())
    .bind(ev.outcome.as_deref())
    .bind(ev.event_ts.as_deref())
    .bind(sqlx::types::Json(metadata_json))
    .execute(pool)
    .await?;
    Ok(eid)
}

/// Stage many events in a single transaction. Returns the count of events
/// submitted (NOT necessarily inserted — `ON CONFLICT DO NOTHING` silently
/// skips duplicate `event_id`s, matching Python's contract).
pub async fn stage_events(
    pool: &PgPool,
    schema: &str,
    table: &str,
    events: &[StagedEvent],
) -> Result<usize> {
    validate_ident(schema)?;
    validate_ident(table)?;
    let fq = format!("\"{schema}\".\"{table}\"");
    let mut tx = pool.begin().await?;
    let sql = format!(
        r#"INSERT INTO {fq}
           (event_id, session_id, seq, role, content, tool, outcome, event_ts, metadata)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz, $9::jsonb)
           ON CONFLICT (event_id) DO NOTHING"#
    );
    for ev in events {
        let eid = ev.event_id.clone().unwrap_or_else(|| {
            derive_event_id(&ev.session_id, ev.seq, ev.event_ts.as_deref(), &ev.content)
        });
        let metadata_json = ev.metadata.clone().unwrap_or(serde_json::json!({}));
        sqlx::query(&sql)
            .bind(&eid)
            .bind(&ev.session_id)
            .bind(ev.seq)
            .bind(ev.role.as_deref())
            .bind(&ev.content)
            .bind(ev.tool.as_deref())
            .bind(ev.outcome.as_deref())
            .bind(ev.event_ts.as_deref())
            .bind(sqlx::types::Json(metadata_json))
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(events.len())
}

/// Drop rows older than `older_than` (ISO-8601). When `only_consolidated`
/// is true (the default Python uses), only rows whose `consumed` jsonb
/// contains the `"consolidated"` key are dropped — so an event that has
/// only made it through realtime stays available for the consolidate path.
/// Returns the number of rows deleted.
pub async fn prune_staging(
    pool: &PgPool,
    schema: &str,
    table: &str,
    older_than: &str,
    only_consolidated: bool,
) -> Result<u64> {
    validate_ident(schema)?;
    validate_ident(table)?;
    let fq = format!("\"{schema}\".\"{table}\"");
    let sql = if only_consolidated {
        format!(
            "DELETE FROM {fq} WHERE staged_at < $1::timestamptz AND consumed ? 'consolidated'"
        )
    } else {
        format!("DELETE FROM {fq} WHERE staged_at < $1::timestamptz")
    };
    let res = sqlx::query(&sql).bind(older_than).execute(pool).await?;
    Ok(res.rows_affected())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_id_matches_python_format() {
        // Python:
        //   sha1(f"s1\x00{seq}\x00hi".encode()).hexdigest()
        // Cross-impl invariant: the same (session_id, seq, content)
        // must yield the same id under both implementations.
        let id_with_seq = derive_event_id("s1", Some(1), None, "hi");
        let id_with_ts = derive_event_id("s1", None, Some("2026-01-01"), "hi");
        let id_no_disambig = derive_event_id("s1", None, None, "hi");
        // Different disambigs → different ids.
        assert_ne!(id_with_seq, id_with_ts);
        assert_ne!(id_with_seq, id_no_disambig);
        // sha1 hex = 40 chars.
        assert_eq!(id_with_seq.len(), 40);
        // Deterministic.
        assert_eq!(
            id_with_seq,
            derive_event_id("s1", Some(1), None, "hi"),
        );
        // seq takes precedence over ts when both present (matches Python).
        let id_both = derive_event_id("s1", Some(1), Some("2026-01-01"), "hi");
        assert_eq!(id_both, id_with_seq);
    }

    #[test]
    fn validate_ident_rejects_dangerous_chars() {
        assert!(validate_ident("foo").is_ok());
        assert!(validate_ident("foo_bar").is_ok());
        assert!(validate_ident("Foo").is_err());
        assert!(validate_ident("0bad").is_err());
        assert!(validate_ident("a; DROP TABLE x").is_err());
        assert!(validate_ident("a\"b").is_err());
    }
}
