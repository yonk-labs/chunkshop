# R2 — Rust MariaDB Backend — Handover

**Status:** Ready to start. Wave 2 sub-project. Depends on R1 merged (it is — merge commit `13cac8b` on `experimental/v4-modular-backends`).

**Worktree to operate in:** create new — `/home/yonk/yonk-tools/chunkshop-r2-mariadb/`

**Branch to create:** `experimental/v4-rust-mariadb` off `experimental/v4-modular-backends`

**Estimated size:** 1–2 sessions.

---

## Session-startup checklist

Run these in order before doing anything else.

```bash
# 1. Confirm R1 is merged on the integration branch
cd /home/yonk/yonk-tools/chunkshop-v4
git log --oneline -3   # should show merge commit "Merge R1: Rust modular backends skeleton"

# 2. Create a fresh worktree off the integration branch tip
cd /home/yonk/yonk-tools
git -C chunkshop-v4 worktree add ../chunkshop-r2-mariadb -b experimental/v4-rust-mariadb

# 3. Confirm the trait surface is in place
ls /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust/chunkshop/src/backends/
# expect: base.rs, mod.rs, postgres.rs

# 4. Confirm baseline tests pass before adding anything
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3
# expect: "126 passed; 0 failed; 1 ignored" total across all binaries

# 5. Confirm MariaDB test DSN is reachable (or note it isn't)
echo "$CHUNKSHOP_TEST_DSN_MARIADB"
# default if docker-compose.test.yaml is up: mysql://root:rootpw@localhost:3307/chunkshop_test
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml up -d mariadb 2>&1 | tail -3
```

If any of these fail, stop and surface the failure — don't try to "fix" R1 from R2's worktree.

---

## Mission

Add a MariaDB backend to the Rust crate that plugs into R1's trait surface, mirroring `experimental/v4-modular-backends/python/src/chunkshop/{backends,sinks,sources}/{mariadb*,mariadb_table}.py`. Behavioral parity with Python is the goal — Python is the canonical reference, vectors must round-trip across implementations.

## What R1 already gave you

Read these first — they ARE the contract:

- **`rust/chunkshop/src/backends/base.rs`** (100 lines) — `BackendDialect` (sync helpers) + `BackendConn` (async I/O) + `Backend: BackendDialect + BackendConn` + `ColSpec`. This is the AFIT-based trait you implement.
- **`rust/chunkshop/src/backends/postgres.rs`** (~460 lines) — the reference implementation. Read it cover-to-cover before writing `mariadb.rs`. Mirror its structure: struct + `BackendDialect` impl + `BackendConn` impl + tests at the bottom.
- **`rust/chunkshop/src/sinks/base.rs`** (40 lines) — `Sink` trait, 5 methods.
- **`rust/chunkshop/src/sinks/pg.rs`** (~410 lines) — reference sink. `MariadbSink` mirrors its mode-dispatch (`overwrite` / `append` / `create_if_missing`), foreign-tag safety, append preflight, source write-once, delete_orphans.
- **`rust/chunkshop/src/sources/pg_table.rs`** (106 lines) — reference source-from-DB. `MariadbTableSource` mirrors it.
- **`rust/chunkshop/src/{backends,sinks,sources}/mod.rs`** — the `Any*` enums and `load_*` factories. **You will modify all three** to add a MariaDB variant.
- **`rust/chunkshop/tests/dialect_postgres_parity.rs`** + **`tests/parity-fixtures/dialect-postgres.json`** — template for `dialect_mariadb_parity.rs` + `dialect-mariadb.json`.

## Files you will create (Rust side)

| File | Mirrors | Approx lines |
|---|---|---|
| `rust/chunkshop/src/backends/mariadb.rs` | `python/src/chunkshop/backends/mariadb.py` (156 lines) | ~250 |
| `rust/chunkshop/src/sinks/mariadb.rs` | `python/src/chunkshop/sinks/mariadb.py` (247 lines) | ~400 |
| `rust/chunkshop/src/sources/mariadb_table.rs` | `python/src/chunkshop/sources/mariadb_table.py` (56 lines) | ~110 |
| `rust/chunkshop/tests/dialect_mariadb_parity.rs` | `tests/dialect_postgres_parity.rs` (135 lines) | ~135 |
| `rust/chunkshop/tests/parity-fixtures/dialect-mariadb.json` | `tests/parity-fixtures/dialect-postgres.json` (38 lines) | similar |
| `rust/chunkshop/tests/backend_mariadb_conn.rs` | `tests/backend_postgres_conn.rs` (79 lines) | similar |
| `rust/chunkshop/tests/mariadb_table_source.rs` | `tests/pg_table_source.rs` | similar |
| `rust/chunkshop/tests/mariadb_sink_create_table.rs` | `tests/pg_sink_create_table.rs` | similar |

## Files you will modify

| File | Change |
|---|---|
| `rust/chunkshop/Cargo.toml` | Add MySQL driver feature to sqlx (or alternative driver — see open questions) |
| `rust/chunkshop/src/backends/mod.rs` | Add `pub mod mariadb;` + re-export + `AnyBackend::Mariadb` variant + `load_backend` arm |
| `rust/chunkshop/src/sinks/mod.rs` | `pub mod mariadb;` + `AnySink::Mariadb` variant + `impl Sink for AnySink` arms (5 methods) + `load_sink` arm |
| `rust/chunkshop/src/sources/mod.rs` | `pub mod mariadb_table;` + `AnySource::MariadbTable` + `iter_documents` arm + `load_source` arm |
| `rust/chunkshop/src/config.rs` | `TargetConfig::Mariadb(MariadbTargetConfig)` variant + `SourceConfig::MariadbTable(MariadbTableSourceConfig)` variant. **Mirror Python's pydantic models** at `python/src/chunkshop/config.py` for these two types — same field names, same validators. |
| `rust/chunkshop/src/lib.rs` | Re-export `MariadbBackend`, `MariadbSink`, `MariadbTableSource` |

## Open architectural questions to settle first (brainstorm before planning)

These come from roadmap §9. Decide them at the start of the session — they cascade.

1. **Driver pick.** Three real options:
   - `sqlx` with the `mysql` feature — matches PostgresBackend's pattern, async-native, but historically thin on MariaDB-specific types like `VECTOR`.
   - `mysql_async` crate — more MariaDB-aware, async, separate ecosystem from sqlx.
   - `mysql` crate — sync-only, would force a `tokio::task::spawn_blocking` wrap per call.

   **Default recommendation:** start with `sqlx` mysql feature for ecosystem consistency with `PostgresBackend`. The Python side does its vector binding inline as `VEC_FromText('[...]')` text — that pattern carries over fine without driver-side VECTOR support.

2. **Vector literal format.** Python uses `VEC_FromText('[1.0,2.0,...]')` inline (see `python/src/chunkshop/backends/mariadb.py:54`). The Rust `BackendDialect::vector_literal` returns this same string. The MariaDB sink will splice that string INLINE into INSERT SQL (not as a `?` bind), because none of the MySQL drivers have a VECTOR adapter. **This means `mariadb.rs::vector_literal` returns a different shape than `postgres.rs::vector_literal`** — Postgres uses pgvector binary binding via `Vector::from(...)`, MariaDB uses inline text expression.

3. **MariaDB minimum version enforcement.** Hard floor 11.7 (the version that introduced native `VECTOR`). On `connect()`, run `SELECT VERSION()`, parse, error if < 11.7. Mirror Python's exact error message.

4. **Test DSN.** `CHUNKSHOP_TEST_DSN_MARIADB` is already conventional (see project CLAUDE.md). Default if `docker-compose.test.yaml` is up: `mysql://root:rootpw@localhost:3307/chunkshop_test`. Tests skip if unset, matching the Python pattern.

## Acceptance criteria

| ID | Criterion | Verified by |
|---|---|---|
| **R2-SC-001** | `MariadbBackend` impls `BackendDialect` + `BackendConn` (i.e. `Backend`) | `cargo build` clean; `let _: &dyn BackendDialect = &MariadbBackend::new(...);` typechecks (or use the AFIT-equivalent gymnastics) |
| **R2-SC-002** | `MariadbSink` impls `Sink` and routes through `MariadbBackend` for connection + dialect | spec-review confirms no inline raw-driver calls in sink |
| **R2-SC-003** | `MariadbTableSource` reads rows from a MariaDB table with the same column-projection logic as `PgTableSource` | mariadb_table_source integration test |
| **R2-SC-004** | Cross-language vector parity: write 5 chunks via Python `MariadbSink`, read them via Rust crate, query top-5 — matching IDs in matching order | manual e2e or test fixture |
| **R2-SC-005** | Dialect parity fixture passes (8 tests on ident quoting, fq table, vector literal, etc.) | `cargo test -p chunkshop-rs --test dialect_mariadb_parity` |
| **R2-SC-006** | All 126 existing tests still pass; build clean | `cargo test -p chunkshop-rs` |
| **R2-SC-007** | `target.type: mariadb` accepted in YAML; sample YAML lands at `docs/samples/sample-mariadb.yaml` | manual `cargo run -- ingest --config ...` |
| **R2-SC-008** | Min-version check rejects MariaDB < 11.7 with clear error | unit test or integration test against a 10.x image |

## Recommended workflow

1. **`/mission-brief`** — lock R2-SC-001 through R2-SC-008 as success criteria; capture the driver pick + vector literal format decision in Constraints. ~15 minutes.
2. **`superpowers:writing-plans`** — produce a per-task plan into `docs/superpowers/plans/<date>-r2-rust-mariadb.md`. Aim for 12–18 tasks; R1's 29-task plan was too granular in retrospect (the Phase E mechanical moves should be one task each, not five).
3. **Execute the plan directly** (do NOT use the full subagent-driven-development ceremony). Tier-2 discipline learned from R1: dispatch implementer subagents only for `MariadbBackend` and `MariadbSink` core impls (the substantive parts); do all wiring (mod.rs edits, config.rs variants, factory arms, lib.rs re-exports) inline with Edit/Write. Each substantive subagent gets one spec-review check; skip the code-quality reviewer subagent unless something looks off in the diff.

## Lessons from R1 — fold these in

- **Verbatim plan paste + 3-stage subagent review = high overhead, low signal when the plan is well-specified.** Run light verification by default; reserve full review for the parts where you're not confident.
- **Test files reference `chunkshop::source::*` / `chunkshop::sink::*`?** Probably not — R1 cleaned that up — but `grep -rn "chunkshop::sink::\|chunkshop::source::" rust/chunkshop/tests/` before the merge to be safe.
- **The package is `chunkshop-rs`, not `chunkshop`.** Always `cargo test -p chunkshop-rs` (the plan templates may say `-p chunkshop`).
- **Bakeoff config (`rust/chunkshop/src/bakeoff/config.rs::BakeoffTargetConfig`) was deliberately out of R1 scope** and still uses the legacy `schema:` shape. R2 doesn't need to touch it either — but flag it in the mission brief's Out of Scope section.
- **`cargo build -p chunkshop-rs` builds the lib; the tests are separate compile units that may have stale imports.** Run `cargo test -p chunkshop-rs --no-run` to flush test compile errors before starting any test work.
- **The `tags` column type matters.** Postgres uses `text[]`; MariaDB doesn't have native arrays — Python serializes tags as `JSON`. Verify `MariadbBackend::tags_array_type_ddl` returns `JSON` (or `LONGTEXT`?) and the sink binds `tags` as `serde_json::to_string(&tags_vec)?` not `&tags_vec`.

## Watch-outs specific to MariaDB

- **`%s` vs `?` placeholders.** sqlx uses `?` for MySQL/MariaDB, not `$1`-style. The plan code from R1 uses `$1` — every SQL string will need re-templating. The `BackendDialect::placeholder(i: usize) -> String` helper might be worth adding (defaults to `$N` for PG, returns `?` for MariaDB).
- **Inline vector literals defeat parameter binding.** Verify ALL identifiers and string-typed literals in the SQL are still allowlisted/quoted properly — the inline VEC_FromText splice is the one exception.
- **`source_tag` write-once.** Python's MariaDB sink uses `INSERT ... ON DUPLICATE KEY UPDATE` (the MySQL/MariaDB equivalent of `ON CONFLICT`). The exclusion list (skip `source` in the UPDATE clause) needs the same shape — verify by reading `python/src/chunkshop/sinks/mariadb.py`.
- **Schema vs database.** MariaDB doesn't have schemas in the PG sense — `database_name` maps to MariaDB's database. `CREATE DATABASE IF NOT EXISTS \`name\`` (note backticks for quoting). Don't double-quote idents the PG way.

## Definition of done

- All R2-SC-XXX criteria met
- Mission brief's drift checkpoints all green
- `cargo test -p chunkshop-rs` clean: 126 baseline + new MariaDB tests
- Branch ready to merge into `experimental/v4-modular-backends` with a `--no-ff` merge commit (matches R1's pattern: see commit `13cac8b`)
- Sample YAML at `docs/samples/sample-mariadb.yaml` validates and runs end-to-end

## After R2 merges

Wave 2 fans out: R3 and R4 can run in parallel. RT (Wave 3) waits for both R3 and R4. See sibling handovers `R3-rust-sqlite-handover.md` and `RT-rust-matrix-test-handover.md`.
