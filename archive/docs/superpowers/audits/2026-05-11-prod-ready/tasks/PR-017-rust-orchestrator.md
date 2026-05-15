# PR-017 — Rust orchestrator (multi-cell subprocess fan-out)

**Priority:** P4
**Effort:** XL (~1 week)
**Dependencies:** none
**GAP-IDs:** GAP-002

## Problem

Python has `chunkshop orchestrate --concurrency N` for parallel multi-cell subprocess fan-out. Rust has no equivalent — users running large multi-cell workloads must shell out to Python orchestrator or roll their own.

## Solution

Port the orchestrator to Rust:

- Tokio-based task pool with `Command::spawn` for subprocess management.
- Same checkpoint cadence (60s / 120s / 300s / 600s).
- Same SIGTERM-process-group cleanup on overall timeout.
- Same JSON summary output shape.

Cargo subcommand:
```bash
chunkshop-rs orchestrate --concurrency 4 --config-dir ./cells/
```

## Acceptance Criteria

- [ ] `chunkshop-rs orchestrate` exists and runs.
- [ ] Behavior parity with Python orchestrator on a 10-cell test corpus.
- [ ] Same checkpoint cadence + JSON summary shape.

## Risk if Skipped

Rust users running heavy workloads either shell out to Python or write their own driver. Not a data correctness issue.
