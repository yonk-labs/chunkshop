# RM-A — Rust port of Python SP-A agent-memory primitives

**Date:** 2026-05-19
**Status:** active design
**Tracks:** chunkshop#9
**Inherits from:** `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md` (Python SP-A)

## 1. Problem framing

Python chunkshop 0.4.4 ships SP-A: a two-tier agent-memory layer (`agent_memory.memory`) with staging API, session-aware source, episode framer, consolidation chunker, memory sink, and a `read_pre_chunked` bridge to pg-raggraph. **Rust chunkshop has none of it.** Per `CLAUDE.md` ("vectors from any implementation are interchangeable") this is a real cross-language parity gap. RM-A closes it as a discrete Rust wave, analogous to R1–R4 (the original backend-trait waves).

Behavior is **fully inherited from Python SP-A** — same schema, same operational invariants, same YAML contracts. This spec only documents the Rust-specific adjustments and the latent-correctness requirements that must be designed in from day one.

## 2. Inherited decisions (do not re-litigate)

D1–D9 from Python SP-A apply unchanged. In particular:

| # | Decision | RM-A treatment |
|---|---|---|
| D2 | Library + staging-API push | `chunkshop::memory::{stage_event,…}` — same surface, Rust signatures |
| D3 | chunkshop owns the staging table | Same DDL, same indices, same `consumed` jsonb column |
| D4 | Two tiers | `tier='provisional'` (realtime) + `tier='consolidated'` (lazy supersede) |
| D5 | Consolidation = user-wired callable | Rust trait `Consolidator` + zero-network extractive default |
| D6 | Episode chunks + atomic fact rows, one table, `kind` discriminator | Same |
| D7–D9 | (DDL details from SP-A spec) | Unchanged |

**Schema is byte-identical to Python's.** A Python SP-A `agent_memory.memory` table is fully readable/writable by the Rust port, and vice-versa.

## 3. Rust-specific architecture

### 3.1 Module layout

Mirrors Python's layout, adapted to Rust's flatter source tree:

| Python | Rust |
|---|---|
| `chunkshop.memory.staging` | `chunkshop::memory::staging` (new top-level module) |
| `chunkshop.sources.session_staging` | `chunkshop::sources::session_staging` |
| `chunkshop.framers.session_episode` | `chunkshop::framer` (add a `SessionEpisodeFramer` variant) |
| `chunkshop.chunkers.consolidation` | `chunkshop::chunker` (add a `ConsolidationChunker` variant) |
| `chunkshop.sinks.memory_pg` | `chunkshop::sinks::memory_pg` (new file; new `AnySink::Memory(MemorySink)` variant) |
| `chunkshop.consolidators` | `chunkshop::consolidators` (new top-level module — trait + extractive default) |
| `chunkshop.memory.reader` (read_pre_chunked) | **Deferred to RM-B** (see §6 out of scope) |

### 3.2 Runtime / dep choices

- **`sqlx`** for the staging table and Postgres I/O — matches the existing Rust backend layer (R1–R3); no new DB driver.
- **`tokio`** async runtime — already in use across the workspace.
- **No PyO3.** RM-A is a clean-room Rust implementation, not a Python FFI binding. The Python SP-A is the *behavior reference*; the code does not call into Python at runtime.
- **`thiserror`** for typed errors (`MemoryError`); **`anyhow`** at module boundaries (matches existing patterns).
- **`serde` with `#[serde(deny_unknown_fields)]`** on every memory config struct — the Rust equivalent of pydantic's `extra="forbid"`. Non-negotiable: a typo in YAML must fail load-time, not at runtime.

### 3.3 Feature gating

New Cargo feature `memory`, gated on `[features]` in `rust/chunkshop/Cargo.toml`:

- Default: included in `full` (which is `default`), so the CLI binary always has memory.
- Library consumers can opt out via `default-features = false, features = ["source", "sink"]` if they don't want the memory surface.
- Internal `#[cfg(feature = "memory")]` guards on `pub mod memory`, the `SessionStagingSource`, the `SessionEpisodeFramer` variant, the `ConsolidationChunker` variant, and the `MemorySink` variant in `AnySink`.

### 3.4 Consolidator trait shape

```rust
pub trait Consolidator: Send + Sync {
    fn consolidate(&self, episode: &Episode) -> Result<ConsolidationOutput>;
}

pub struct Episode<'a> {
    pub text: &'a str,
    pub events: &'a [Event],
    pub session_id: &'a str,
    pub episode_start_ts: f64,
    pub episode_end_ts: f64,
}

pub struct ConsolidationOutput {
    pub summary: String,
    pub facts: Vec<FactTriple>,
}

pub struct FactTriple {
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub support_span: Option<String>,
    pub confidence: Option<f64>,
}
```

Wiring: `ConsolidationChunker` holds `Arc<dyn Consolidator>`. The default factory returns the zero-network `ExtractiveConsolidator`. Users wiring an LLM consolidator implement `Consolidator` on their own type and inject it.

This **diverges from Python** in shape (`module:`/`function:` callable vs. trait object) because Rust has no equivalent of Python's dynamic `importlib.import_module` for arbitrary user code. Users wiring a Rust consolidator do it at compile time, not via YAML. The YAML `consolidator:` section in the preset still names a built-in (`mode: extractive` or future `mode: llm`); custom impls are wired in code by the consumer.

### 3.5 AnySink extension

`rust/chunkshop/src/sinks/mod.rs`'s `AnySink` enum gains:

```rust
pub enum AnySink {
    Pg(PgSink),
    Mariadb(MariadbSink),
    Sqlite(SqliteSink),
    Clickhouse(ClickhouseSink),
    #[cfg(feature = "memory")]
    Memory(MemorySink),
}
```

The `load_sink` dispatcher returns `AnySink::Memory(...)` when `TargetConfig::Postgres.memory: Some(_)` (mirroring Python's `MemoryConfig`-on-`TargetConfig` discriminator).

## 4. Latent correctness requirements (pinned to Python fix commits)

These shipped in Python during/after SP-A and **must not be reintroduced** in Rust:

| # | Requirement | Python fix | Rust treatment |
|---|---|---|---|
| 1 | **O1 — session-level consolidate SELECT.** A late event for an already-consolidated session must trigger full-staging rebuild, not row-level re-select. | `49861dc` | Build it correctly from day 1: `WHERE session_id IN (SELECT … HAVING bool_or(consumed empty))` — never row-level. The plan's TDD task for SessionStagingSource asserts this with an O1 test before implementation. |
| 2 | **O3 — crash-safe per-session commit.** Watermark UPDATE inside `iter_documents` runs *after* all docs yield, in the same connection. MemorySink commits per document. | (inherent from SP-A; tested in `test_memory_resilience.py`) | Same control flow; sqlx `Transaction` per document. |
| 3 | **Bi-temporal type discipline.** `effective_from`/`effective_to`/`recorded_at` are `timestamptz`; ISO strings cast explicitly in comparisons. | `722b9ad` | sqlx types `chrono::DateTime<Utc>` end-to-end → no Decimal-from-epoch class of bug is even constructible. The invariant is enforced by the type system. |
| 4 | **Identifier safety.** schema/table/source_tag/promote_path each pass a regex allowlist. | (inherent) | Same regex (`^[a-z_][a-z0-9_]*$` etc.); rejected at config-load via serde validator. |
| 5 | **Append-only staging with ON CONFLICT (event_id) DO NOTHING.** Replay-safe. | (inherent) | Same DDL + same `INSERT … ON CONFLICT DO NOTHING`. |
| 6 | **`deny_unknown_fields` on all memory configs.** Typos fail load-time. | (inherent — pydantic `extra="forbid"`) | `#[serde(deny_unknown_fields)]` on every struct in the memory config tree. |

## 5. Testing strategy

Follows existing Rust conventions: `cargo test` for units, `tests/` directory for integration, skip-if-no-DSN for Postgres-touching tests.

**Unit** (no infra):
- `stage_event` event_id derivation (deterministic hash of `(session_id, seq|ts, content)`)
- `SessionStagingSource::documents_from_rows` (pure grouping; SQLite or in-memory mock)
- `SessionEpisodeFramer` (time-gap, role/tool boundary, max-turns, max-words, single/empty cases)
- `ConsolidationChunker` with a deterministic fake `Consolidator` impl (kinds, support_span, length-cap, **explicit O4 resilience test**: consolidator returns Err → episode emitted, zero facts, `consolidation_error` metadata key set, no propagation)
- `ExtractiveConsolidator` determinism

**Integration** (Postgres, `tests/memory_*.rs`):
- `ensure_staging_table` + `stage_event` + `stage_events` + `prune_staging` round-trips
- `MemorySink` DDL (canonical + promoted columns; identifier-safety failures)
- `supersede` (provisional gone, scoped by `source_tag`, second namespace untouched, double-run idempotent)
- `soft-invalidate` (newer contradicting fact retracts older; sparse triple no-op)
- **O1 late-event rebuild** (mirrors Python's `test_o1_late_event_rebuilds_from_full_staging`)
- **O3 crash/resume** (mirrors Python's `test_o3_crash_mid_run_resumes_cleanly`)
- end-to-end preset run (`memory/realtime.yaml` then `memory/consolidate.yaml`)
- **pg-raggraph contract test** (mirrors Python; same column-set assertion — drift fails CI on either side)

**Cross-implementation** (extra-credit, optional):
- Python writes via SP-A, Rust reads from same table — confirms schema interchangeability.

## 6. Success criteria

- **SC-R1** `chunkshop::memory::{stage_event, stage_events, ensure_staging_table, prune_staging}` exist with the same surface contract as Python; idempotent on `event_id`. (Unit + integration.)
- **SC-R2** `SessionStagingSource`, `SessionEpisodeFramer`, `ConsolidationChunker`, `MemorySink` exist as new providers wired into the existing dispatch enums (`AnySource`, `Framer`, `Chunker`, `AnySink`).
- **SC-R3** A Rust `ExtractiveConsolidator` provides zero-network defaults; the `Consolidator` trait lets users wire any impl at compile time.
- **SC-R4** `memory/realtime.yaml` and `memory/consolidate.yaml` (byte-identical to Python's, including schema/table defaults) drive a Rust `chunkshop-rs ingest --config` end-to-end run successfully against a seeded fixture.
- **SC-R5** The pg-raggraph contract test passes (same promoted column set as Python's).
- **SC-R6** Operational invariants O1–O8 each have a passing Rust test (notably O1 rebuild, O3 crash-resume, O4 per-session resilience).
- **SC-R7** A Python SP-A-written `agent_memory.memory` table can be read/written by Rust RM-A code without modification, and vice versa (cross-implementation smoke).
- **SC-R8** No regression in existing Rust tests (145+ passing today); `deny_unknown_fields` is enforced on every memory config struct.

## 7. Out of scope (explicit)

- **`read_pre_chunked` in Rust** — defer to **RM-B** (a separate, smaller wave) if a Rust consumer surfaces. Python ships it because pg-raggraph is Python; no current Rust consumer needs it.
- **Cross-backend memory-sink parity** (SQLite/MariaDB/ClickHouse memory sinks). Postgres-only for RM-A v1; matches Python SP-A §9.
- **PyO3 FFI bindings.** RM-A is standalone Rust.
- **SP-B/SP-C/SP-D.** Same scoping as Python SP-A.
- **Embedding-based topic-shift segmentation.** Deferred in Python SP-A; still deferred here.
- **A built-in LLM consolidator.** Like Python, ships only the extractive default; LLM wiring is the consumer's job.

## 8. References

- Python SP-A spec: `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`
- chunkshop#9 (this wave's tracking issue)
- O1 fix commit (must not recur): `49876dc` *(canonical: `49861dc`)*
- Epoch wiring fix (Python-specific bug class avoided by Rust types): `722b9ad`
- R-series precedent: `archive/docs/superpowers/plans/2026-05-01-v4-modular-backends-implementation.md`
- Existing Rust SP-A-adjacent modules: `rust/chunkshop/src/{chunker.rs, framer.rs, sources/, sinks/, backends/postgres.rs}`
