//! RM-A Task 9: MemorySink — extends PgSink with memory-cell stamping +
//! namespace-qualified row-id. Mirror of Python `chunkshop.sinks.memory_pg`.
//!
//! Task 10 will add supersede (consolidated → delete prior provisional)
//! and soft-invalidate (newer contradicting fact retracts older). Task 9
//! is just the DDL + stamping piece.

use std::future::Future;
use std::sync::Mutex;

use anyhow::Result;
use serde_json::Value;

use crate::backends::postgres::PostgresBackend;
use crate::chunker::Chunk;
use crate::config::{MemoryConfig, MemoryTier, PostgresTargetConfig};
use crate::sinks::base::Sink;
use crate::sinks::pg::PgSink;

pub struct MemorySink {
    inner: PgSink,
    mem: MemoryConfig,
    /// ISO-8601 UTC `recorded_at` stamped on every chunk this sink writes.
    /// Fixed at construction so all chunks of a single cell run share a
    /// `recorded_at` value (matches Python).
    recorded_at: String,
    /// Resolved namespace — falls back to `source_tag` when `mem.namespace`
    /// is None, matching Python's MemorySink behavior.
    namespace: String,
    /// Per-run set of doc_ids already superseded — prevents a second
    /// supersede DELETE for the same doc on a subsequent write_document
    /// call within the same cell run. Mirror of Python `self._superseded`.
    superseded: Mutex<std::collections::HashSet<String>>,
}

impl MemorySink {
    pub fn new(cfg: PostgresTargetConfig, backend: PostgresBackend, embed_dim: usize) -> Self {
        let mem = cfg
            .memory
            .clone()
            .expect("MemorySink requires PostgresTargetConfig.memory to be set");
        let namespace = mem
            .namespace
            .clone()
            .or_else(|| cfg.source_tag.clone())
            .unwrap_or_else(|| "default".to_string());
        let recorded_at = now_iso();
        let inner = PgSink::new(cfg, backend, embed_dim).with_id_namespace(namespace.clone());
        Self {
            inner,
            mem,
            recorded_at,
            namespace,
            superseded: Mutex::new(std::collections::HashSet::new()),
        }
    }

    /// Stamp memory-cell metadata onto a chunk. Mirror of Python
    /// `MemorySink._stamp` — kind/tier/namespace/recorded_at; effective_from
    /// inferred from episode_end_ts (epoch f64 → ISO string) when present.
    /// Underscore-prefixed metadata keys (e.g. `_episode_events`) are stripped
    /// before insert because they don't belong in the persisted jsonb.
    fn stamp(&self, c: &Chunk) -> Chunk {
        let mut m = c
            .metadata
            .as_object()
            .cloned()
            .unwrap_or_default();
        // Strip underscore-prefixed keys (transient per-stage data).
        m.retain(|k, _| !k.starts_with('_'));
        // Default kind=episode for back-compat with chunkers that don't stamp it.
        m.entry("kind").or_insert(Value::String("episode".into()));
        m.entry("retracted").or_insert(Value::Bool(false));
        m.insert("tier".into(), Value::String(self.tier_str().into()));
        m.insert("namespace".into(), Value::String(self.namespace.clone()));
        m.insert("recorded_at".into(), Value::String(self.recorded_at.clone()));
        // effective_from <- ISO of episode_end_ts when present.
        if !m.contains_key("effective_from") {
            if let Some(epoch) = m.get("episode_end_ts").and_then(|v| v.as_f64()) {
                m.insert("effective_from".into(), Value::String(iso_from_epoch(epoch)));
            }
        }
        Chunk {
            doc_id: c.doc_id.clone(),
            seq_num: c.seq_num,
            original_content: c.original_content.clone(),
            embedded_content: c.embedded_content.clone(),
            metadata: Value::Object(m),
        }
    }

    fn tier_str(&self) -> &'static str {
        match self.mem.tier {
            MemoryTier::Provisional => "provisional",
            MemoryTier::Consolidated => "consolidated",
        }
    }

    pub fn namespace(&self) -> &str {
        &self.namespace
    }
    pub fn recorded_at(&self) -> &str {
        &self.recorded_at
    }
}

impl Sink for MemorySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move { self.inner.create_table().await }
    }

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            let stamped: Vec<Chunk> = chunks.iter().map(|c| self.stamp(c)).collect();

            // Supersede: for consolidated-tier writes, DELETE prior rows for
            // this doc_id scoped by source_tag (so cross-namespace memory
            // for the same session is left alone). Once per doc_id per run.
            // Mirror of Python `MemorySink.write_document`'s supersede branch.
            if self.mem.supersede && matches!(self.mem.tier, MemoryTier::Consolidated) {
                let already = {
                    let mut s = self.superseded.lock().unwrap();
                    if s.contains(doc_id) {
                        true
                    } else {
                        s.insert(doc_id.to_string());
                        false
                    }
                };
                if !already {
                    let fq = self.inner.fq_pub();
                    let source = self.inner.source_tag().unwrap_or("default");
                    sqlx::query(&format!(
                        "DELETE FROM {fq} WHERE doc_id = $1 AND source = $2"
                    ))
                    .bind(doc_id)
                    .bind(source)
                    .execute(self.inner.pool().await?)
                    .await?;
                }
            }

            // The actual insert.
            self.inner
                .write_document(doc_id, &stamped, embeddings, tags_per_chunk)
                .await?;

            // Soft-invalidate: for each fact chunk with a populated SPO
            // triple AND effective_from, retract prior same-(subject,predicate)
            // facts whose effective_from is older AND that are not already
            // retracted. Scoped by source_tag — cross-namespace facts are
            // independent. Explicit ::timestamptz casts so the ISO-string
            // params line up with the column type unambiguously.
            let source = self.inner.source_tag().unwrap_or("default");
            let fq = self.inner.fq_pub();
            for c in stamped.iter() {
                let meta = match c.metadata.as_object() {
                    Some(m) => m,
                    None => continue,
                };
                if meta.get("kind").and_then(|v| v.as_str()) != Some("fact") {
                    continue;
                }
                let subject = match meta.get("subject").and_then(|v| v.as_str()) {
                    Some(s) if !s.is_empty() => s,
                    _ => continue,                            // sparse triple → no-op
                };
                let predicate = match meta.get("predicate").and_then(|v| v.as_str()) {
                    Some(s) if !s.is_empty() => s,
                    _ => continue,
                };
                let effective_from = match meta.get("effective_from").and_then(|v| v.as_str()) {
                    Some(s) if !s.is_empty() => s,
                    _ => continue,                            // need timestamp to compare
                };
                sqlx::query(&format!(
                    "UPDATE {fq} \
                     SET retracted = true, retracted_at = now(), \
                         effective_to = $1::timestamptz \
                     WHERE source = $2 AND subject = $3 AND predicate = $4 \
                       AND effective_from < $5::timestamptz \
                       AND coalesce(retracted, false) = false"
                ))
                .bind(effective_from)
                .bind(source)
                .bind(subject)
                .bind(predicate)
                .bind(effective_from)
                .execute(self.inner.pool().await?)
                .await?;
            }

            Ok(())
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { self.inner.delete_document(doc_id).await }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { self.inner.count_docs().await }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { self.inner.query_top_k(query_vec, k).await }
    }
}

fn now_iso() -> String {
    // Format-stable UTC ISO-8601 with microseconds + explicit "+00:00" tz —
    // matches Python's `datetime.now(timezone.utc).isoformat()`.
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    iso_from_epoch_nanos(nanos)
}

fn iso_from_epoch(epoch_seconds: f64) -> String {
    let nanos = (epoch_seconds * 1_000_000_000.0) as u128;
    iso_from_epoch_nanos(nanos)
}

/// Render an ISO-8601 UTC string from epoch nanoseconds. Avoids a chrono
/// dep — we only need a stable string that PG accepts for `timestamptz`.
fn iso_from_epoch_nanos(nanos: u128) -> String {
    let secs = (nanos / 1_000_000_000) as i64;
    let micros = ((nanos / 1_000) % 1_000_000) as u32;
    // Days since 1970-01-01.
    let mut days = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400);
    let h = (secs_of_day / 3600) as u32;
    let m = ((secs_of_day % 3600) / 60) as u32;
    let s = (secs_of_day % 60) as u32;
    // Civil-from-days (Howard Hinnant's algorithm; standard).
    days += 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let doe = (days - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if mo <= 2 { y + 1 } else { y };
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        y, mo, d, h, m, s, micros
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_from_epoch_round_trips_known_value() {
        // 2026-01-01T00:00:00.000000+00:00 → 1767225600 seconds.
        let s = iso_from_epoch(1_767_225_600.0);
        assert_eq!(s, "2026-01-01T00:00:00.000000+00:00");
    }

    #[test]
    fn iso_from_epoch_handles_microseconds() {
        // 1767225600.123456 → microseconds 123456
        let s = iso_from_epoch(1_767_225_600.123_456);
        assert!(s.starts_with("2026-01-01T00:00:00."));
        assert!(s.ends_with("+00:00"));
    }
}
