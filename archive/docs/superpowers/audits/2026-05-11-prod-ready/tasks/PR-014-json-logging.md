# PR-014 — Optional structured (JSON) logging

**Priority:** P3
**Effort:** M
**Dependencies:** PR-006 (Python `logging` migration)
**GAP-IDs:** GAP-010

## Problem

Both languages emit human-readable log lines. Users running chunkshop in a job-runner (Airflow, Dagster, k8s CronJob) that ships logs to an aggregator (Datadog, Loki, CloudWatch) get unstructured strings.

## Solution

Add a YAML field `runtime.log_format` accepting `"text"` (default) or `"json"`. When `"json"`:

- Python: switch the `logging` handler to `python-json-logger` formatter.
- Rust: switch `tracing-subscriber` to its JSON formatter.

## Acceptance Criteria

- [ ] `runtime.log_format: json` in YAML produces one JSON object per log line.
- [ ] Keys include: `timestamp`, `level`, `module`, `message`, plus structured fields where used.
- [ ] Default (omit or `"text"`) is unchanged.
- [ ] Documented in the per-runtime section of `docs/architecture.md`.

## Risk if Skipped

Users running chunkshop in production deployment-style workflows have to grep / awk progress lines. Friction, not failure.
