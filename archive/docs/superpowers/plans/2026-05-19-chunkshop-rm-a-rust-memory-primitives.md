# Implementation plan — RM-A (Rust port of Python SP-A agent-memory primitives)

**Spec:** `docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md`
**Tracks:** chunkshop#9
**Mirror reference:** `archive/docs/superpowers/plans/2026-05-19-chunkshop-memory-primitives-sp-a.md` (Python SP-A)

15 TDD tasks, mirroring Python SP-A's 15-task structure. Behavior is fully inherited from Python; each Rust task's "expected behavior" cell points at the Python test that locks the semantics. **Always write the failing test first; never implement before the test exists.**

| # | Task | Python anchor (behavior reference) |
|---|---|---|
| 1 | Config — `SessionStagingSourceConfig` + `MemoryConfig` on `TargetConfig` | `python/src/chunkshop/config.py` (SP-A blocks) |
| 2 | Config — `SessionEpisodeFramerConfig` | `python/src/chunkshop/config.py` |
| 3 | Config — `Consolidator` enum + `ConsolidationChunkerConfig` | `python/src/chunkshop/config.py` |
| 4 | Staging API — `chunkshop::memory` (stage_event, stage_events, ensure_staging_table, prune_staging) | `python/src/chunkshop/memory/staging.py` |
| 5 | `SessionStagingSource` (read with **session-level WHERE** from day 1) | `python/src/chunkshop/sources/session_staging.py` |
| 6 | `SessionEpisodeFramer` (pure, no I/O) | `python/src/chunkshop/framers/session_episode.py` |
| 7 | `Consolidator` trait + `ExtractiveConsolidator` default | `python/src/chunkshop/consolidators/extractive.py` |
| 8 | `ConsolidationChunker` (episode+fact emission, O4 resilience) | `python/src/chunkshop/chunkers/consolidation.py` |
| 9 | `MemorySink` — stamping + DDL (integration) | `python/src/chunkshop/sinks/memory_pg.py` |
| 10 | `MemorySink` — supersede + soft-invalidate (integration) | `python/src/chunkshop/sinks/memory_pg.py` |
| 11 | Loader dispatch additions | `python/src/chunkshop/{sources,sinks,framers,chunkers}/__init__.py` |
| 12 | `memory/` preset YAMLs (byte-identical to Python's) | `python/src/chunkshop/configs/memory/{realtime,consolidate}.yaml` |
| 13 | End-to-end + pg-raggraph contract test | `python/tests/chunkshop/test_memory_e2e.py` |
| 14 | O1 + O3 resilience tests (integration) | `python/tests/chunkshop/test_memory_resilience.py` |
| 15 | Regression sweep + docs note + CHANGELOG entry | (final pass) |

## Conventions

- All file paths are under `rust/chunkshop/` unless noted.
- Each task: **(a) write failing test → (b) verify red → (c) implement minimal → (d) verify green → (e) commit**.
- Commit messages: `feat(rust/memory): <task>` or `test(rust/memory): <task>`.
- Schema/table identifier safety: same regex as Python (`^[a-z_][a-z0-9_]*$`).
- All new memory config structs MUST carry `#[serde(deny_unknown_fields)]`.
- Postgres integration tests skip if `CHUNKSHOP_TEST_DSN` not reachable (matches existing Rust pattern in `tests/backend_postgres_conn.rs`).
- New Cargo feature `memory`, default-on within `full`.

---

## Task 1: Config — `SessionStagingSourceConfig` + `MemoryConfig`

**Files:**
- Modify: `src/config.rs` — add `SessionStagingSourceConfig`, add `MemoryConfig`, add `MemorySource(SessionStagingSourceConfig)` variant to `SourceConfig`, add `memory: Option<MemoryConfig>` field to PG target.
- Add `Cargo.toml [features]`: `memory = ["source", "sink"]`; include in `full`.

**Step 1: Write failing test** — `src/config.rs` `#[cfg(test)] mod memory_config_tests`:
```rust
#[test]
fn session_staging_source_deserialises() {
    let yaml = r#"
type: session_staging
dsn: "postgresql://localhost/x"
staging_table: chunkshop_staging
staging_schema: public
mode: realtime
"#;
    let cfg: SourceConfig = serde_yaml::from_str(yaml).unwrap();
    assert!(matches!(cfg, SourceConfig::SessionStaging(_)));
}
#[test]
fn memory_block_on_pg_target() { /* tier/supersede/namespace round-trip */ }
#[test]
fn unknown_field_in_memory_rejected() { /* asserts deny_unknown_fields */ }
```

**Step 2: Run → red** (`cargo test --features memory -- memory_config`)
**Step 3: Implement** structs with serde (`#[serde(tag = "type", rename_all = "snake_case")]` already used for `SourceConfig`). Both new structs `#[serde(deny_unknown_fields)]`. `MemoryConfig`: `tier: MemoryTier` (enum Provisional|Consolidated, `#[serde(rename_all = "lowercase")]`), `supersede: bool` (default true on consolidated only via validator), `namespace: Option<String>`.
**Step 4: Run → green**
**Step 5: Commit**: `feat(rust/memory): config models for session staging + MemoryConfig`

---

## Task 2: Config — `SessionEpisodeFramerConfig`

**Files:** Modify: `src/config.rs` — add `SessionEpisodeFramerConfig`, add `SessionEpisode(SessionEpisodeFramerConfig)` variant to whatever framer-config enum exists today (or create one).

**Step 1: Failing test** — fields: `max_gap_seconds: u64` (default 1800), `max_turns: u32` (default 40), `max_words: u32` (default 1200), `boundary_on_tool: bool` (default true). Round-trip + deny_unknown.
**Step 2–5:** as Task 1. Commit: `feat(rust/memory): SessionEpisodeFramerConfig`

---

## Task 3: Config — Consolidator union + `ConsolidationChunkerConfig`

**Files:** Modify: `src/config.rs` — add `ConsolidatorMode` enum (`Extractive` for v1, leave room for `Llm`/`Callable` later), `ConsolidationChunkerConfig { base, consolidator: ConsolidatorConfig, fact_max_chars: usize, if_oversize: Option<IfOversize> }`. Add `Consolidation(...)` variant to chunker-config enum.

**Step 1: Failing test** — ConsolidationChunkerConfig deserialises with base nested (sentence_aware), consolidator { mode: extractive }, fact_max_chars=1200. Also: `if_oversize` requires a ceiling, mirroring Python's behavior (Python `2f6b09c`).
**Step 2–5:** as Task 1. Commit: `feat(rust/memory): Consolidator + ConsolidationChunkerConfig`

---

## Task 4: Staging API — `chunkshop::memory` module

**Files:**
- Create: `src/memory/mod.rs` (re-exports), `src/memory/staging.rs`.
- Modify: `src/lib.rs` — `#[cfg(feature = "memory")] pub mod memory;`.

**Step 1: Failing tests** — `tests/memory_staging.rs` (integration, skip-if-no-DSN):
```rust
#[tokio::test] async fn ensure_creates_table_with_indices() {…}
#[tokio::test] async fn stage_event_idempotent_on_event_id() {…}     // double-stage same event = 1 row
#[tokio::test] async fn stage_events_bulk_inserts() {…}
#[tokio::test] async fn prune_only_drops_consolidated_older_than() {…}
```

**Step 2: Run → red.**

**Step 3: Implement** — `pub async fn ensure_staging_table(pool: &PgPool, schema: &str, table: &str) -> Result<()>`: CREATE TABLE IF NOT EXISTS + 3 indices, identifier-safety regex applied before formatting. `pub async fn stage_event(...)` with deterministic event_id derivation (same hash as Python: `sha256(session_id|seq|ts|content)`), single `INSERT … ON CONFLICT (event_id) DO NOTHING`. `pub async fn stage_events(...)` bulk via `sqlx::query!` + unnest. `pub async fn prune_staging(...)` with the `consumed ? 'consolidated'` predicate.

**Step 4: Run → green.**

**Step 5: Commit:** `feat(rust/memory): staging API (stage_event/stage_events/ensure/prune)`

---

## Task 5: `SessionStagingSource` — read logic (with O1 session-level WHERE from day 1)

**Files:** Create: `src/sources/session_staging.rs`. Modify: `src/sources/mod.rs` — add `#[cfg(feature = "memory")] pub mod session_staging;` and `AnySource::SessionStaging(...)` variant.

**Step 1: Failing tests** — `tests/source_session_staging.rs`:
```rust
#[tokio::test] async fn yields_one_document_per_session() {…}
#[tokio::test] async fn events_ordered_by_seq_then_ts() {…}
#[tokio::test] async fn realtime_mode_advances_consumed_realtime() {…}
#[tokio::test] async fn consolidate_mode_uses_session_level_where() {…}   // O1 baked in
#[tokio::test] async fn max_sessions_caps_output() {…}
```

**Step 2: Run → red.**

**Step 3: Implement** — `SessionStagingSource { cfg, pool }`. **The consolidate-mode SQL MUST be session-level from the first commit** (this is the O1 latent-correctness requirement — do not write a row-level version first then fix it):

```sql
SELECT event_id, session_id, seq, role, content, tool, outcome,
  extract(epoch FROM coalesce(event_ts, staged_at))::double precision
FROM {schema}.{table}
WHERE session_id IN (
  SELECT session_id FROM {schema}.{table}
  GROUP BY session_id
  HAVING max(coalesce(event_ts, staged_at)) < now() - interval '{N} seconds'
     AND (bool_or(coalesce(consumed->>'consolidated','') = '')
          OR max(staged_at) > min(nullif(consumed->>'consolidated','')::timestamptz))
)
ORDER BY session_id, seq NULLS LAST
```

Realtime mode: row-level WHERE as Python (correct for realtime). Watermark UPDATE runs after all rows are yielded, in same connection. Test asserts that a session with a late event after consolidation gets its FULL history re-emitted (this fails if anyone reverts to row-level).

**Step 4: Run → green.**

**Step 5: Commit:** `feat(rust/memory): SessionStagingSource with session-level consolidate WHERE (O1 baked in)`

---

## Task 6: `SessionEpisodeFramer` (pure, no I/O)

**Files:** Modify: `src/framer.rs` — add `SessionEpisodeFramer` variant to the framer enum + impl.

**Step 1: Failing tests** (unit, in-file `#[cfg(test)]`):
```rust
#[test] fn single_episode_when_under_thresholds() {…}
#[test] fn time_gap_creates_boundary() {…}
#[test] fn max_turns_creates_boundary() {…}
#[test] fn max_words_creates_boundary() {…}
#[test] fn tool_boundary_when_enabled() {…}
#[test] fn empty_session_yields_zero_episodes() {…}
```

**Step 2–5:** Implement, gap-arithmetic on numeric epoch ts (matches Python). Each episode is a new `Document` with `episode_start_ts`/`episode_end_ts`/`frame_seq`/`_episode_events` metadata. Commit: `feat(rust/memory): SessionEpisodeFramer (stateless episode segmentation)`

---

## Task 7: `Consolidator` trait + `ExtractiveConsolidator` default

**Files:** Create: `src/consolidators/mod.rs`, `src/consolidators/extractive.rs`. Modify: `src/lib.rs` — `pub mod consolidators;`.

**Step 1: Failing tests** (unit):
```rust
#[test] fn extractive_deterministic_on_same_input() {…}
#[test] fn extractive_emits_summary_no_facts_on_short_text() {…}
#[test] fn extractive_strips_role_tags() {…}                              // py 527f9f4
```

**Step 2–5:** Implement `Consolidator` trait + `ExtractiveConsolidator`. The extractive default mirrors Python's zero-network heuristics: take top-N sentences by length/keyword density for the summary; emit no facts (facts require structured extraction; LLM consolidator's job). Strip `[user]` / `[assistant]` role tags from input even with leading whitespace. Commit: `feat(rust/memory): Consolidator trait + zero-network extractive default`

---

## Task 8: `ConsolidationChunker` (episode+fact emission, O4 resilience)

**Files:** Modify: `src/chunker.rs` — add `ConsolidationChunker` variant + impl.

**Step 1: Failing tests** (unit, with a fake `Consolidator` impl):
```rust
#[test] fn emits_episode_chunk_plus_one_fact_per_triple() {…}
#[test] fn fact_chunk_truncated_to_fact_max_chars() {…}
#[test] fn consolidator_error_yields_episode_only_with_error_metadata() {…}   // O4
#[test] fn metadata_carries_subject_predicate_object_support_span() {…}
#[test] fn extractor_alias_stamped_for_pgrg_contract() {…}                    // py Task 14 fix
```

**Step 2–5:** Implement. Episode chunk = base chunker run on the episode text (default sentence_aware), facts emitted as separate chunks with `kind=fact` metadata. O4: catch consolidator error → episode-only output + `consolidation_error` metadata key + `extractor: <mode>` alias for pg-raggraph contract. Commit: `feat(rust/memory): ConsolidationChunker (episode+fact emission, O4 resilience)`

---

## Task 9: `MemorySink` — stamping + DDL (integration)

**Files:** Create: `src/sinks/memory_pg.rs`. Modify: `src/sinks/mod.rs` — add `MemorySink` variant to `AnySink`.

**Step 1: Failing tests** — `tests/memory_sink.rs` (skip-if-no-DSN, self-cleaning):
```rust
#[tokio::test] async fn create_table_includes_canonical_plus_promoted_columns() {…}
#[tokio::test] async fn write_stamps_tier_namespace_recorded_at() {…}
#[tokio::test] async fn write_namespace_qualified_row_id() {…}            // py 3dbd12f
#[tokio::test] async fn invalid_table_identifier_rejected() {…}
```

**Step 2–5:** Implement. `MemorySink` wraps `PgSink` (composition) — DDL adds promoted columns from `promote_metadata` config. Stamping in `write_document`: tier/namespace/recorded_at unconditionally; `effective_from = m.get("episode_end_ts")` converted ISO via helper (mirrors Python `_iso`). Row id format: `{namespace}::{doc_id}::{seq_num}`. Commit: `feat(rust/memory): MemorySink stamping + DDL`

---

## Task 10: `MemorySink` — supersede + soft-invalidate (integration)

**Files:** Modify: `src/sinks/memory_pg.rs`.

**Step 1: Failing tests** (extends `tests/memory_sink.rs`):
```rust
#[tokio::test] async fn consolidated_supersedes_provisional_scoped_by_source() {…}
#[tokio::test] async fn double_consolidate_is_idempotent() {…}
#[tokio::test] async fn soft_invalidate_retracts_older_contradicting_fact() {…}
#[tokio::test] async fn sparse_triple_no_op() {…}
```

**Step 2–5:** Implement. Supersede: `DELETE FROM ... WHERE doc_id=$1 AND source=$2` before insert, scoped per (doc_id, source); idempotent on re-run. Soft-invalidate: separate UPDATE setting `retracted=true, retracted_at=now(), effective_to=$1::timestamptz WHERE source=$2 AND subject=$3 AND predicate=$4 AND effective_from < $5::timestamptz AND coalesce(retracted,false)=false` (mirrors Python including the explicit `::timestamptz` casts — bi-temporal type discipline). Sparse-triple no-op = facts missing subject/predicate/effective_from are skipped. Commit: `feat(rust/memory): MemorySink supersede + soft-invalidate`

---

## Task 11: Loader dispatch additions

**Files:** Modify: `src/sources/mod.rs`, `src/sinks/mod.rs`, `src/framer.rs`, `src/chunker.rs` — wire all four new variants into the existing dispatch enums (AnySource, AnySink, Framer, Chunker) so `load_source(cfg) / load_sink(cfg) / load_framer(cfg) / load_chunker(cfg)` work end-to-end.

**Step 1: Failing tests** — `tests/memory_dispatch.rs`:
```rust
#[test] fn load_source_session_staging() {…}
#[test] fn load_framer_session_episode() {…}
#[test] fn load_chunker_consolidation() {…}
#[tokio::test] async fn load_sink_memory_when_memoryconfig_present() {…}
```

**Step 2–5:** wire match arms. Commit: `feat(rust/memory): loader dispatch for memory components`

---

## Task 12: `memory/` preset YAMLs (byte-identical to Python's)

**Files:** Create `rust/chunkshop/src/configs/memory/realtime.yaml` + `consolidate.yaml`.

**Step 1: Failing test** — `tests/memory_presets.rs`: load both presets, assert structural keys match Python's (or just assert `load_config()` succeeds against them and produces the expected variants).

**Step 2–5:** Copy Python's YAMLs verbatim (they're already in `python/src/chunkshop/configs/memory/`). The schema/table defaults match (`agent_memory.memory`). Commit: `feat(rust/memory): realtime + consolidate preset YAMLs`

---

## Task 13: End-to-end + pg-raggraph contract test

**Files:** Create: `tests/memory_e2e.rs`, `tests/fixtures/memory_session.jsonl` (copy from Python).

**Step 1: Failing tests** (mirrors `test_memory_e2e.py`):
```rust
#[tokio::test] async fn e2e_realtime_then_consolidate() {…}
#[tokio::test] async fn pgraggraph_contract_columns_present() {…}
```

**Step 2: Run → red.**

**Step 3: Fix wiring** (anticipated issues identical to Python: ensure presets' `promote_metadata` covers every contract column; `extractor` alias on every fact chunk).

**Step 4: Run → green.**

**Step 5: Commit:** `test(rust/memory): e2e realtime→consolidate + pg-raggraph contract guard`

---

## Task 14: O1 + O3 resilience tests

**Files:** Create: `tests/memory_resilience.rs`.

**Step 1: Failing tests** (mirrors Python's `test_memory_resilience.py`):
```rust
#[tokio::test] async fn o1_late_event_rebuilds_from_full_staging() {…}    // would FAIL if anyone reverted SessionStagingSource to row-level
#[tokio::test] async fn o3_crash_mid_run_resumes_cleanly() {…}            // injects an error in MemorySink::write_document for the 3rd doc; rest still committed; rerun completes
```

**Step 2: Run.** With Task 5 implemented correctly, both should pass on the first run. The O1 test is here as a regression guard — its existence is the protection against future "optimisations" that revert the session-level WHERE.

**Step 5: Commit:** `test(rust/memory): O1 + O3 resilience guards`

---

## Task 15: Regression sweep + docs + CHANGELOG

**Step 1: Full Rust test suite** — `cargo test --features full` from `rust/` — must pass clean (>= the current 145-test baseline + ~25 new memory tests, so ~170+).

**Step 2: Docs note** — append to `docs/incremental.md` a short "Agent memory (RM-A, Rust)" section parallel to the existing Python "Agent memory (SP-A)" section. Same two-cell pattern; same env-var (`CHUNKSHOP_MEMORY_DSN`); CLI example uses `chunkshop-rs ingest --config rust/chunkshop/src/configs/memory/realtime.yaml`.

**Step 3: CHANGELOG entry** — under next `## Unreleased` (or pending release), document RM-A as the Rust port of SP-A.

**Step 4: Cross-implementation smoke** (optional, extra credit) — `tests/memory_cross_impl.rs`: Python writes via SP-A (driven from a shell script in the test, or pre-seeded fixture), Rust reads via SessionStagingSource → MemorySink and asserts identical rows. If too costly to set up, defer.

**Step 5: Commit:** `chore(rust/memory): regression sweep green + docs + CHANGELOG (RM-A complete)`

---

## Self-review

**Spec coverage:**
- SC-R1 (Task 4), SC-R2 (Tasks 5,6,8,9,11), SC-R3 (Task 7), SC-R4 (Tasks 12,13), SC-R5 (Task 13 contract test), SC-R6 (Tasks 8,10,14 — O1/O3/O4), SC-R7 (Task 15 cross-impl, optional), SC-R8 (Task 15 regression).
- Out-of-scope items (RM-B reader, sqlite/mariadb/clickhouse memory sinks, PyO3) untouched.

**Latent-correctness coverage:**
- O1 (session-level WHERE): baked into Task 5 from day 1; tested by Task 14.
- O3 (crash-safe): per-doc commit by sqlx Transaction; tested by Task 14.
- Bi-temporal types: enforced by sqlx `chrono::DateTime<Utc>` end-to-end (no class of bug possible).
- Identifier safety: same regex; Task 9 tests rejection.
- Append-only ON CONFLICT: Task 4 tests idempotency.
- `deny_unknown_fields`: Task 1 tests rejection.

**Diff from Python plan:**
- 15 tasks vs Python's 15 — same count.
- Task 4 (staging API) is async in Rust; otherwise behaviourally identical.
- Task 5 builds O1 in from day 1 (Python had to fix it post-hoc in `49861dc`) — avoids the same data-loss bug.
- Task 7 (consolidator) uses a trait rather than `module:`/`function:` callable. YAML wiring still names a built-in mode; custom impls are compile-time.
- No `extra="forbid"` story — replaced by `#[serde(deny_unknown_fields)]` (same effect).

**Open implementation questions** (decide during the task, not now):
- Whether `AnySource` and `AnySink` enums get the memory variants `#[cfg(feature = "memory")]`-gated (probably yes, matches existing pattern).
- Whether to provide a `tracing` event for O4 consolidator-error (probably yes — log + continue).
