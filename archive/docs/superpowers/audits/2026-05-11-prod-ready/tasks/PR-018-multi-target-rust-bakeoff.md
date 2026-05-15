# PR-018 — Multi-target Rust bakeoff (cross-backend comparison)

**Priority:** P4
**Effort:** M
**Dependencies:** none
**GAP-IDs:** GAP-001 (also Wave-2 follow-up #7)

## Problem

Python's `chunkshop bakeoff` accepts a `targets:` list and produces a leaderboard across all 4 backends side-by-side. Rust's `chunkshop-rs bakeoff` accepts only a single `target:` block with the legacy `schema:` field — PG-only by design as of v0.4.0.

## Solution

Extend Rust's `BakeoffConfig` to accept `targets:` (plural) using the v0.4.0 `database:` shape:

```yaml
targets:
  - { type: postgres,   dsn_env: PG_DSN,  database: bk_pg }
  - { type: mariadb,    dsn_env: MD_DSN,  database: bk_md }
  - { type: sqlite,     dsn_env: SQ_PATH, database: ignored }
  - { type: clickhouse, dsn_env: CH_DSN,  database: bk_ch }
```

Mirror Python's bakeoff runner: per backend, run the chunker × embedder matrix, then emit a unified report comparing MRR + ingest_s + query_s side by side.

### Steps

1. Change `BakeoffTargetConfig` to `Vec<BakeoffTargetEntry>` with the per-engine target shape (same as `TargetConfig::*` variants).
2. Update `bakeoff/runner.rs` to loop over targets, calling `run_cell` per (target × chunker × embedder).
3. Update `bakeoff/output.rs` to emit the cross-backend comparison table (mirror Python's `report.md` layout).
4. Drop the legacy `target.schema:` field in v0.5; keep it as a deprecated alias for v0.4.x.

## Acceptance Criteria

- [ ] `chunkshop-rs bakeoff --config docs/samples/bakeoff-v04/bakeoff-v04.yaml --yes` produces a report covering all 4 backends.
- [ ] The Python and Rust bakeoffs produce comparable reports for the same input (same MRR per (chunker, embedder), different ingest/query wall time across implementations is expected).
- [ ] Existing single-target `target:` YAML still parses (deprecated alias for `targets: [...]` of length 1).

## Risk if Skipped

Cross-backend bakeoff comparison stays Python-only. Rust-only deployments lose the "leaderboard across backends" feature.
