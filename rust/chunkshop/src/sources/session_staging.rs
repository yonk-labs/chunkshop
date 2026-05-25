//! RM-A Task 5: SessionStagingSource — reads the chunkshop-owned staging
//! table and yields one `Document` per session, with mode-specific
//! semantics.
//!
//! - **realtime** mode: row-level WHERE selects rows whose `consumed.realtime`
//!   watermark is unset or older than `staged_at`. Pulls only new events.
//!   After yielding, advances `consumed.realtime` for the emitted sessions.
//!   Provisional-tier writes go through this path.
//!
//! - **consolidate** mode: **session-level WHERE** — a session is eligible
//!   when its `max(coalesce(event_ts, staged_at)) < now() - min_age_seconds`
//!   AND at least one of its rows has either no `consumed.consolidated`
//!   watermark or a newer `staged_at` than the recorded watermark. When
//!   eligible, **all** of the session's rows are emitted (full-staging
//!   rebuild), so a late event after consolidation rebuilds the session's
//!   memory instead of erasing it. This is **non-negotiable** — Python's
//!   row-level original (now fixed in 49861dc) was a data-loss bug.
//!
//! Mirror of Python `chunkshop.sources.session_staging.SessionStagingSource`.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use anyhow::{anyhow, Context, Result};
use serde_json::Value;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Row};
use tokio::sync::OnceCell;

use crate::config::{SessionStagingMode, SessionStagingSourceConfig};
use crate::sources::base::Document;

fn ident_re() -> &'static regex::Regex {
    static R: OnceLock<regex::Regex> = OnceLock::new();
    R.get_or_init(|| regex::Regex::new(r"^[a-z_][a-z0-9_]*$").unwrap())
}

fn validate_ident(v: &str) -> Result<()> {
    if !ident_re().is_match(v) {
        return Err(anyhow!(
            "identifier must match ^[a-z_][a-z0-9_]*$, got {v:?}"
        ));
    }
    Ok(())
}

/// Resolve a DSN string from either the literal `dsn` field or the
/// `dsn_env` env-var-name field. Matches Python's precedence (literal wins
/// when both set).
fn resolve_dsn(cfg: &SessionStagingSourceConfig) -> Result<String> {
    if let Some(d) = &cfg.dsn {
        // Match Python's `${VAR}` expansion: not implemented here for v1
        // (Rust callers wire env vars at startup). If the literal contains
        // `${...}`, fall through to dsn_env path.
        if !d.contains("${") {
            return Ok(d.clone());
        }
    }
    if let Some(env) = &cfg.dsn_env {
        return std::env::var(env).with_context(|| format!("DSN env var {env} not set"));
    }
    Err(anyhow!(
        "session_staging source needs either `dsn` (literal) or `dsn_env` (env var name)"
    ))
}

pub struct SessionStagingSource {
    cfg: SessionStagingSourceConfig,
    pool: OnceCell<PgPool>,
    /// O3 crash-safety: iter_documents() stores (watermark_iso, key,
    /// emitted_sessions) here; the watermark UPDATE is deferred to an
    /// explicit `commit_processed()` call the runner makes AFTER the
    /// per-doc write loop succeeds. If the loop errors mid-iteration,
    /// commit_processed() never runs and the next run reselects everything.
    /// Mirror of Python's generator semantics (the UPDATE runs after all
    /// yields complete, never reached on a mid-yield crash).
    pending_watermark: Mutex<Option<(String, &'static str, Vec<String>)>>,
}

impl SessionStagingSource {
    pub fn new(cfg: SessionStagingSourceConfig) -> Self {
        Self {
            cfg,
            pool: OnceCell::new(),
            pending_watermark: Mutex::new(None),
        }
    }

    /// O3: advance the per-session watermark for sessions emitted by the
    /// most recent `iter_documents()` call. The runner MUST call this
    /// after the per-doc write loop succeeds. If it isn't called (mid-loop
    /// crash or early return), the watermark stays unadvanced and the
    /// next run reselects the same sessions — same crash-safety the
    /// Python generator semantics provide.
    pub async fn commit_processed(&self) -> Result<()> {
        let pending = self.pending_watermark.lock().unwrap().take();
        let Some((watermark, wm_key, sessions)) = pending else {
            return Ok(());
        };
        if sessions.is_empty() {
            return Ok(());
        }
        let fq = format!(
            "\"{}\".\"{}\"",
            self.cfg.staging_schema, self.cfg.staging_table
        );
        let update_sql = format!(
            "UPDATE {fq} \
             SET consumed = consumed || jsonb_build_object($1::text, $2::text) \
             WHERE session_id = ANY($3)"
        );
        sqlx::query(&update_sql)
            .bind(wm_key)
            .bind(&watermark)
            .bind(&sessions)
            .execute(self.pool().await?)
            .await?;
        Ok(())
    }

    async fn pool(&self) -> Result<&PgPool> {
        self.pool
            .get_or_try_init(|| async {
                let dsn = resolve_dsn(&self.cfg)?;
                PgPoolOptions::new()
                    .max_connections(2)
                    .connect(&dsn)
                    .await
                    .with_context(|| format!("connecting session_staging pool"))
            })
            .await
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        validate_ident(&self.cfg.staging_schema)?;
        validate_ident(&self.cfg.staging_table)?;
        let fq = format!(
            "\"{}\".\"{}\"",
            self.cfg.staging_schema, self.cfg.staging_table
        );

        let (where_clause, wm_key) = match self.cfg.mode {
            SessionStagingMode::Realtime => (
                // Row-level: any row whose realtime watermark is unset or older
                // than staged_at.
                "WHERE coalesce(consumed->>'realtime','') = '' \
                 OR staged_at > (consumed->>'realtime')::timestamptz"
                    .to_string(),
                "realtime",
            ),
            SessionStagingMode::Consolidate => {
                // O1: SESSION-LEVEL eligibility. A late event for an
                // already-consolidated session must re-emit the FULL session
                // (all its events) so MemorySink's destructive supersede
                // rebuilds rather than fragments the consolidated memory.
                // Mirror of Python's session-level WHERE (49861dc fix).
                let n = self.cfg.min_age_seconds as i64;
                (
                    format!(
                        "WHERE session_id IN (\
                           SELECT session_id FROM {fq} \
                           GROUP BY session_id \
                           HAVING max(coalesce(event_ts, staged_at)) < now() - make_interval(secs => {n}) \
                             AND (bool_or(coalesce(consumed->>'consolidated','') = '') \
                                  OR max(staged_at) > min(nullif(consumed->>'consolidated','')::timestamptz)) \
                         )"
                    ),
                    "consolidated",
                )
            }
        };

        let select_sql = format!(
            "SELECT event_id, session_id, seq, role, content, tool, outcome, \
                    extract(epoch FROM coalesce(event_ts, staged_at))::double precision AS ts \
             FROM {fq} {where_clause} \
             ORDER BY session_id, seq NULLS LAST"
        );

        let pool = self.pool().await?;
        // Acquire the watermark BEFORE the data SELECT to minimize the
        // concurrent-insert window — same pattern Python uses. Bind as
        // ISO-8601 text (matches Python's `.isoformat()` storage); the
        // UPDATE casts it back via `$2::text` into the consumed jsonb.
        let watermark: String = sqlx::query_scalar(
            "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')",
        )
        .fetch_one(pool)
        .await?;
        let rows = sqlx::query(&select_sql).fetch_all(pool).await?;

        // Group rows by session_id (input order preserved by ORDER BY).
        let mut by_session: HashMap<String, Vec<Value>> = HashMap::new();
        let mut session_order: Vec<String> = Vec::new();
        for r in &rows {
            let sid: String = r.try_get("session_id")?;
            let event_id: String = r.try_get("event_id")?;
            let seq: Option<i64> = r.try_get("seq")?;
            let role: Option<String> = r.try_get("role")?;
            let content: String = r.try_get("content")?;
            let tool: Option<String> = r.try_get("tool")?;
            let outcome: Option<String> = r.try_get("outcome")?;
            let ts: f64 = r.try_get("ts")?;
            let mut ev = serde_json::Map::new();
            ev.insert("event_id".into(), Value::String(event_id));
            ev.insert("seq".into(), seq.map(Value::from).unwrap_or(Value::Null));
            ev.insert(
                "role".into(),
                role.map(Value::String).unwrap_or(Value::Null),
            );
            ev.insert("content".into(), Value::String(content));
            ev.insert(
                "tool".into(),
                tool.map(Value::String).unwrap_or(Value::Null),
            );
            ev.insert(
                "outcome".into(),
                outcome.map(Value::String).unwrap_or(Value::Null),
            );
            ev.insert("ts".into(), Value::from(ts));
            if !by_session.contains_key(&sid) {
                session_order.push(sid.clone());
            }
            by_session.entry(sid).or_default().push(Value::Object(ev));
        }

        let max_sessions = self.cfg.max_sessions.unwrap_or(usize::MAX);
        let mode_str = match self.cfg.mode {
            SessionStagingMode::Realtime => "realtime",
            SessionStagingMode::Consolidate => "consolidate",
        };

        let mut docs = Vec::new();
        let mut emitted_sessions: Vec<String> = Vec::new();
        for sid in session_order.into_iter().take(max_sessions) {
            let evs = by_session.remove(&sid).unwrap_or_default();
            // Reconstruct session text: `[role/tool] content` lines, in seq
            // order (already sorted by SELECT).
            let mut lines = Vec::with_capacity(evs.len());
            let mut first_ts = Value::Null;
            let mut last_ts = Value::Null;
            for e in &evs {
                let role = e.get("role").and_then(|v| v.as_str()).unwrap_or("event");
                let tool = e.get("tool").and_then(|v| v.as_str());
                let tag = match tool {
                    Some(t) => format!("{role}/{t}"),
                    None => role.to_string(),
                };
                let content = e.get("content").and_then(|v| v.as_str()).unwrap_or("");
                lines.push(format!("[{tag}] {content}"));
            }
            if let Some(first) = evs.first() {
                first_ts = first.get("ts").cloned().unwrap_or(Value::Null);
            }
            if let Some(last) = evs.last() {
                last_ts = last.get("ts").cloned().unwrap_or(Value::Null);
            }
            let mut meta = serde_json::Map::new();
            meta.insert("session_id".into(), Value::String(sid.clone()));
            meta.insert("namespace".into(), Value::Null);
            meta.insert("event_count".into(), Value::from(evs.len() as u64));
            meta.insert("first_ts".into(), first_ts);
            meta.insert("last_ts".into(), last_ts);
            meta.insert("mode".into(), Value::String(mode_str.into()));
            meta.insert("_session_events".into(), Value::Array(evs));
            docs.push(Document {
                id: sid.clone(),
                content: lines.join("\n"),
                title: None,
                metadata: Value::Object(meta),
            });
            emitted_sessions.push(sid);
        }

        // O3: don't advance the watermark yet — store it and let the
        // runner trigger the UPDATE after the per-doc write loop succeeds.
        // Mid-iteration crash → commit_processed() never runs → next run
        // reselects these sessions. Matches Python generator semantics.
        *self.pending_watermark.lock().unwrap() = Some((watermark, wm_key, emitted_sessions));

        Ok(docs)
    }
}
