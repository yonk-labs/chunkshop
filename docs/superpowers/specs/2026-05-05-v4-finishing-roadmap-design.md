# v0.4.0 Finishing Roadmap — Design Spec

**Date:** 2026-05-05
**Status:** Draft (brainstorming complete, pending writing-plans for each sub-project)
**Integration branch:** `experimental/v4-modular-backends`
**Worktree:** `/home/yonk/yonk-tools/chunkshop-v4`
**Predecessor spec:** [`2026-04-30-v4-modular-backends-design.md`](2026-04-30-v4-modular-backends-design.md)

## 1. Goal

Finish the v0.4.0 story by closing the gap between the Python `experimental/v4-modular-backends` work (already shipped — PG refactor + MariaDB + SQLite + ClickHouse on the sink side; PG + MariaDB + SQLite as table sources) and the Rust crate (still v0.3.x — Postgres-only). Also add the one Python piece that was deferred from the original v0.4.0 ship: a ClickHouse table source.

This spec is a **roadmap** — it names the sub-projects, their order, branching strategy, and integration acceptance criteria. Each sub-project gets its own brainstorming → spec → writing-plans → implementation cycle in follow-up sessions.

## 2. Non-goals

- This is not an implementation spec. No architectural details for the Rust `Backend` trait, no driver picks for Rust ClickHouse, no SQL dialect helpers — those are sub-project-level decisions.
- This is not a release-engineering spec. No crates.io/PyPI publishing strategy, no docs-site rewrite, no migration tooling. v0.4.0 stays experimental until explicitly promoted.
- This does not re-litigate the v0.4.0 architectural decisions already locked in [`2026-04-30-v4-modular-backends-design.md`](2026-04-30-v4-modular-backends-design.md). Module layout, Backend Protocol shape on the Python side, and YAML field harmonization (`target.type: postgres`, `target.database`) are inherited.

## 3. Current state — what's done, what's missing

### Python side (`experimental/v4-modular-backends`, last commit `e8ac33b`)

| Surface | State |
|---|---|
| `backends/{postgres,mariadb,sqlite,clickhouse}.py` | ✅ shipped |
| `sinks/{pg,mariadb,sqlite,clickhouse}.py` | ✅ shipped |
| `sources/{pg_table,mariadb_table,sqlite_table}.py` | ✅ shipped |
| `sources/clickhouse_table.py` | ❌ **missing** — deferred as research spike (OQ4 in predecessor spec) |
| 12-cell cross-backend matrix test | ✅ shipped (3 sources × 4 sinks) |
| Cross-backend matrix at full 16-cell coverage (4 × 4) | ❌ blocked on `clickhouse_table.py` |

### Rust side (`main`, last commit `4b22380`)

| Surface | State |
|---|---|
| `rust/chunkshop/src/sink.rs` (PG only) | v0.3.x shape — single file, not modular |
| `rust/chunkshop/src/source.rs` (files, http, json_corpus, pg_table, s3) | v0.3.x shape — no MariaDB / SQLite / ClickHouse table sources |
| `backends/` module | ❌ does not exist |
| MariaDB sink + source | ❌ does not exist |
| SQLite sink + source | ❌ does not exist |
| ClickHouse sink + source | ❌ does not exist |
| 16-cell cross-backend matrix test | ❌ does not exist |

### Bug fix folded in

`python/src/chunkshop/cli.py:15` has a hardcoded stale `version="0.1.0"` literal in the click `version_option` decorator — present on both `main` and `experimental/v4-modular-backends`. The Rust binary (`chunkshop-rs`) likely has an analogous issue. Both must be fixed before tagging v0.4.0.

## 4. Sub-project decomposition

Six sub-projects + one drive-by polish item.

| ID | Name | Scope | Est. sessions |
|---|---|---|---|
| **P1** | Python ClickHouse source | New `sources/clickhouse_table.py` + config discriminator + integration tests; resolves OQ4 from predecessor spec | 1 |
| **R1** | Rust modular backends skeleton | New `rust/chunkshop/src/backends/` module + `Backend` trait + refactor PG sink/source into the new shape **with no behavior change**. PG-only at the end of this sub-project. | 1–2 |
| **R2** | Rust MariaDB | `backends/mariadb.rs` + `sinks/mariadb.rs` + `sources/mariadb_table.rs` + driver dep + integration tests | 1–2 |
| **R3** | Rust SQLite | `backends/sqlite.rs` + `sinks/sqlite.rs` + `sources/sqlite_table.rs` + driver dep + integration tests | 1–2 |
| **R4** | Rust ClickHouse | `backends/clickhouse.rs` + `sinks/clickhouse.rs` + `sources/clickhouse_table.rs` + driver pick (different ecosystem from sqlx — its own design question) + integration tests | 2 |
| **RT** | Rust 16-cell matrix test | `rust/chunkshop/tests/cross_backend_matrix.rs` mirroring the Python 16-cell matrix; runs against all four backends, skips per-DSN | 1 |
| **CLI-FIX** | Version string fix | One-line patch on `python/src/chunkshop/cli.py:15` (use `importlib.metadata.version("chunkshop")`) + analogous fix in Rust binary if it has the same issue | 0 (drive-by) |

## 5. Wave structure

Sub-projects are sequenced into three waves to maximize parallelism while respecting real dependencies.

### Wave 1 — start now, fully parallel

P1, R1, and CLI-FIX are independent (no shared files, no shared abstractions in motion).

```
Wave 1:
  ├─ P1        (Python CH source)
  ├─ R1        (Rust backends/ skeleton + PG refactor)
  └─ CLI-FIX   (drive-by — hitchhikes on either of the above merges)
```

### Wave 2 — start after R1 lands, fully parallel among themselves

R2, R3, R4 each branch off the integration branch *after* R1 has merged, because they all need the `Backend` trait to exist before they can plug in.

```
Wave 2 (depends on R1 merged):
  ├─ R2  (MariaDB)
  ├─ R3  (SQLite)
  └─ R4  (ClickHouse)
```

R2/R3/R4 will each touch the same loader factories (`load_backend`, `load_sink`, `load_source`), producing 3-line merge conflicts at integration time. These are trivial to resolve.

### Wave 3 — after Wave 2 fully merged

```
Wave 3:
  └─ RT  (16-cell Rust cross-backend matrix test) → tag v0.4.0
```

RT cannot start until R2/R3/R4 are all merged, because the matrix needs all four backends to exist on the integration branch.

## 6. Branching pattern

Inherited from the v0.3.x repo convention (one feature = one worktree = one branch off the integration base).

- **Integration branch:** `experimental/v4-modular-backends` (continues to grow, no new "v0.4.0 train" branch)
- **Sub-project branches:** each off the integration branch tip, named:
  - `experimental/v4-py-clickhouse-source` (P1)
  - `experimental/v4-rust-backends-skeleton` (R1)
  - `experimental/v4-rust-mariadb` (R2)
  - `experimental/v4-rust-sqlite` (R3)
  - `experimental/v4-rust-clickhouse` (R4)
  - `experimental/v4-rust-matrix-test` (RT)
- **Worktree pattern:** `git worktree add ../chunkshop-<short-name> -b <branch>` from the integration branch's worktree.
- **Merge-back:** plain `git merge --no-ff` from the integration branch's worktree once the sub-project's tests are green and its plan's drift checkpoints all pass.
- **No publishing during v0.4.0.** Tag policy is deferred — pick a name when cutting (`v0.4.0`, `v0.4.0-experimental`, etc.). Crates.io and PyPI stay on 0.3.x.

## 7. Acceptance criteria (integration-level)

These are the gates to declare v0.4.0 done. Each sub-project will have its own SC list inside its own spec; these are only the criteria that span the whole effort.

| ID | Criterion | Verified by |
|---|---|---|
| **V4-SC-001** | All Wave 1 + Wave 2 + Wave 3 sub-projects merged to `experimental/v4-modular-backends` | `git log --oneline experimental/v4-modular-backends` shows merge commits for P1, R1, R2, R3, R4, RT |
| **V4-SC-002** | Python 16-cell cross-backend matrix passes (4 sources × 4 sinks, all green or skipped-with-DSN) | existing matrix test on integration branch, expanded to include `clickhouse_table` row |
| **V4-SC-003** | Rust 16-cell cross-backend matrix passes (mirror of Python) | RT sub-project deliverable |
| **V4-SC-004** | All existing PG tests pass on both languages after R1 refactor (no regressions) | `uv run pytest -q` + `cargo test -p chunkshop` clean run |
| **V4-SC-005** | `chunkshop --version` (Python) and `chunkshop-rs --version` (Rust) both print the actual package version | manual check both binaries; both must report `0.4.0` (or whatever tag is cut) |
| **V4-SC-006** | YAML field harmonization complete on both languages: `target.type: postgres` accepted, `target.type: pgvector` rejected with clear error; `target.database` accepted, `target.schema` rejected | sample YAMLs validate on both implementations; legacy-form YAML errors with a clear message |
| **V4-SC-007** | Integration branch tagged (tag name TBD at cut time) | `git tag` shows the v0.4.0-tier tag pointing at the integration branch tip |

## 8. Out of scope (deferred to v0.4.1+ or later)

Inherited from the predecessor spec, with Rust-specific additions:

- Async I/O on either side
- Connection pooling on either side
- Cross-backend factorial bakeoff (bakeoff stays PG-only)
- Migration scripts from 0.3.x → v0.4.0 (re-ingest is the policy)
- Rich HNSW tuning per backend (`hnsw: bool` is the only knob; per-backend dicts come later)
- Vector distance function selection (cosine hardcoded for v0.4.0)
- Backend hot-swap mid-pipeline / multi-sink fanout
- Crates.io and PyPI publishing
- Go port
- Sample YAMLs for every cross-backend combo (one canonical sample per backend is enough; cross-combos are covered by tests, not user-facing samples)
- Docs site / `docs/architecture.md` rewrite for v0.4.0 — separate "v0.4.0 release-prep" work item, NOT blocking the tag

## 9. What each sub-project's brainstorming session must settle

The roadmap deliberately leaves these to each sub-project. Listing them here so the brainstorming sessions can be brief (no need to re-derive context).

### P1 — Python ClickHouse source

- ClickHouse `metadata_columns` JSON-merge approach (`toJSONString(map(...))` vs named-tuple — predecessor spec OQ4)
- JOIN-via-VIEW pattern equivalent on ClickHouse — does it work with `CREATE VIEW IF NOT EXISTS`, and does ReplacingMergeTree change the semantics?
- Streaming cursor strategy — `clickhouse-connect` `query_rows_stream` vs simple `query_rows`
- Test infrastructure — what DSN env var (`CHUNKSHOP_TEST_DSN_CH`?), and does the existing `docker-compose.test.yaml` need a CH container?

### R1 — Rust modular backends skeleton

- **Load-bearing architectural question:** mirror Python's `Backend` trait shape one-for-one, OR lean on `sqlx`'s existing `Database` trait abstraction (PG, MySQL, SQLite are all sqlx-native; CH is not). The decision has cascading effects on R2/R3/R4 sizing.
- Identifier-safety helpers — port the regex-allowlist policy directly, or use sqlx's parameter binding plus a thin ident validator?
- File layout — does the `Backend` trait live in `backends/mod.rs` or `backends/base.rs` (matching Python's `base.py`)?
- Refactor scope — what stays in `sink.rs` / `source.rs` (or do those files get deleted entirely)?

### R2 — Rust MariaDB

- Driver pick — `sqlx::MySql` (matches MariaDB wire protocol) vs `mysql_async` vs `mysql` crate. Each has tradeoffs on async, vector type support, and bulk-insert perf.
- MariaDB minimum version enforcement — port the Python policy (hard floor 11.7, error on connect if `SELECT VERSION()` returns lower)
- Vector literal format — `VEC_FromText('[…]')` text-binding equivalent in Rust

### R3 — Rust SQLite

- Driver pick — `sqlx::Sqlite` vs `rusqlite` (latter has better extension-loading story for `sqlite-vec`)
- `sqlite-vec` extension loading — how it's bundled with the binary
- Two-table dance (chunks table + virtual vec table) — port from Python implementation
- WAL mode + connection lifecycle parity with Python

### R4 — Rust ClickHouse

- **Driver pick is the big question.** Options: `clickhouse` crate (official, but has its own type system), `clickhouse-rs` crate (community, older), or raw `reqwest` + JSON-over-HTTP (matches the Python `clickhouse-connect` style).
- Append-only semantics — `delete_orphans` no-op + warn pattern on Rust side
- ReplacingMergeTree opt-in — config field plumbing
- Streaming query — Rust analogue of `query_rows_stream`

### RT — Rust 16-cell matrix test

- Test harness pattern — one giant test file or one file per row?
- Per-DSN skip discipline — match Python's pattern exactly (skip with clear message, don't fail)
- Fixture corpus — reuse `docs/samples/` or build a small Rust-specific fixture?
- Cargo feature flags — does each backend's tests gate on a `cargo test --features mariadb,sqlite,clickhouse` flag, or are they always-on with DSN-skip?

## 10. References

- [Predecessor spec — v4 modular backends design](2026-04-30-v4-modular-backends-design.md) — the original Python-only v0.4.0 spec; everything in §4 (Architecture) and §5 (YAML config) is inherited
- Python integration branch: `experimental/v4-modular-backends` at `/home/yonk/yonk-tools/chunkshop-v4`
- Rust crate: `rust/chunkshop/` — currently at v0.3.x parity on `main`
- Repo convention for feature work: `git worktree add ../chunkshop-<feature> -b feat/<feature>` (per `CLAUDE.md`)

## 11. Drift checkpoints (for execution against this roadmap)

- **DC-WAVE1:** P1 + R1 both merged to integration branch. CLI-FIX has landed. Existing PG tests still pass on both languages.
- **DC-WAVE2:** R2 + R3 + R4 all merged to integration branch. Each has its own integration tests passing against its respective DSN.
- **DC-FINAL:** RT merged. Python 16-cell matrix expanded to include the new `clickhouse_table` row and passes. Rust 16-cell matrix passes. CLI version strings correct on both binaries. Tag cut.
