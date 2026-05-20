# MemorySink — architecture

The agent-memory write side of chunkshop. `MemorySink` extends `PgSink`
with the two-tier consolidation semantics the SP-A spec defines and
keeps the read shape consumable by anything that speaks plain Postgres
(or, specifically, by pg-raggraph through the `pre_chunked` seam).

This doc is the user-facing companion to the design spec at
[`docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`](../superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md).
The spec is the *why*; this is the *what's-there-and-how-to-use-it*.

For the Rust port (RM-A), the structure is byte-identical at the schema
level — same `agent_memory.memory` columns, same `event_id` derivation,
same operational invariants. The differences are noted inline.

## TL;DR

- Your agent calls `chunkshop.memory.stage_event(...)` after every turn.
- One scheduled job (`memory/realtime.yaml`) runs every minute and
  writes `tier='provisional'` rows so a fresh agent reply has memory
  to read within ~1 minute.
- A second scheduled job (`memory/consolidate.yaml`) runs nightly,
  segments quiet sessions into episodes, extracts SPO facts via a
  consolidator you wire up, and **supersedes** the provisional rows
  with `tier='consolidated'` ones.
- The store is a single Postgres table you can read with plain SQL, or
  bridge to pg-raggraph with `chunkshop.memory.read_pre_chunked(dsn)`,
  or point any vector-search tool at directly.
- You bring the scheduler. chunkshop does not run a daemon.

## Data flow

```
┌──────────────────────┐
│  Your agent runtime  │
└──────────┬───────────┘
           │ stage_event(dsn, session_id="s1", role="user",
           │             content="...", seq=42)
           ▼
┌─────────────────────────────────────────────────────────────┐
│  public.chunkshop_staging   (chunkshop owns this DDL)        │
│  ─────────────────────────                                   │
│  event_id  text PRIMARY KEY  ← deterministic sha1            │
│  session_id, seq, role, content, tool, outcome               │
│  event_ts timestamptz, staged_at timestamptz DEFAULT now()   │
│  consumed jsonb {realtime: "<wm>", consolidated: "<wm>"}     │
│  metadata jsonb                                              │
│  + indices on (session_id, seq) and (staged_at)              │
└────────────┬───────────────────────────────┬────────────────┘
             │                                │
       (every ~1 min)                  (nightly, hourly,
        realtime cell                   whatever cadence)
             │                          consolidate cell
             ▼                                ▼
┌─────────────────────────────────────────────────────────────┐
│  agent_memory.memory   (the read surface)                    │
│  ──────────────────────                                      │
│  id = "{namespace}::{doc_id}::{seq_num}"   PRIMARY KEY        │
│  doc_id = session_id, seq_num int, source text                │
│                                                              │
│  tier ∈ {provisional, consolidated}    ← O2 selector         │
│  kind ∈ {episode, fact}                ← shape of the row    │
│                                                              │
│  Promoted columns (queryable, indexable):                    │
│    namespace, recorded_at, session_id                        │
│    subject, predicate, object       (kind='fact' rows)       │
│    support_span, confidence, extractor                       │
│    effective_from, effective_to     (bi-temporal)            │
│    retracted, retracted_at          (soft-invalidate)        │
│                                                              │
│  Canonical chunkshop columns:                                │
│    original_content, embedded_content, embedding             │
│    tags text[], metadata jsonb                               │
└─────────────────────────────────────────────────────────────┘
             ▲
             │ SELECT ... WHERE tier='consolidated' AND retracted=false
             │
       Your reader: pg-raggraph, custom SQL, LangChain,
       LlamaIndex, anything that speaks Postgres
```

## The two tiers

The realtime/consolidate split solves a real tension: agents need
**fresh** memory for the next reply, but **good** memory takes time to
extract. Doing both jobs in one pass means either the realtime path is
slow or the consolidated path is shallow. The two-cell pattern keeps
both fast and good by writing them on different schedules.

| | `tier='provisional'` | `tier='consolidated'` |
|---|---|---|
| Written by | realtime cell | consolidate cell |
| Cadence | every ~1 minute | hourly / nightly |
| Framer | `identity` (no segmentation) | `session_episode` (gap/turn/word/tool boundaries) |
| Chunker | `fixed_overlap` (cheap word-window) | `consolidation` (episode chunks + per-triple fact chunks) |
| Consolidator runs? | no | yes — your wired SPO extractor |
| Latency budget | <1 s per turn | as long as it needs |
| Survives reconsolidation? | no — `supersede=true` on consolidated DELETEs it | yes (until a later run supersedes again) |

When a reader has to pick between both tiers for the same session, **it
should prefer `consolidated`**. That's the **O2 invariant**: any
consumer of `agent_memory.memory` must filter by tier or accept the
freshest tier wins. `chunkshop.memory.read_pre_chunked()` defaults to
`tier='consolidated'`; LangChain/LlamaIndex/etc. should set their
metadata filter explicitly.

## Row identity: namespaces matter

The PK is `id = "{namespace}::{doc_id}::{seq_num}"`. Two namespaces
writing memory for the same session don't collide. That's how you run a
multi-tenant agent — every tenant gets its own namespace, the same
`session_id="s1"` lands as `tenant_a::s1::0` and `tenant_b::s1::0`.

Supersede DELETEs are scoped by `source` (which usually equals
namespace), so reconsolidating tenant A's memory never touches tenant
B's rows for the same session. Tested as
[`consolidated_supersedes_provisional_scoped_by_source`](../../python/tests/chunkshop/test_memory_sink_supersede.py).

## Late events and rebuild (O1)

> **The bug that almost shipped**: my first implementation filtered the
> consolidate-mode `WHERE` at row granularity. A late event arriving
> after a session was already consolidated would re-select **only that
> row**. `MemorySink`'s destructive supersede would then DELETE the
> prior consolidated memory for that session and replace it with a
> consolidation of just the late fragment. Every follow-up message
> erased the memory of the session it was supposed to extend.
>
> The fix (commit `49861dc`) makes the WHERE **session-level**: when a
> session has any new event, *all* of its rows are reselected, so
> supersede replaces the old consolidation with a complete new one. The
> regression-guard test
> ([`test_o1_late_event_rebuilds_from_full_staging`](../../python/tests/chunkshop/test_memory_resilience.py))
> is the reason this can't recur.
>
> In Rust this invariant was baked in from Task 5 commit 1 — the test
> exists before the WHERE does, so reverting to row-level isn't even
> reachable as a regression.

The practical implication for you: **don't worry about late events.**
Stage them whenever they arrive. The consolidate cell handles
late-arrival rebuild correctly. The only thing you need is to make sure
the cell runs occasionally after every burst of staging.

## Crash safety (O3)

Two pieces:

1. **`MemorySink.write_document` commits per-document**, in a short-
   lived transaction. A mid-run crash leaves processed sessions
   consolidated, in-flight session atomic-or-not-committed, the rest
   pending.
2. **The source's watermark advance is deferred** to a hook
   (`commit_processed()` in Rust, generator-yield semantics in Python)
   that runs only after the per-doc write loop succeeds. A mid-loop
   crash leaves the watermark unadvanced, so the next run reselects the
   same sessions and resumes cleanly.

Combined, this means: rerun-after-crash is safe. The next run picks up
where the previous one stopped. Idempotent on doc id; ON-CONFLICT
DO NOTHING on staging.

## The consolidator seam

The consolidate cell calls a *consolidator* once per episode. That's
where SPO facts get extracted. v1 ships a **zero-network extractive
default** in both Python and Rust — selects sentences for a summary,
emits no facts. That gets you episode rows immediately; facts require a
richer extractor.

Wiring an LLM (or any structured extractor):

**Python** — name a callable in the consolidate YAML:

```yaml
chunker:
  type: consolidation
  base: { type: sentence_aware, max_chars: 2000 }
  consolidator:
    mode: callable
    module: my_app.memory.consolidators
    function: extract_with_claude
  fact_max_chars: 1200
```

```python
# my_app/memory/consolidators.py
def extract_with_claude(episode: dict) -> dict:
    """
    Input:  {text, frame_seq, session_id, episode_start_ts, episode_end_ts}
    Output: {summary: str, facts: [{subject, predicate, object,
                                     support_span, confidence}, ...]}
    """
    # ... call your model of choice, structured-output ...
    return {"summary": ..., "facts": [...]}
```

**Rust** — implement the `Consolidator` trait at compile time:

```rust
use chunkshop::consolidators::{Consolidator, EpisodeInput,
                                ConsolidationOutput, FactTriple};

pub struct ClaudeConsolidator { /* ... */ }

impl Consolidator for ClaudeConsolidator {
    fn consolidate(&self, ep: &EpisodeInput<'_>) -> anyhow::Result<ConsolidationOutput> {
        // ... call your model ...
        Ok(ConsolidationOutput {
            summary: "...".into(),
            facts: vec![FactTriple { /* ... */ }],
        })
    }
    fn mode(&self) -> &'static str { "claude" }
}
```

**O4 resilience**: if the consolidator returns `Err` (LLM timeout,
malformed output, anything), the chunker emits the **episode chunk
only**, zero facts, with `consolidation_error=<msg>` stamped in metadata.
Never raises. One poisoned session never blocks the nightly run.

## What the row contract guarantees pg-raggraph (and others)

The promoted column set on `agent_memory.memory` is the **pg-raggraph
fact contract**. SP-A guarantees these columns exist with these names
and types so a consumer can read without a shim:

| Column | Type | Used by |
|---|---|---|
| `subject` | text | `kind='fact'` SPO triple subject |
| `predicate` | text | SPO triple predicate |
| `object` | text | SPO triple object |
| `support_span` | text | quote/excerpt the fact was extracted from |
| `confidence` | text | consolidator-supplied confidence score |
| `effective_from` | timestamptz | when the fact became true (caller's ts) |
| `effective_to` | timestamptz | when soft-invalidate retracted it |
| `retracted` | bool | true if a newer contradicting fact retracted this one |
| `retracted_at` | timestamptz | when `retracted` was set |
| `extractor` | text | which consolidator produced this fact (`extractive`, `claude`, …) |
| `namespace` | text | tenant scope |

There's a regression test in both Python (`test_pgraggraph_contract_columns_present`)
and Rust (`memory_e2e::e2e_stage_then_consolidate`) that asserts every
one of these is present after a consolidate run. Drift on either side
fails CI; the schema is the contract.

## What's deliberately **not** in chunkshop's scope

Per the SP-A spec §9:

- **No realtime graph reader.** Stay in chunkshop for ingest. Use
  pg-raggraph or your own SQL for retrieval/graph traversal. (SP-B in
  the spec's sub-project map; lives in pg-raggraph.)
- **No scheduler.** chunkshop is a batch CLI. cron / systemd /
  k8s CronJob / Airflow / in-process loop — your choice. See
  [`docs/samples/memory-scheduling/`](../samples/memory-scheduling/).
- **No capture/forward hooks.** Your agent calls `stage_event`
  directly. The staging API is the seam.
- **No LLM eval harness.** SP-D in the spec; separate scope.
- **No cross-backend memory-sink parity.** Postgres only for v1.
  SQLite/MariaDB/ClickHouse MemorySinks would be RM-B+.

## Reading further

- Spec: [`docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`](../superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md)
- Rust port spec: [`docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md`](../superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md)
- Scheduling patterns: [`docs/samples/memory-scheduling/`](../samples/memory-scheduling/)
- pg-raggraph integration: [`docs/samples/memory-to-pgraggraph/`](../samples/memory-to-pgraggraph/)
- The brief usage note in [`docs/incremental.md`](../incremental.md) §Agent memory
