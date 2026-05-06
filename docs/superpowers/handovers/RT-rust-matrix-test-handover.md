# RT — Rust 16-Cell Cross-Backend Matrix Test — Handover (Wave 3)

**Status:** Cannot start until R2, R3, AND R4 are all merged to `experimental/v4-modular-backends`. This is the only Wave 3 sub-project.

**Worktree to create:** `/home/yonk/yonk-tools/chunkshop-rt-matrix/`

**Branch:** `experimental/v4-rust-matrix-test` off `experimental/v4-modular-backends`

**Estimated size:** 1 session.

---

## Pre-condition gate (CRITICAL)

Before doing anything, verify all four backends are present:

```bash
cd /home/yonk/yonk-tools/chunkshop-v4
git log --oneline experimental/v4-modular-backends | grep -E "Merge (R[1-4]|RT)"
# expect at minimum:
#   Merge R4: ...
#   Merge R3: ...
#   Merge R2: ...
#   Merge R1: Rust modular backends skeleton

ls rust/chunkshop/src/backends/
# expect: base.rs, mod.rs, postgres.rs, mariadb.rs, sqlite.rs, clickhouse.rs

ls rust/chunkshop/src/sinks/
# expect: base.rs, mod.rs, pg.rs, mariadb.rs, sqlite.rs, clickhouse.rs

ls rust/chunkshop/src/sources/
# expect: base.rs, mod.rs, files.rs, http.rs, json_corpus.rs, pg_table.rs, s3.rs,
#         mariadb_table.rs, sqlite_table.rs, clickhouse_table.rs
```

**If any backend is missing, STOP.** RT exists to validate the matrix; running it before all four backends are present is meaningless. Surface what's missing and pick up the corresponding sub-project's handover instead.

If R4 (Rust ClickHouse) is intentionally deferred — confirm with the user. A "Wave 3 minus R4" version of RT (3 backends × 3 sources = 9 cells, not 16) is valid as an interim deliverable but should be flagged in the mission brief as such.

---

## Session-startup checklist

```bash
# 1. Confirm R2/R3/R4 all merged (see gate above)
cd /home/yonk/yonk-tools/chunkshop-v4
git log --oneline experimental/v4-modular-backends | head -20

# 2. Create the worktree
cd /home/yonk/yonk-tools
git -C chunkshop-v4 worktree add ../chunkshop-rt-matrix -b experimental/v4-rust-matrix-test

# 3. Bring up all DBs needed for the matrix
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml up -d
# expect: postgres, mariadb, clickhouse containers up
# (sqlite needs no container)

# 4. Verify all DSN env vars
echo "PG:        $CHUNKSHOP_TEST_DSN"
echo "MariaDB:   $CHUNKSHOP_TEST_DSN_MARIADB"
echo "ClickHouse: $CHUNKSHOP_TEST_DSN_CH"
# any unset → that row of the matrix will skip; that's acceptable but record it

# 5. Confirm baseline tests pass before adding the matrix
cd /home/yonk/yonk-tools/chunkshop-rt-matrix/rust
cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3
# expect: a much larger number than 126 — depends on what R2/R3/R4 added.
# Capture the number — it's your baseline. RT must not regress it.
```

---

## Mission

Mirror the Python 16-cell cross-backend matrix test (`python/tests/chunkshop/test_cross_backend_matrix.py`, 217 lines) on the Rust side. Validate that any of 4 sources (Files, JsonCorpus, MariadbTable, SqliteTable, PgTable, plus optionally ClickhouseTable depending on scope) feeds correctly into any of 4 sinks (Pg, Mariadb, Sqlite, Clickhouse).

The matrix is the integration-level proof that the Backend/Sink/Source trait surface from R1 actually composes — that R2/R3/R4 didn't break each other and that vectors round-trip across backends.

## What the matrix actually tests

For each `(source, sink)` cell:

1. Construct the source from a fixture
2. Iterate documents
3. Chunk + embed (use a deterministic embedder — `fastembed` with a fixed seed, or skip embedding and use a stub)
4. Write to the sink
5. Read back via `count_docs` / `query_top_k`
6. Assert: count matches expected, top_k ordering is stable, distances are within tolerance

Per-cell DSN reachability — skip with a clear message if the DSN isn't set, mirroring the Python pattern at `test_cross_backend_matrix.py`. Never fail a cell because a DSN is missing — only fail when a present DSN produces wrong behavior.

## Files you will create

| File | Mirrors | Approx lines |
|---|---|---|
| `rust/chunkshop/tests/cross_backend_matrix.rs` | `python/tests/chunkshop/test_cross_backend_matrix.py` (217 lines) | 250–350 |
| `rust/chunkshop/tests/parity-fixtures/matrix-corpus.json` (optional) | `python/tests/chunkshop/fixtures/matrix-corpus.json` if it exists | small |

## Files you will probably NOT need to modify

The whole point of RT is that the matrix exercises existing public API. If you find yourself modifying `lib.rs`, `mod.rs` files, or any `backends/*` / `sinks/*` / `sources/*` source — STOP. That's R1/R2/R3/R4 work bleeding into RT. Surface the problem; don't fold it into RT.

## Open architectural questions to settle first

From roadmap §9:

1. **Test harness pattern: one giant file or one file per row.**
   - One file (`cross_backend_matrix.rs`) is what Python does. Rust supports parameterized tests via `rstest` or via straight loops in a single test fn. Single-file is recommended for parity.
   - Default recommendation: one file with a `#[test]` per cell (16 tests, ignored if DSN missing) generated by a macro or written out longhand.

2. **Per-DSN skip discipline.** Match Python exactly: at the top of each cell, check the DSN env var; if unset, `eprintln!` a clear "skipping cell X×Y because $VAR unset" and `return`. **Don't use `#[ignore]`** — that would hide the cell from default `cargo test` runs and defeat the purpose.

3. **Fixture corpus.** Reuse `docs/samples/handbook-*.md` (the established sample set) or build a small synthetic corpus inside `tests/parity-fixtures/`. Default: synthetic fixture, ~5 short docs, hardcoded inline in the test file. Avoids file-IO timing variance.

4. **Cargo features.** Roadmap question: `cargo test --features mariadb,sqlite,clickhouse` gating, or always-on with DSN-skip?
   - Default: always-on. R2/R3/R4 should land their backends as default-on dependencies (no feature flags). Skip-on-missing-DSN is the runtime gate.

## Acceptance criteria

| ID | Criterion | Verified by |
|---|---|---|
| **RT-SC-001** | A single test file `cross_backend_matrix.rs` runs all S×T cells (S = sources with DSN, T = sinks with DSN) | `cargo test --test cross_backend_matrix` |
| **RT-SC-002** | Each cell skips with a clear message if its DSN is unset; never silently passes | manual unset env vars; verify skip output |
| **RT-SC-003** | All cells with DSNs reachable PASS | full matrix run on a dev box with all 3 DSNs set |
| **RT-SC-004** | No regression in the baseline test suite (whatever count R4 left) | full `cargo test -p chunkshop-rs` |
| **RT-SC-005** | The matrix runs in under 3 minutes wall time on a developer laptop | manual timing check |
| **RT-SC-006** | The Python and Rust matrices have the same cell layout (same source × sink labels in the same order) | side-by-side review of the two test files |
| **V4-SC-007 (roadmap-level)** | Tag `v0.4.0` (or chosen name) cuts on `experimental/v4-modular-backends` after RT merges | `git tag` check |

## Recommended workflow

1. **Read `python/tests/chunkshop/test_cross_backend_matrix.py` cover to cover.** It's 217 lines. Understand its skip pattern, its assertion style, its fixture structure.
2. **`/mission-brief`** — lock RT-SC-001 through RT-SC-006. Capture the harness-pattern + features decision in Constraints.
3. **`superpowers:writing-plans`** — short plan, 6–10 tasks. Most of the work is shaping the harness; the per-cell test bodies are formulaic.
4. **Execute directly** — RT is mostly mechanical (the test logic per cell is identical across cells, just parameterized differently). Skip the implementer-subagent ceremony entirely; this is a Write/Edit job.

## Lessons from R1/R2/R3 — fold these in

- Package is `chunkshop-rs`. Always `cargo test -p chunkshop-rs`.
- `cargo test --test cross_backend_matrix` runs only the new file. Use that for fast iteration during development, then full sweep at the end.
- DSN env vars: `$CHUNKSHOP_TEST_DSN` (PG), `$CHUNKSHOP_TEST_DSN_MARIADB`, `$CHUNKSHOP_TEST_DSN_CH`. SQLite needs no DSN — uses `:memory:` or `tempfile::tempdir()`.
- The cross-language parity fixtures (`tests/parity-fixtures/dialect-*.json`) are SEPARATE from RT. Don't confuse them. Those test single-method outputs; RT tests end-to-end pipelines.

## Watch-outs specific to RT

- **Determinism.** If you use a real embedder (`fastembed` with the actual ONNX model), it's slow and can be flaky across CI environments. Two options:
  - Mock embedder that returns a deterministic vector per input (e.g. `[hash(text) as f32; 384]`). Recommended for the matrix.
  - Real embedder, accept ~30s per cell. Probably blows RT-SC-005 (3-minute budget).
- **Cleanup between cells.** Each test cell must drop its DBs/tables on exit, otherwise cell N+1 sees cell N's leftovers and assertions get weird. Mirror Python's teardown patterns.
- **`tags_array_type_ddl` divergence is real.** PG uses `text[]`, MariaDB and SQLite use `JSON` / `TEXT`. Round-tripping tags through different backends is a place where serialization parity bugs hide. Add a tag round-trip assertion to at least one cell per sink.
- **Top-k ordering can differ across backends.** Cosine distance computation is identical mathematically, but float precision and tie-breaking differs. Use `assert!((dist1 - dist2).abs() < 1e-4)` and compare ordered IDs, not exact distances.
- **ClickHouse `delete_orphans` is a no-op + warn pattern in Python.** Verify the Rust side matches before asserting cell-level invariants.

## Definition of done

- All RT-SC-XXX criteria met
- Mission brief drift checkpoints green
- Full `cargo test -p chunkshop-rs` clean, no regressions vs the post-R4 baseline
- Branch ready to merge `--no-ff` into `experimental/v4-modular-backends`
- After merge: V4-SC-001 (all sub-projects merged) is satisfied; V4-SC-003 (Rust matrix passes) is satisfied; ready to cut the v0.4.0 tag

## After RT merges

This is the last Wave 3 sub-project. v0.4.0 acceptance criteria become satisfiable:

- V4-SC-001 — all sub-project merge commits visible
- V4-SC-002 — Python matrix passes (verify with `uv run pytest -q python/tests/chunkshop/test_cross_backend_matrix.py`)
- V4-SC-003 — Rust matrix passes (this sub-project's deliverable)
- V4-SC-004 — no regressions on existing PG tests
- V4-SC-005 — `chunkshop --version` and `chunkshop-rs --version` (this is CLI-FIX from Wave 1; verify it's done)
- V4-SC-006 — YAML field harmonization (verify legacy-form YAMLs reject cleanly on both languages)
- V4-SC-007 — tag cut (separate explicit step; ask the user before tagging)
