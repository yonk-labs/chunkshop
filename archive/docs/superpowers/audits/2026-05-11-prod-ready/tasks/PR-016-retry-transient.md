# PR-016 — Retry-with-backoff on transient connection errors

**Priority:** P4
**Effort:** M
**Dependencies:** none
**GAP-IDs:** GAP-011

## Problem

A transient TCP reset or temporary unavailability mid-ingest fails the cell. Reruns are idempotent (primary key `{doc_id}::{seq_num}`), so recovery is just `chunkshop ingest --config x.yaml` again. But the failure is loud and forces operator intervention.

## Solution

Wrap sink connect + `write_document` in a retry loop with exponential backoff. Distinguish:

- **Transient** (retry): TCP reset, connection-refused, server-temp-unavailable, timeout.
- **Logical** (no retry): auth failure, schema mismatch, dim mismatch, syntax error.

Config knob: `runtime.retry: { max_attempts: 3, base_delay_ms: 200, max_delay_ms: 5000 }`.

## Acceptance Criteria

- [ ] Transient errors trigger configured retries before failing the cell.
- [ ] Logical errors fail immediately.
- [ ] Documented; opt-in via YAML.

## Risk if Skipped

Operator pages on every network blip. Mid-impact.
