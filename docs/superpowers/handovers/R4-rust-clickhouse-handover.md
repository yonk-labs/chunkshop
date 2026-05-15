# R4 — Rust ClickHouse Backend — Handover

**Status:** Ready to start. Wave 2 sub-project, parallel-safe with R2/R3. Depends on R1 merged (it is — merge commit `13cac8b` on `experimental/v4-modular-backends`).

**Worktree to operate in:** create new — `/home/yonk/yonk-tools/chunkshop-r4-clickhouse/`

**Branch to create:** `experimental/v4-rust-clickhouse` off `experimental/v4-modular-backends`

**Estimated size:** 1–3 sessions. Highest variance among R2/R3/R4 because the Rust ClickHouse driver ecosystem is less mature than sqlx-backed options. Pick the driver early — it dominates the rest.

---

## Session-startup checklist

```bash
# 1. Confirm R1 is merged on the integration branch
cd /home/yonk/yonk-tools/chunkshop-v4
git log --oneline -3   # expect "Merge R1: Rust modular backends skeleton"

# 2. Create a fresh worktree off the integration branch
cd /home/yonk/yonk-tools
git -C chunkshop-v4 worktree add ../chunkshop-r4-clickhouse -b experimental/v4-rust-clickhouse

# 3. Confirm trait surface in place
ls /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust/chunkshop/src/backends/
# expect: base.rs, mod.rs, postgres.rs

# 4. Baseline tests
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3
# expect: 126 passed; 0 failed; 1 ignored

# 5. ClickHouse test infrastructure
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml up -d clickhouse
echo "$CHUNKSHOP_TEST_DSN_CH"
# Should be set by docker-compose env or your shell. The Python side already uses
# this env var convention; mirror exactly.
```

---

## Mission

Add a ClickHouse backend to the Rust crate that plugs into R1's trait surface, mirroring `experimental/v4-modular-backends/python/src/chunkshop/{backends,sinks}/clickhouse.py` (and any `sources/clickhouse_table.py` that lands from P1). Behavioral parity with Python is the goal — same `delete_orphans` warn-once semantics, same opt-in `ReplacingMergeTree`, same append-only contract.

## What R1 already gave you

Read these first — they ARE the contract:

- `rust/chunkshop/src/backends/base.rs` — `BackendDialect` + `BackendConn` + `Backend` + `ColSpec`
- `rust/chunkshop/src/backends/postgres.rs` — reference implementation (~460 lines)
- `rust/chunkshop/src/sinks/{base.rs, pg.rs}` — `Sink` trait + reference sink (~410 lines)
- `rust/chunkshop/src/sources/pg_table.rs` — reference source-from-DB (~106 lines)
- `rust/chunkshop/src/{backends,sinks,sources}/mod.rs` — `Any*` enums + `load_*` factories
- `rust/chunkshop/tests/dialect_postgres_parity.rs` + `tests/parity-fixtures/dialect-postgres.json` — parity-test template

## ClickHouse is fundamentally different from PG/MariaDB/SQLite — read this first

ClickHouse is **append-only** at the storage layer. There is no UPSERT. There are no transactions in the OLTP sense. Mutations (`ALTER TABLE … DELETE`) are async and not atomic with writes. This drives a series of behavioral departures:

- **No `ON CONFLICT` / `ON DUPLICATE KEY`.** `INSERT INTO {table} VALUES (...)` is the only write path. Re-ingesting the same doc produces duplicate rows.
- **`delete_orphans` is a no-op + warn pattern** (see `python/src/chunkshop/sinks/clickhouse.py:90–97`). Sink emits a one-time-per-process `tracing::warn!` on construction if `cfg.delete_orphans: true`. Don't fail; don't pretend to delete; warn and move on.
- **Dedup is opt-in via `ReplacingMergeTree(created_at)` engine.** The sink config carries an `engine: String` field that the Rust port mirrors. Default engine is `MergeTree`. Users who want dedup set `engine: ReplacingMergeTree(created_at)` and use `SELECT ... FINAL` / `OPTIMIZE TABLE … FINAL` to force merge.
- **`metadata_columns` JSON-merge.** Predecessor spec OQ4 (now resolved): use `toJSONString(map(...))` to build the metadata column inline. See Python `clickhouse.py` for the canonical pattern.
- **Streaming reads.** The Python side uses `clickhouse-connect`'s `query_rows_stream`. The Rust analog depends on the driver pick — see open question #1.

Read these Python sources cover-to-cover BEFORE writing any Rust:

- `python/src/chunkshop/backends/clickhouse.py` (198 lines) — DDL, identifier quoting, JSON-path SQL, vector literal format, no-upsert documentation
- `python/src/chunkshop/sinks/clickhouse.py` (285 lines) — append-only INSERT loop, `delete_orphans` warn pattern, `ReplacingMergeTree` engine plumbing, `_DELETE_ORPHANS_WARNED` PID-keyed set
- `python/tests/chunkshop/test_sink_clickhouse.py` and `test_backend_clickhouse.py` — parity targets

If P1 (Python ClickHouse source) has merged, also read `python/src/chunkshop/sources/clickhouse_table.py`. If P1 hasn't merged yet, the Rust source is OUT OF SCOPE for R4 — flag it and move on. RT (Wave 3) will need it eventually but R4's mission only requires `ClickhouseBackend` + `ClickhouseSink`.

## Files you will create

| File | Mirrors | Approx lines |
|---|---|---|
| `rust/chunkshop/src/backends/clickhouse.rs` | `python/src/chunkshop/backends/clickhouse.py` (198) | ~320 |
| `rust/chunkshop/src/sinks/clickhouse.rs` | `python/src/chunkshop/sinks/clickhouse.py` (285) | ~440 |
| `rust/chunkshop/src/sources/clickhouse_table.rs` (only if P1 merged) | `python/src/chunkshop/sources/clickhouse_table.py` | ~150 |
| `rust/chunkshop/tests/dialect_clickhouse_parity.rs` | `tests/dialect_postgres_parity.rs` (135) | ~135 |
| `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json` | `tests/parity-fixtures/dialect-postgres.json` (38) | similar |
| `rust/chunkshop/tests/backend_clickhouse_conn.rs` | `tests/backend_postgres_conn.rs` (79) | similar |
| `rust/chunkshop/tests/clickhouse_sink_create_table.rs` | `tests/pg_sink_create_table.rs` | similar |
| `rust/chunkshop/tests/clickhouse_sink_append_only.rs` | new — exercises the no-upsert + delete_orphans warn behavior | ~120 |

## Files you will modify

| File | Change |
|---|---|
| `rust/chunkshop/Cargo.toml` | Add the chosen ClickHouse driver dependency |
| `rust/chunkshop/src/backends/mod.rs` | `pub mod clickhouse;` + `AnyBackend::Clickhouse` + `load_backend` arm |
| `rust/chunkshop/src/sinks/mod.rs` | `pub mod clickhouse;` + `AnySink::Clickhouse` + 5 trait-impl arms + `load_sink` arm |
| `rust/chunkshop/src/sources/mod.rs` | (only if P1 merged) `pub mod clickhouse_table;` + variant + arms |
| `rust/chunkshop/src/config.rs` | `TargetConfig::Clickhouse(ClickhouseTargetConfig)` with `engine: String` field. Mirror Python pydantic model. |
| `rust/chunkshop/src/lib.rs` | Re-export `ClickhouseBackend`, `ClickhouseSink` (and source if P1 merged) |

## Open architectural questions to settle first (THIS IS THE BIG ONE)

From roadmap §9. Settle these in brainstorming before planning — the driver pick especially has cascading effects.

1. **Driver pick — load-bearing.** Three real options, ranked by ecosystem maturity:
   - **`clickhouse` crate (official, by ClickHouse Inc.)** — async-first, has its own type system that's strict about schema mapping. Vector type support: yes for `Array(Float32)`. Uses native protocol, faster than HTTP for bulk. Ergonomics: very strict types + derive-heavy.
   - **`clickhouse-rs` crate (community)** — older, less actively maintained. Lower-level API. Verify currency (last release date) before committing.
   - **Raw `reqwest` + JSON-over-HTTP** — matches the Python `clickhouse-connect` style. Simplest, most flexible, slowest. Most lines of code but most predictable.

   **Default recommendation:** start with the official `clickhouse` crate. If its strict typing forces too many wrapper structs for `metadata` and `tags` columns, fall back to `reqwest` + JSON. Document the decision in the mission brief.

2. **Vector type at the schema level.** ClickHouse stores vectors as `Array(Float32)`. There's no `vector(dim)` type. `vector_type_ddl(dim)` returns `Array(Float32)` (dim is unused — CH doesn't enforce dim at the type level; sink validates by length). Confirm by reading Python `clickhouse.py::vector_type_ddl`.

3. **`delete_orphans` warn-once mechanism.** Python uses a module-level `_DELETE_ORPHANS_WARNED: set[int]` keyed by PID. Rust analog: a `std::sync::OnceLock<()>` or `AtomicBool` static inside the sink module. Trigger on `Sink::create_table` (or `Sink::new`?), guard with the once-cell; emit `tracing::warn!`.

4. **`ReplacingMergeTree` engine plumbing.** Config field `engine: Option<String>` defaulting to `"MergeTree"` (or `None` → fallback). The engine string is interpolated into `CREATE TABLE … ENGINE = {engine}` — needs allowlist validation (regex `^(MergeTree|ReplacingMergeTree(\(.+\))?)$` or similar) to prevent SQL injection. **Read Python's validator** in `config.py` and mirror it exactly.

5. **DSN format.** Python uses `clickhouse-connect` URL style: `clickhouse://user:pass@host:port/database`. Rust mirrors. The `dsn_env` field stays.

6. **Test DSN.** `CHUNKSHOP_TEST_DSN_CH` is the conventional env var (per project CLAUDE.md). docker-compose.test.yaml exposes ClickHouse on port 8123 (HTTP) by default. If using the native protocol, may need port 9000 instead — check the chosen driver's URL parsing rules.

## Acceptance criteria

| ID | Criterion | Verified by |
|---|---|---|
| **R4-SC-001** | `ClickhouseBackend` impls `BackendDialect` + `BackendConn` (i.e. `Backend`) | `cargo build` clean |
| **R4-SC-002** | `ClickhouseSink` impls `Sink` and routes through `ClickhouseBackend` | spec-review confirms no inline raw-driver calls in sink |
| **R4-SC-003** | `write_document` is INSERT-only (no UPSERT shenanigans); re-ingesting the same doc twice produces 2× rows in default `MergeTree` mode | append-only integration test |
| **R4-SC-004** | `cfg.delete_orphans: true` produces exactly ONE warning per process (not per call) and does NOT actually delete | warn-once test capturing log output |
| **R4-SC-005** | `engine: "ReplacingMergeTree(created_at)"` is accepted, validated against allowlist, interpolated into `CREATE TABLE` | config-parsing + integration test with `OPTIMIZE FINAL` |
| **R4-SC-006** | Cross-language vector parity: write 5 chunks via Python `ClickhouseSink`, query top-5 via Rust → matching IDs in matching order | manual e2e |
| **R4-SC-007** | Dialect parity fixture passes (~8 tests) | `cargo test --test dialect_clickhouse_parity` |
| **R4-SC-008** | All 126 baseline tests still pass | full suite green |
| **R4-SC-009** | `target.type: clickhouse` accepted in YAML; sample at `docs/samples/sample-clickhouse.yaml` | `cargo run -- ingest --config ...` |
| **R4-SC-010** | (conditional on P1 merged) `ClickhouseTableSource` reads rows with same projection logic as `PgTableSource` | source integration test |

## Recommended workflow

Same shape as R2/R3:

1. **`/mission-brief`** — lock R4-SC-001 through R4-SC-009 (and R4-SC-010 if P1 merged). Capture driver pick + engine allowlist regex + warn-once mechanism in Constraints. **Spend extra time on the driver-pick brainstorm** — it's the highest-variance decision in this whole sub-project.
2. **`superpowers:writing-plans`** — 14–18 task plan into `docs/superpowers/plans/<date>-r4-rust-clickhouse.md`. Likely 2–4 tasks more than R2 because of the engine plumbing and the warn-once mechanism.
3. **Execute directly** with tier-2 discipline: substantive parts (`ClickhouseBackend::connect` w/ driver-specific URL parsing, `ClickhouseSink::write_document` INSERT-only path, `ClickhouseSink::query_top_k` with `cosineDistance(...)` function) get one implementer subagent each + spec-review pass; mechanical parts inline.

## Lessons from R1 — fold these in

- Skip the elaborate review-subagent ceremony for verbatim plan paste. Light verification (your own diff read + cargo test) is enough for ~80% of tasks.
- Package is `chunkshop-rs`, not `chunkshop`. Always `cargo test -p chunkshop-rs`.
- `cargo build -p chunkshop-rs` builds the lib; tests are separate compile units. Run `cargo test -p chunkshop-rs --no-run` to flush test compile errors before starting any test work.
- Don't widen identifier-allowlist regexes "to make ClickHouse happy" — fix at the call site or in `clickhouse.rs::quote_ident` instead.
- Bakeoff config is OUT of R4 scope. The `bakeoff/config.rs::BakeoffTargetConfig` is its own struct and was deliberately untouched in R1. R4 doesn't need to migrate it.

## Watch-outs specific to ClickHouse

- **No transactions.** The PgSink pattern of `tx.begin() … insert loop … tx.commit()` does not apply. Each INSERT is its own atomic statement (CH guarantees row-atomicity, not transaction-atomicity). The `write_document` impl loops over chunks issuing one INSERT each — or uses a bulk INSERT if the driver supports it (the official `clickhouse` crate does via `Insert<T>`).
- **Bulk INSERT is the perf path.** A naive per-row INSERT for 1000 chunks is 1000× slower than one bulk INSERT. The Python side uses `client.insert_dict(...)` for bulk. Match that on the Rust side via the driver's bulk API.
- **`Array(Float32)` binding.** Different drivers handle this differently. Verify by writing a smoke test that round-trips a `Vec<f32>` through the chosen driver before committing to it.
- **`tags` column type.** Use `Array(String)` (CH-native) — NOT JSON, NOT TEXT. CH has real array types, use them.
- **`metadata` column type.** Use `String` (storing JSON text). Then the JSON-path SQL becomes `JSONExtractString(metadata, 'a', 'b')` rather than PG's `metadata->'a'->>'b'`. The `BackendDialect::json_path_sql` impl returns the right CH form.
- **No `vector(dim)`.** Don't try to enforce dim at the schema level. Sink validates by Vec length at write time.
- **Identifier quoting.** CH uses backticks (`` `table` ``), not double quotes. `quote_ident` returns backtick-wrapped output. Check the dialect parity fixture matches.
- **`fq_table` format.** CH uses `database.table` notation, no quotes typically (or backticks). Match Python.
- **`OPTIMIZE TABLE … FINAL` is a sink demo concern.** Not part of R4 unless tests need it. The default tests don't need to invoke it.
- **HTTP vs native protocol port.** docker-compose default config exposes 8123 (HTTP) and sometimes 9000 (native). Drivers vary. If `$CHUNKSHOP_TEST_DSN_CH` is wrong-port, all tests fail with cryptic connection errors. Document the expected port in the mission brief.

## Definition of done

- All R4-SC-XXX criteria met (R4-SC-010 conditional on P1)
- Mission brief drift checkpoints green
- `cargo test -p chunkshop-rs` clean: 126 baseline + new ClickHouse tests
- Branch ready to merge `--no-ff` into `experimental/v4-modular-backends` (mirror R1's `13cac8b` pattern)
- Sample YAML at `docs/samples/sample-clickhouse.yaml`

## After R4 merges

Wave 2 is complete. RT (Wave 3) is unblocked once R2 + R3 + R4 are all merged. See `RT-rust-matrix-test-handover.md` for the matrix test that ties everything together.
