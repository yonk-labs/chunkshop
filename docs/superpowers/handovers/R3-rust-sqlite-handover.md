# R3 — Rust SQLite Backend — Handover

**Status:** Ready to start. Wave 2 sub-project, parallel-safe with R2/R4. Depends on R1 merged (it is — merge commit `13cac8b` on `experimental/v4-modular-backends`).

**Worktree to operate in:** create new — `/home/yonk/yonk-tools/chunkshop-r3-sqlite/`

**Branch to create:** `experimental/v4-rust-sqlite` off `experimental/v4-modular-backends`

**Estimated size:** 1–2 sessions. Probably the most architecturally distinctive of R2/R3/R4 because of the `sqlite-vec` two-table dance.

---

## Session-startup checklist

```bash
# 1. Confirm R1 is merged
cd /home/yonk/yonk-tools/chunkshop-v4
git log --oneline -3   # expect merge commit "Merge R1: Rust modular backends skeleton"

# 2. Create the worktree
cd /home/yonk/yonk-tools
git -C chunkshop-v4 worktree add ../chunkshop-r3-sqlite -b experimental/v4-rust-sqlite

# 3. Confirm the trait surface
ls /home/yonk/yonk-tools/chunkshop-r3-sqlite/rust/chunkshop/src/backends/
# expect: base.rs, mod.rs, postgres.rs

# 4. Baseline test sweep
cd /home/yonk/yonk-tools/chunkshop-r3-sqlite/rust
cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3
# expect: 126 passed; 0 failed; 1 ignored

# 5. SQLite test infra: NO DSN env var needed.
# Python's pattern uses :memory: or tmp_path — see python/tests/chunkshop/test_backend_sqlite.py.
# Rust will follow the same pattern; no docker-compose entry to bring up.
```

---

## Mission

Add a SQLite backend with vector-search via the `sqlite-vec` extension, mirroring `experimental/v4-modular-backends/python/src/chunkshop/{backends,sinks,sources}/{sqlite*,sqlite_table}.py`. Behavioral parity with Python is the goal.

## What R1 already gave you

Same trait surface as R2 — read these first:

- `rust/chunkshop/src/backends/base.rs` — `BackendDialect` + `BackendConn` + `Backend` + `ColSpec`
- `rust/chunkshop/src/backends/postgres.rs` — reference implementation
- `rust/chunkshop/src/sinks/{base.rs, pg.rs}` — `Sink` trait + reference sink
- `rust/chunkshop/src/sources/pg_table.rs` — reference source-from-DB
- `rust/chunkshop/src/{backends,sinks,sources}/mod.rs` — `Any*` enums + `load_*` factories
- `rust/chunkshop/tests/dialect_postgres_parity.rs` + `tests/parity-fixtures/dialect-postgres.json` — parity-test template

## SQLite is architecturally different from R2/R4 — read this first

`sqlite-vec` does NOT integrate vectors into a normal table. It uses a virtual table (`vec0`) for the embedding column, joined back to the main "chunks" table on `id`. This means:

- `MariadbSink` and `PgSink` write to ONE table per cell.
- `SqliteSink` writes to TWO tables per cell: a regular table (`{table}` — id, doc_id, seq_num, content, tags, metadata, source) AND a virtual `vec0` table (`{table}_vec` — id, embedding).
- Reads JOIN them on `id`.
- `vec0` virtual tables don't support UPSERT — DELETE+INSERT each chunk per write.
- HNSW is a no-op — `sqlite-vec` is brute-force KNN. Sink should warn (but not fail) if `target.hnsw: true`.

Read these Python sources cover-to-cover BEFORE writing any Rust:

- `python/src/chunkshop/backends/sqlite.py` (163 lines) — `CREATE VIRTUAL TABLE IF NOT EXISTS {vec_fq} USING vec0(...)`, plus `sqlite_master` table-existence check
- `python/src/chunkshop/sinks/sqlite.py` (276 lines) — the two-table dance, append preflight that checks for the vec0 partner, the DELETE+INSERT cycle for embedding rows
- `python/src/chunkshop/sources/sqlite_table.py` (42 lines)
- `python/tests/chunkshop/test_sink_sqlite.py` — exercises the two-table behavior

## Files you will create

| File | Mirrors | Approx lines |
|---|---|---|
| `rust/chunkshop/src/backends/sqlite.rs` | `python/src/chunkshop/backends/sqlite.py` (163) | ~280 |
| `rust/chunkshop/src/sinks/sqlite.rs` | `python/src/chunkshop/sinks/sqlite.py` (276) | ~450 |
| `rust/chunkshop/src/sources/sqlite_table.rs` | `python/src/chunkshop/sources/sqlite_table.py` (42) | ~90 |
| `rust/chunkshop/tests/dialect_sqlite_parity.rs` | `tests/dialect_postgres_parity.rs` (135) | ~135 |
| `rust/chunkshop/tests/parity-fixtures/dialect-sqlite.json` | `tests/parity-fixtures/dialect-postgres.json` (38) | similar |
| `rust/chunkshop/tests/backend_sqlite_conn.rs` | `tests/backend_postgres_conn.rs` (79) | similar |
| `rust/chunkshop/tests/sqlite_table_source.rs` | `tests/pg_table_source.rs` | similar |
| `rust/chunkshop/tests/sqlite_sink_create_table.rs` | `tests/pg_sink_create_table.rs` | larger — verify both tables get created |
| `rust/chunkshop/tests/sqlite_sink_two_table_dance.rs` | new — exercise the vec0 split | ~150 |

## Files you will modify

| File | Change |
|---|---|
| `rust/chunkshop/Cargo.toml` | Add `sqlx` sqlite feature OR `rusqlite` + `sqlite-vec` companion crate (see open questions) |
| `rust/chunkshop/src/backends/mod.rs` | `pub mod sqlite;` + `AnyBackend::Sqlite` + `load_backend` arm |
| `rust/chunkshop/src/sinks/mod.rs` | `pub mod sqlite;` + `AnySink::Sqlite` + 5 trait-impl arms + `load_sink` arm |
| `rust/chunkshop/src/sources/mod.rs` | `pub mod sqlite_table;` + `AnySource::SqliteTable` + `iter_documents` arm + `load_source` arm |
| `rust/chunkshop/src/config.rs` | `TargetConfig::Sqlite(SqliteTargetConfig)` + `SourceConfig::SqliteTable(SqliteTableSourceConfig)`. Mirror Python's pydantic models. |
| `rust/chunkshop/src/lib.rs` | Re-export `SqliteBackend`, `SqliteSink`, `SqliteTableSource` |

## Open architectural questions to settle first

From roadmap §9, plus learned items:

1. **Driver pick — the load-bearing decision.**
   - `sqlx::Sqlite` — async-native, ecosystem-consistent with PostgresBackend, but loading custom extensions (`sqlite-vec`) requires per-connection setup (`load_extension`).
   - `rusqlite` — sync, mature extension-loading story, but every method needs `tokio::task::spawn_blocking` to fit the async `BackendConn` trait.

   **Default recommendation:** `rusqlite` despite the async wrap, because `sqlite-vec` extension loading is the gnarly part and `rusqlite` documents it. Sync-then-spawn_blocking is well-trod ground for SQLite in async Rust.

2. **`sqlite-vec` extension distribution.** Three options:
   - Bundle a precompiled `.so`/`.dylib`/`.dll` with the binary and `load_extension` it at runtime
   - Compile from source via `cc` build script
   - Use the `sqlite-vec` Rust crate if it exists (check crates.io — it does, last verified mid-2025; verify currency)

   **Default recommendation:** the `sqlite-vec` crate. Falls back to bundling if the crate is stale.

3. **WAL mode + connection lifecycle.** Python opens with WAL on. Rust should match: on `connect()`, run `PRAGMA journal_mode=WAL`. Confirm by reading Python's `backends/sqlite.py`.

4. **DSN format.** Python accepts `path/to/db.sqlite` or `:memory:`. Rust mirrors. The `dsn_env` field stays — points to a file path or `:memory:` literal. Validate at parse time that it's not a Postgres-style URL.

5. **Test infra: in-process, no docker.** No `CHUNKSHOP_TEST_DSN_SQLITE` env var by convention — tests use `:memory:` for unit tests and `tmp_path` (Rust: `tempfile::tempdir()`) for tests that need persistence.

## Acceptance criteria

| ID | Criterion | Verified by |
|---|---|---|
| **R3-SC-001** | `SqliteBackend` impls `BackendDialect` + `BackendConn` | `cargo build` clean |
| **R3-SC-002** | `SqliteSink` creates BOTH the chunks table and the `{table}_vec` virtual table on `create_table()` | sqlite_sink_create_table integration test |
| **R3-SC-003** | `SqliteSink::write_document` upserts the chunks table AND DELETE+INSERTs into the vec0 table in the same transaction | two_table_dance test |
| **R3-SC-004** | `query_top_k` runs `vec0 MATCH '...'` JOINed back to the chunks table, returns `(doc_id, seq_num, distance)` | integration test |
| **R3-SC-005** | `target.hnsw: true` produces a one-time warning on `create_table` (not an error) | unit test capturing log output |
| **R3-SC-006** | `append` mode preflight requires both tables exist; refuses if `_vec` partner is missing | sink_modes_parity-style test |
| **R3-SC-007** | Cross-language vector parity: write via Python `SqliteSink`, query via Rust → matching results | manual e2e |
| **R3-SC-008** | Dialect parity fixture passes | `cargo test --test dialect_sqlite_parity` |
| **R3-SC-009** | All 126 baseline tests still pass | full suite green |

## Recommended workflow

Same shape as R2:

1. **`/mission-brief`** — lock R3-SC-001 through R3-SC-009 as success criteria; capture the driver pick + extension-distribution decision in Constraints.
2. **`superpowers:writing-plans`** — 14–20 task plan into `docs/superpowers/plans/<date>-r3-rust-sqlite.md`. SQLite has more architectural distinctness so the plan will be longer than R2 by 2–4 tasks.
3. **Execute directly** with tier-2 discipline: substantive parts (`SqliteBackend::connect` w/ extension load, `SqliteSink::create_table` two-table DDL, `SqliteSink::write_document` two-table tx, `query_top_k` JOIN) get one implementer subagent each + spec-review pass; mechanical parts (mod.rs wiring, config.rs variants, factory arms) inline.

## Lessons from R1 — fold these in

Same as R2:

- Skip the elaborate review subagent ceremony for verbatim plan paste — light verification (your own diff read + cargo test) is enough for ~80% of tasks.
- Package is `chunkshop-rs`, not `chunkshop`.
- `cargo build` builds the lib; tests are separate compile units. Run `cargo test --no-run` early to flush test-side compile errors.
- Don't widen the regex allowlists in `PromoteColumn` / config validators "to make MariaDB / SQLite happier" — fix at the call site or in the backend's `quote_ident` instead.

## Watch-outs specific to SQLite

- **No `text[]` array type.** Tags column is `TEXT` storing JSON. `tags_array_type_ddl` returns `TEXT`. Sink binds tags as `serde_json::to_string(&tags)?`.
- **`vec0` virtual tables refuse UPSERT.** The Python sink's pattern is: DELETE WHERE id IN (...) followed by plain INSERTs into the vec table, all inside the same tx as the upsert into the main table. Mirror this. Don't try to ON CONFLICT a vec0 table.
- **Extension loading per-connection.** If you use a connection pool, every connection needs `load_extension('sqlite-vec')`. `rusqlite::Connection::open(...)` + `unsafe { conn.load_extension_enable() }` + `conn.load_extension(path, None)`. Wrap in a connection-init hook.
- **`PRAGMA foreign_keys=ON` policy.** Decide. Python's choice should drive yours.
- **`:memory:` databases per test.** Cross-test isolation comes from new in-memory DBs per test, not from cleanup. Don't try to share a `:memory:` connection across tests.
- **No schema namespace.** SQLite has no schemas — `database_name` field becomes meaningless. Either: ignore it (return empty fq prefix), error if non-empty, or repurpose it as an attached-database name. Match Python's choice.
- **HNSW warning timing.** Emit on `create_table` once, not per-write. Use a `tracing::warn!` and gate on the config field; do NOT panic.

## Definition of done

- All R3-SC-XXX criteria met
- Mission brief's drift checkpoints green
- `cargo test -p chunkshop-rs` clean: 126 baseline + new SQLite tests
- Branch ready to merge into `experimental/v4-modular-backends` with `--no-ff` (mirror R1's `13cac8b`)
- Sample YAML at `docs/samples/sample-sqlite.yaml`

## After R3 merges

Wave 2 continues — R4 (ClickHouse) is parallel-safe. RT (Wave 3) waits for R2 + R3 + R4 all merged.
