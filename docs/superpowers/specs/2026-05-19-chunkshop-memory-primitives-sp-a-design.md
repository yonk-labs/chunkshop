# Design Spec — chunkshop Agent-Memory Primitives (SP-A)

**Date:** 2026-05-19
**Status:** Design approved, pre-plan
**Topic slug:** chunkshop-memory-primitives-sp-a
**Research basis:** `skill-output/research-and-design/Research-Report-agentic-memory-chunking-retrieval.md` (+ Summary)

## 1. Context & Problem

Flat stream chunking of agent sessions through chunkshop yields ~50–60% recall — below a brute-force full-context baseline (~73% LoCoMo / ~92% offline LongMemEval). Research concluded this is a *missing-primitives* problem, not a tuning problem, and that chunkshop is structurally an excellent **offline memory-consolidation engine** (batch CLI + external scheduler + append-WHERE + subprocess isolation) but lacks the memory primitives and owns only a thin read path.

The full vision decomposes into four sub-projects:

- **SP-A — chunkshop memory primitives** *(this spec)*: staging API, session-aware source, episode framer, consolidation chunker, memory sink, `memory/` preset.
- **SP-B — realtime reader / graph layer**: satisfied by **pg-raggraph (Python)**, which already has a `chunkshop-integration.md` (Pattern C) and an `ingest_records(pre_chunked=…)` seam. Not built here.
- **SP-C — realtime↔batch orchestrator**: scheduling glue. Out of scope (chunkshop assumes an external scheduler).
- **SP-D — eval harness**: LongMemEval/MultiHop-RAG with LLM-as-judge, on chunkshop's bakeoff infra. Out of scope here.

SP-A is the highest write-side recall leverage, lowest risk, and the structural fit. Each sub-project gets its own spec → plan → build cycle.

## 2. Locked Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | First sub-project | SP-A (chunkshop memory primitives) |
| D2 | Input contract | chunkshop is a **library**; caller pushes session-tagged events via a staging API. Capture/forward hooks are **out of scope**. |
| D3 | Raw staging | chunkshop **owns a raw, append-only session staging table** |
| D4 | Two tiers | realtime (provisional, usable ~20 min) + lazy consolidation (hours/days → optimal structure, supersedes provisional) |
| D5 | Consolidation intelligence | **user-wired callable** (same pattern as `lede`/`CallableSummarizer`); v1 default = existing extractive primitives; LLM is opt-in wiring |
| D6 | Output shape | episode chunks **+ atomic fact rows**, one table, `kind` discriminator |
| D7 | Temporal/versioning | supersede (`tier`) + per-fact soft-invalidate; **schema aligned 1:1 to `pg_raggraph.facts`** (overrides earlier `valid_at`/`invalidated_at` naming) |
| D8 | Architecture | **Approach A** — two cells, one staging source, tier = config |
| D9 | pg-raggraph alignment | Yes — align 1:1, degrade gracefully (LLM → SPO triples; extractive default → `support_span` proposition + sparse triple) |

## 3. Architecture (Approach A)

```
OUT OF SCOPE (hooks/forwarder)        IN SCOPE (SP-A, chunkshop library)

 live capture ──chunkshop.memory.stage_event(session_id, role, content,
                  tool, outcome, ts, event_id?)──▶ [staging table]
                                                   (chunkshop-owned, append-only)
                                                        │              │
              realtime.yaml (scheduler: every few min)   consolidate.yaml (scheduler: nightly,
              WHERE new events                            WHERE session quiet > min_age)
                    │                                          │
            SessionStagingSource(mode=realtime)        SessionStagingSource(mode=consolidate)
                    │ identity framer                         │ SessionEpisodeFramer
                    │ fixed_overlap chunker                    │ consolidation chunker (callable)
                    │ int8 bge-small embed                     │ int8 bge-small embed
                    ▼                                          ▼
            MemorySink tier=provisional ──supersede◀── MemorySink tier=consolidated
                                                       kind=episode|fact; soft-invalidate
                                                       contradicted prior facts
```

Both tiers are ordinary chunkshop cells differing only by YAML, driven by `runner.run_cell` + the existing orchestrator + an **external** scheduler (cron/systemd). Reuses `if_oversize`, the `SummarizerConfig`-style callable wiring, identifier-safety, write-once `source`, append-WHERE.

## 4. Components & Contracts

Pipeline order is chunkshop's verified existing flow: `source.iter_documents()` → `framer.frame(raw)` → `chunker.chunk(doc)` → `embedder.embed(embedded_content)` → `extractor.extract(original_content)` → metadata merge (doc/framer, extractor, **chunker-wins**) → `sink.write_document(doc.id, chunks, embeddings, tags)`.

**C1. `chunkshop.memory` — staging API** (new module, e.g. `python/src/chunkshop/memory/staging.py` + `__init__.py`)
- `stage_event(dsn, *, session_id, role, content, ts, seq=None, tool=None, outcome=None, event_id=None, metadata=None) -> str`
- `stage_events(dsn, events: list[dict]) -> int` (bulk)
- `ensure_staging_table(dsn, *, table=…, schema=…) -> None`
- `prune_staging(dsn, *, older_than, only_consolidated=True) -> int`
- Append-only INSERT, **idempotent on `event_id`** via `ON CONFLICT (event_id) DO NOTHING` (replay-safe). Deliberately **not** the `Pipeline` class (that requires `inline` source). `event_id` defaults to a deterministic hash of `(session_id, seq|ts, content)` if caller omits it.

**C2. Staging table** (chunkshop-owned, separate from the memory table; identifier-safe name, default `chunkshop_staging`)
Columns: `event_id text PRIMARY KEY`, `session_id text NOT NULL`, `seq bigint NULL`, `role text`, `content text NOT NULL`, `tool text NULL`, `outcome text NULL`, `event_ts timestamptz NULL`, `staged_at timestamptz NOT NULL DEFAULT now()`, `consumed jsonb NOT NULL DEFAULT '{}'`, `metadata jsonb NOT NULL DEFAULT '{}'`. Indices: `(session_id, seq)`, `(staged_at)`, partial index for not-yet-consolidated selection. Append-only; ordering within a session = `seq` when present else `(event_ts, staged_at)`.

**C3. `SessionStagingSource`** (new source, `type: session_staging`, `_DsnResolvable`; file `sources/session_staging.py`, pydantic model in `SourceConfig` union, loader branch in `sources/__init__.py`)
- `iter_documents()` yields **one `Document` per session**: `id=session_id`, `content`= deterministic role/tool/outcome-tagged serialization of ordered events (stable line format the framer/chunker parse, each line prefixed with its stable staging ordinal), `metadata={session_id, namespace, event_count, first_ts, last_ts, mode, base_ordinal}`.
- Config: `dsn`, `staging_table`, `staging_schema`, `mode: Literal["realtime","consolidate"]`, `min_age_seconds: int` (consolidate: last event older than N), watermark behavior via `consumed` jsonb (realtime: events past `consumed.realtime`). Append-WHERE incremental.
- **Realtime idempotency:** in `realtime` mode the source emits the session's events with a **stable per-event ordinal** (the staging `seq`/row order) carried into chunk `seq_num`, so `id=session_id::ordinal` upserts deterministically across incremental re-runs (no seq-restart collision). The `consolidate` mode does not need stable ordinals — `MemorySink` rebuilds via delete-then-insert (O1) and may freely reassign `seq_num`.

**C4. `SessionEpisodeFramer`** (new framer, `type: session_episode`; file `framers/session_episode.py`, pydantic in `FramerConfig` union, loader branch)
- `frame(raw) -> list[Document]`, **stateless / no I/O** (verified `DocFramer` constraint). v1 segmentation = time-gap threshold + role/tool structural boundaries + `max_turns`/`max_words` cap. Stamps `metadata["framer"]="session_episode"`, `frame_seq`, `episode_start_ts`, `episode_end_ts`, `episode_turn_span`.
- Embedding-based topic-shift segmentation is **deferred** (needs a model → violates stateless). Realtime cell uses the existing `identity` framer (no segmentation, cheap).

**C5. Consolidation chunker** (new, `type: consolidation`; file `chunkers/consolidation.py`, pydantic in `ChunkerConfig` union, loader branch) — a **chunker, not an extractor** (verified: `Extractor.extract(text)` can only annotate, cannot emit rows).
- Mirrors `SummaryEmbedChunker`: wraps a base chunker; invokes a user-wired callable through a new `ConsolidatorConfig` with `external|callable|passthrough` modes (parallels `SummarizerConfig`). Default `callable` → `chunkshop.consolidators.extractive` (ships lede + spaCy + RAKE, zero-network). Users wire an LLM module for real SPO triples.
- Emits `Chunk`s of two kinds via `metadata["kind"]`:
  - `episode`: `original_content`=raw episode text, `embedded_content`=summary-enriched (callable summary).
  - `fact`: one per fact; `original_content`/`embedded_content`=`support_span` proposition text (embedded ⇒ vector-retrievable, the Dense-X lever); `metadata` carries `subject/predicate/object` (sparse under extractive default), `confidence`, `effective_from`, `source_chunk_seq` (parent episode `seq_num`).
- Reuses `if_oversize` for episodes. **Facts are length-capped, not split** (truncate `support_span` to embedder limit, set `metadata.truncated=true`) — splitting a proposition would break the triple.
- **Defensive (operational rule O4):** callable error/timeout for an episode → passthrough fallback (raw episode chunk, zero facts), stamp `metadata.consolidation_error`; never raise. Keeps the nightly cell green; a later run rebuilds.

**C6. `MemorySink`** (new sink mode; `PgSink` subclass, file `sinks/memory_pg.py`, target mode `memory`)
- Inherits DDL (`_canonical_cols` + `promote_metadata`), identifier safety, `query_top_k`, dim preflight.
- **Owns the cell-level promoted fields** so chunkers/framers stay memory-agnostic: `MemorySink` stamps `tier` (from config), `namespace` (= the `source`/`source_tag` value; the `namespace` column is just a pg-raggraph-named mirror of `source`), `recorded_at` (run start time), and defaults `kind='episode'` when a chunk's metadata omits it. `SessionStagingSource` provides `session_id` (also `doc_id`). The consolidation chunker only overrides `kind='fact'` and the per-fact fields. (`tier`/`namespace`/`recorded_at` are authoritative and set unconditionally by the sink — chunkers cannot override them. Only `kind` is a default: applied when absent, so a consolidation-chunker `fact` chunk's explicit `kind='fact'` is preserved while episode chunks fall back to `'episode'`.)
- Overrides `write_document` to add, in one transaction per session:
  1. **Supersede** (consolidate tier, first touch of a session in the run): `DELETE FROM <t> WHERE doc_id=<session_id> AND source=<namespace>` (drops provisional + any prior consolidated for that session, scoped to this cell's `source` so cross-namespace provenance safety holds) → insert fresh set.
  2. **Soft-invalidate** (post-insert): for each new fact, `UPDATE … SET retracted=true, retracted_at=now(), effective_to=<new.effective_from> WHERE kind='fact' AND namespace=… AND subject=… AND predicate=… AND effective_from < new.effective_from AND NOT retracted`. Sparse extractive triples ⇒ conservative no-op (no false invalidation).

**C7. `memory/` preset** (config dir like `configs/factorial-int8/`)
- `realtime.yaml`: `session_staging(mode=realtime)` → `identity` → `fixed_overlap` → int8 bge-small (384) → `none` extractor → `MemorySink(mode=memory, tier=provisional)`.
- `consolidate.yaml`: `session_staging(mode=consolidate, min_age_seconds)` → `session_episode` → `consolidation(base=sentence_aware, consolidator=callable)` → int8 bge-small (384) → keyphrase/entity extractor (tags only) → `MemorySink(mode=memory, tier=consolidated, supersede=true)`.

## 5. Data Model

Physical table = chunkshop's **unchanged** canonical chunks table (`id` PK =`doc_id::seq_num`, `doc_id`, `seq_num`, `original_content`, `embedded_content`, `tags text[]`, `metadata jsonb`, `embedding vector(384)`, `source`, `created_at`) **plus promoted columns** via the existing `promote_metadata` seam (jsonb path → typed column, `ADD COLUMN IF NOT EXISTS`). pg-raggraph-aligned:

| Promoted column | Type | Episode | Fact | jsonb path |
|---|---|---|---|---|
| `kind` | text | `episode` | `fact` | `kind` |
| `namespace` | text | =source_tag | same | `namespace` |
| `session_id` | text | ✓ | ✓ | `session_id` |
| `tier` | text | `provisional`\|`consolidated` | `consolidated` | `tier` |
| `recorded_at` | timestamptz | ✓ | ✓ | `recorded_at` |
| `subject`/`predicate`/`object` | text | null | ✓ (sparse if extractive) | `subject`/`predicate`/`object` |
| `support_span` | text | null | proposition text (= embedded body) | `support_span` |
| `confidence` | float | null | ✓ | `confidence` |
| `effective_from` | timestamptz | episode end ts | fact event ts | `effective_from` |
| `effective_to` | timestamptz | null | set on supersession | `effective_to` |
| `retracted` | bool | false | false→true on contradiction | `retracted` |
| `retracted_at` | timestamptz | null | set on invalidate | `retracted_at` |
| `extractor` | text | callable mode | callable mode | `extractor` |
| `source_chunk_seq` | int | null | parent episode `seq_num` | `source_chunk_seq` |

`doc_id = session_id`; consolidation chunker assigns one monotonic `seq_num` across all emitted chunks (episodes then their facts); `id = session_id::seq_num`. `count_docs()` = distinct sessions.

**pg-raggraph coupling contract:** the promoted column set/names/types must match what pg-raggraph's `facts` table + Pattern C bridge expect (`subject/predicate/object/source_chunk_id≈source_chunk_seq/support_span/confidence/effective_from/effective_to/retracted/retracted_at/extractor`, `namespace` filter, episode chunks via existing `original_content/embedded_content/embedding/metadata/doc_id/seq_num`). `session_id/tier/recorded_at` are additionally readable from jsonb without pg-raggraph changes. Embedding dim fixed at 384 (int8 bge-small) to match pg-raggraph's default so Pattern C's `pre_chunked` path accepts the rows. This contract is CI-guarded (see §7).

## 6. Operational Invariants

- **O1. No "session-closed" flag.** Consolidate selects only sessions quiet > `min_age_seconds`; late events → next run **rebuilds from full staging** (delete-then-insert is idempotent).
- **O2. Consolidated-wins read rule.** When both tiers exist for a `session_id`, the reader (SP-B) must prefer `tier='consolidated'`. Makes realtime/consolidate races benign; documented SP-B contract.
- **O3. Crash-safe.** Per-session (per-document) commit: a crash leaves processed sessions consolidated, in-flight session atomic, rest pending; next scheduled run resumes.
- **O4. Per-session resilience** (deviates from `run_cell`'s abort-on-error): consolidation chunker degrades to passthrough on callable failure; one poisoned session never blocks the nightly run.
- **O5. Facts length-capped, not split** (C5).
- **O6. Staging retention is caller-driven** via `prune_staging`; default keep (pruning forfeits future re-consolidation — documented tradeoff).
- **O7. Isolation inherited** — supersede DELETE scoped by `source`/`namespace`; append-mode dim preflight blocks embedder drift.
- **O8. Time semantics** — `effective_from`=caller event ts (fallback `staged_at`); `recorded_at`=run time; soft-invalidate orders by `effective_from`, so callers must supply roughly monotonic event timestamps (a `stage_event` contract requirement).

## 7. Testing Strategy

Follows chunkshop conventions (pytest; per-stage units; Postgres integration tests **skip** if `$CHUNKSHOP_TEST_DSN` unreachable and **drop their own schema** in teardown; memory tables namespaced `chunkshop_test_memory*`).

**Unit (no infra):** `stage_event` pure logic (event_id derivation, null-ts fallback); `SessionStagingSource` over SQLite `:memory:` (one Document/session, ordering, mode selection); `SessionEpisodeFramer` pure (time-gap, role/tool boundary, max-turns, single/empty); consolidation chunker with deterministic fake callable (kinds, `support_span`, summary-enrich, length-cap, **explicit O4 resilience test**: callable raises → episode present, zero facts, `consolidation_error`, no propagation); default `chunkshop.consolidators.extractive` determinism.

**Integration (Postgres, skip-if-unreachable, self-cleaning):** MemorySink DDL (canonical + promoted columns, identifier safety); promote mapping per §5 table; **supersede** (provisional gone, scoped by `source`, second namespace untouched, double-run idempotent); **soft-invalidate** (later contradicting fact retracts earlier, history preserved, `WHERE NOT retracted` newest-only; sparse-triple no-op); **late-event rebuild**; **crash/resume** (fail after session k, rerun completes); `prune_staging` (only consolidated & older-than); **pg-raggraph contract test** (assert promoted column set/names/types match Pattern C expectations — drift fails CI here, not silently in SP-B); end-to-end preset run (`realtime.yaml` then `consolidate.yaml` over a seeded fixture → store matches §5).

**Out of testing scope:** recall/LongMemEval/MultiHop-RAG = SP-D. SP-A retrieval smoke only: `query_top_k` returns consolidated episode over a provisional one. Memory mode is Postgres-first; sqlite/mariadb sink parity out of v1 scope.

**Fixtures:** one small synthetic agent-session JSONL (multi-topic, tool calls, a deliberate knowledge-update contradiction pair) under `tests/fixtures/` — **not** the `docs/samples/*-*.md` corpus. Reused across unit + integration.

## 8. Success Criteria

- **SC-1** `chunkshop.memory.stage_event/stage_events/ensure_staging_table/prune_staging` exist, append-only, idempotent on `event_id`. (Unit + integration.)
- **SC-2** `SessionStagingSource`, `SessionEpisodeFramer`, consolidation chunker, `MemorySink` exist as the new providers, each registered in its pydantic union + loader, following chunkshop's "one file + one branch + one model" convention.
- **SC-3** Consolidation intelligence is a user-wired callable; the zero-network extractive default works with no LLM; an LLM callable can be wired without core changes.
- **SC-4** `memory/realtime.yaml` then `memory/consolidate.yaml` over a seeded staging fixture produces episode + fact rows; consolidated supersedes provisional for the session; contradicted prior facts are soft-invalidated.
- **SC-5** The store is consumable by pg-raggraph Pattern C with **zero shim** — the contract test passes.
- **SC-6** Operational invariants O1–O8 each have a passing test (notably O1 rebuild, O3 crash-resume, O4 per-session resilience).
- **SC-7** No regression in existing chunkshop tests; new code respects `extra="forbid"`, identifier-safety regex, and zero-network core.

## 9. Out of Scope (explicit)

Capture/forward hooks (D2); the scheduler itself; SP-B realtime reader / graph traversal / hybrid+rerank (pg-raggraph's job); SP-D eval harness; full Zep-style bi-temporal (transaction-time dimension); a memory graph in chunkshop core; embedding-based topic-shift segmentation (deferred, needs a model); sqlite/mariadb/clickhouse memory-sink parity.

## 10. References

- Research: `skill-output/research-and-design/Research-Report-agentic-memory-chunking-retrieval.md`
- pg-raggraph coupling: `/home/yonk/yonk-tools/pg-raggraph/docs/cookbook/chunkshop-integration.md` (Pattern C), `pg-raggraph/src/pg_raggraph/sql/schema.sql` (`facts` table), `ingest_records()` at `pg-raggraph/src/pg_raggraph/__init__.py`
- chunkshop contracts: `sources/base.py`, `framers/base.py`, `chunkers/base.py`, `chunkers/summary_embed.py`, `extractors/result.py`, `sinks/pg.py`, `runner.py`, `pipeline.py`, `config.py`
