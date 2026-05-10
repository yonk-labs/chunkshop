---
mission: RT — Rust 16-cell cross-backend matrix test
created: 2026-05-10
status: active
---

## TL;DR
RT is the Wave-3 deliverable that closes v0.4.0: a single Rust integration test
file mirroring `python/tests/chunkshop/test_cross_backend_matrix.py` so 4 DB
sources × 4 DB sinks = 16 cells round-trip a real chunkshop pipeline via
`run_cell`. Per-DSN skip with `eprintln!` (never `#[ignore]`); whole matrix
must finish in under 3 minutes wall time and add zero regressions to the 251
baseline.

## Purpose
R1's modular trait surface (`Backend`, `BackendDialect`, `BackendConn`, `Sink`,
`AnyBackend`, `AnySink`, `AnySource`) only proves the contract is *typeable*.
The matrix is the integration-level proof that the four backends shipped in
R1/R2/R3/R4 actually *compose* — that any source feeds any sink, vectors
round-trip, and no two backends silently broke each other on the integration
branch. Without this, V4-SC-003 ("Rust matrix passes") cannot be ticked, and
v0.4.0 cannot be tagged.

## Desired Outcome

- A single new test file at `rust/chunkshop/tests/cross_backend_matrix.rs` that
  drives 16 end-to-end cells through `chunkshop::runner::run_cell`.
- Each cell seeds a 1-doc fixture into its source backend, runs the pipeline,
  asserts chunks landed in the sink (count + non-zero), and cleans up its own
  databases on exit.
- Per-DSN skip messages via `eprintln!` so missing infrastructure produces
  clear "skipping cell X×Y because $VAR unset" output, never silent pass.
- Branch `experimental/v4-rust-matrix-test` ready to merge `--no-ff` into
  `experimental/v4-modular-backends`.
- No edits outside `rust/chunkshop/tests/cross_backend_matrix.rs` (and an
  optional `Cargo.toml` dep bump only if strictly required — measure first).

## Success Criteria

- **RT-SC-001:** A single test file `rust/chunkshop/tests/cross_backend_matrix.rs`
  contains 16 `#[tokio::test]` functions (one per source × sink combination,
  written longhand for debuggability) that all run on `cargo test --test
  cross_backend_matrix`.
- **RT-SC-002:** When a cell's source DSN env var or sink DSN env var is unset,
  the cell prints `eprintln!("skipping {src}->{sink}: ${var} unset")` and
  returns OK — never `#[ignore]`, never silent pass.
- **RT-SC-003:** With `CHUNKSHOP_TEST_DSN`, `CHUNKSHOP_TEST_DSN_MARIADB`, and
  `CHUNKSHOP_TEST_DSN_CH` all set and reachable, all 16 cells PASS:
  `result.docs_processed == 1`, `result.chunks_written > 0`, and the sink
  contains exactly `result.chunks_written` rows for the seeded `doc_id`.
- **RT-SC-004:** Full `cargo test -p chunkshop-rs` is green with `CHUNKSHOP_PY`
  + all 3 DSNs set: 251 (baseline) + 16 (new) = 267 passing, 0 failed, 0
  unexpected ignores.
- **RT-SC-005:** `time cargo test --test cross_backend_matrix` wall time is
  under 180 seconds on the developer laptop, measured at the verification
  step. If exceeded, fall back to the deterministic-mock embedder approach
  (out of scope on first pass; flagged as the documented mitigation).
- **RT-SC-006:** The Rust cell layout matches the Python file's:
  `SOURCE_KINDS = ["pg_table", "mariadb_table", "sqlite_table", "clickhouse_table"]`
  × `SINK_KINDS = ["postgres", "mariadb", "sqlite", "clickhouse"]`, in the
  same order. A side-by-side `diff` of the matrix labels reveals identical
  ordering.

## Constraints

- **NEVER** modify `rust/chunkshop/src/backends/*`, `sinks/*`, `sources/*`,
  `embedder.rs`, `runner.rs`, `pipeline.rs`, `lib.rs`, or any `mod.rs`. RT
  exercises existing public API only. Any urge to edit those is the signal
  that R1/R2/R3/R4 work is bleeding into RT — STOP and surface the gap to the
  user.
- **NEVER** use `#[ignore]` to mark cells. Per-DSN skip is runtime via
  `eprintln!` + `return Ok(())`, so default `cargo test` shows skip output.
- **ALWAYS** use real fastembed `Xenova/bge-small-en-v1.5-int8` (dim=384,
  threads=2, batch_size=8) to mirror the Python matrix exactly — model is
  cached locally from prior R1/R2 work. Measure wall time at RT-SC-005
  verification; only escalate to deterministic mock if budget blown.
- **ALWAYS** clean up per-cell databases (`xbm_src_{src}_{sink}`,
  `xbm_sink_{src}_{sink}`) in a `finally`-style block so cell N+1 starts
  clean.
- **ALWAYS** keep per-cell `chunker = sentence_aware{max_chars: 200, min_chars: 50}`,
  `framer = identity`, `extractor = none`, `mode = overwrite`,
  `source_tag = "xbm"`, `hnsw = false`, single seeded doc with body
  `"Hello world. This is sentence two. " * 10` — direct mirror of Python.
- **ASK FIRST** before pushing the branch, before tagging v0.4.0, or before
  installing pymysql into the worktree's venv. All Wave 1/2 work is
  local-only and the user has been clear about preserving that discipline.

## Testing Requirements

### Functional Testing

- **SC-001 → harness layout:** `cargo test --test cross_backend_matrix --
  --list 2>&1 | grep -c "^test "` reports exactly 16 (or `cargo test
  cross_backend_matrix 2>&1 | grep "^test result"` reports `16 passed; 0
  failed; 0 ignored` when all DSNs set).
- **SC-002 → skip behavior:** Run the matrix once with each DSN unset in turn
  (`unset CHUNKSHOP_TEST_DSN_CH; cargo test --test cross_backend_matrix
  2>&1 | grep skipping`). Expect cells touching that backend to print the
  skip message and exit OK.
- **SC-003 → end-to-end correctness:** Within each cell:
  `assert!(result.docs_processed == 1)`,
  `assert!(result.chunks_written > 0)`, and a sink-side row count via the
  matching backend's connect path equals `result.chunks_written`.
- **SC-004 → non-regression:** Full `cargo test -p chunkshop-rs` reports 267
  passed / 0 failed (251 baseline + 16 new).
- **SC-005 → wall-time budget:** `/usr/bin/time -v cargo test --test
  cross_backend_matrix` reports < 180s elapsed on the developer laptop.
- **SC-006 → label parity:** `diff <(grep -oE 'fn cell_[a-z_]+_to_[a-z_]+'
  cross_backend_matrix.rs | sort) <(grep -E '"(pg|mariadb|sqlite|clickhouse)_table"|"(postgres|mariadb|sqlite|clickhouse)"' test_cross_backend_matrix.py)`
  shows the same source × sink label set.

### E2E / User Simulation Testing

N/A — RT IS the e2e/user-simulation test for chunkshop's Rust modular
backends. There is no UI or CLI surface RT validates beyond what `cargo
test` itself exercises. The "user" here is the developer who runs
`cargo test -p chunkshop-rs` on the integration branch and trusts the
matrix to be the regression net. Re-running `/user-test` or
`/browser-test` does not apply to a Rust library at this level.

## Drift Checkpoints

- **DC-001:** After drafting the harness skeleton (cell-iteration loop or
  16 longhand functions chosen) — re-read this brief and verify SC-001
  (single file), SC-006 (same labels as Python). If the harness has drifted
  toward macro-generated tests or split files, stop and reset.
- **DC-002:** After the first cell compiles and passes (`cargo test --test
  cross_backend_matrix cell_pg_table_to_postgres`) — re-read this brief and
  verify SC-002 (skip pattern uses `eprintln!` + return, NOT `#[ignore]`)
  and SC-003 (assertions cover docs/chunks + sink count). Confirm the
  pattern before replicating across the other 15 cells.
- **DC-003:** After all 16 cells pass — re-read this brief and run the full
  suite. Verify SC-004 (no regression vs 251 baseline) and SC-005 (wall
  time < 180s). If wall time exceeded, escalate per the constraint about
  the deterministic-mock fallback BEFORE proceeding to merge.
- **DC-FINAL:** Before announcing RT complete — re-read this brief and
  confirm every SC-001..006 has cited evidence (test output, timings, diff
  results). Pause before any push, merge, or tag — those require explicit
  user authorization per the constraint.

## Out of Scope

- Modifying any production source file (`src/**/*.rs`). RT is purely
  additive in `tests/`.
- Cargo.toml changes beyond optional new dev-deps strictly required for the
  test (and only if measurement shows them needed).
- Triaging the 8 deferred Wave-2 follow-ups in `project_r2_followups.md`.
  Those are pre-tag triage, not RT scope.
- Adding a deterministic mock embedder (a new `EmbedderConfig` variant,
  `MockEmbedder` struct, runner branch, etc.). Stays out unless RT-SC-005
  measurement blows the 3-minute budget; if it does, surface the
  measurement and ASK before adding it.
- Pushing `experimental/v4-rust-matrix-test` to a remote.
- Cutting the `v0.4.0` tag. V4-SC-007 is a separate explicit user step
  AFTER RT merges.
- "Cleaning up" the pre-existing extractor unused-imports warning visible
  during the baseline run. That's a Wave-2 follow-up, not RT.
- "Fixing" the worktree's missing `pymysql` by editing the venv. The
  workaround is `CHUNKSHOP_PY=/path/to/integration/venv/bin/python` at
  test-run time; document it but don't modify the venv.
- Splitting the matrix into per-row test files, generating cells via macro,
  or factoring a generic `run_one_cell(src, sink)` helper that erases the
  longhand cells. Longhand is mandated for failure-debuggability.
