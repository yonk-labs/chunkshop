# PR-007 — Default ClickHouse to `ReplacingMergeTree` or warn on append without dedup

**Priority:** P2
**Effort:** S (~1 hour)
**Dependencies:** none
**GAP-IDs:** GAP-015

## Problem

ClickHouse's default `MergeTree` engine has no UPSERT. If a chunkshop user runs `mode: append` twice against the same CH cell, they get duplicate rows. This is documented in `docs/engines/clickhouse.md` but easy to miss in a hurried "let me just rerun this" workflow.

## Solution

Two options:

### Option A — Warn on append-without-dedup (recommended for v0.4.x)

When `mode: append` is used on a ClickHouse target without an explicit `engine: ReplacingMergeTree(...)`, emit a one-time process-level warning:

```
WARN: chunkshop::sinks::clickhouse:
  ClickHouse default MergeTree accumulates duplicate rows on re-ingest.
  Set engine: 'ReplacingMergeTree(created_at) ORDER BY (id)' for lazy
  dedup at merge time, or use mode: 'overwrite' for fresh ingests.
```

Mirror the existing `delete_orphans: true` one-time-warn pattern (`rust/chunkshop/src/sinks/clickhouse.rs:29`).

### Option B — Switch the default to `ReplacingMergeTree` (recommended for v0.5)

Change `default_engine` to `"ReplacingMergeTree(created_at) ORDER BY (id)"` in:
- `rust/chunkshop/src/config.rs:ClickhouseTargetConfig`
- `python/src/chunkshop/config.py:ClickhouseTarget`

Document as a breaking change in v0.5 — existing CH tables continue to work as-is; only `CREATE TABLE` defaults change.

## Recommendation

**Option A in v0.4.2.** Behavior unchanged for existing users; new users get a clear nudge. **Option B in v0.5** with the rest of the breaking changes batch.

## Acceptance Criteria

- [ ] Running `chunkshop ingest` with `mode: append` on a CH target without `engine:` emits the warning once per process.
- [ ] Running with explicit `engine: 'ReplacingMergeTree(created_at) ORDER BY (id)'` is silent.
- [ ] Running with `mode: overwrite` is silent.
- [ ] Test added: assert the warning fires under the trigger condition and doesn't fire under the explicit-engine condition.

## Risk if Skipped

Users hit duplicate-row accumulation after their second ingest run, file a bug, learn about ReplacingMergeTree from a thread instead of from the warning. Workflow friction; not a data-integrity bug (the docs are clear).
