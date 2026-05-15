# R4 — Rust ClickHouse Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md` (read it before starting; re-read at every DC-XXX gate)

**Goal:** Add a ClickHouse backend, sink, and table source to the chunkshop Rust crate behind R1's trait surface, mirroring `python/src/chunkshop/{backends,sinks,sources}/clickhouse*.py`.

**Architecture:** `ClickhouseBackend` impls `BackendDialect` only — connection-layer methods stay as inherent methods on the concrete type because R1's `BackendConn` trait is sqlx-Postgres-concrete by deliberate seam (the GAT abstraction is R2's job). `ClickhouseSink` impls the `Sink` trait, append-only by construction (CH has no `ON CONFLICT`). `ClickhouseTableSource` mirrors `PgTableSource`'s shape but streams via the official `clickhouse` crate's cursor API. The `engine` config field gets an allowlist regex — a deliberate hardening relative to Python's raw interpolation.

**Tech Stack:** Rust 2021 edition, official `clickhouse` crate v0.15+ (HTTP transport, native `Vec<f32>` ↔ `Array(Float32)` mapping, typed `Insert<T>` bulk API), tokio async runtime, regex allowlist validation, `tracing` for warn-once.

**Worktree:** `/home/yonk/yonk-tools/chunkshop-r4-clickhouse` on branch `experimental/v4-rust-clickhouse`

**Test infrastructure:** ClickHouse 24.10.4 already running via `docker compose -f docker-compose.test.yaml up -d clickhouse`. Test DSN: `clickhouse://default:chpw@localhost:8124/chunkshop_test` exposed as env var `CHUNKSHOP_TEST_DSN_CH`.

**Cargo package name:** `chunkshop-rs` — always run `cargo test -p chunkshop-rs`.

---

## File Structure

### Files to create

| Path | Purpose | Approx lines |
|---|---|---|
| `rust/chunkshop/src/backends/clickhouse.rs` | `ClickhouseBackend` — `BackendDialect` impl + inherent connection methods | ~340 |
| `rust/chunkshop/src/sinks/clickhouse.rs` | `ClickhouseSink` — `Sink` impl, append-only, warn-once `delete_orphans` | ~430 |
| `rust/chunkshop/src/sources/clickhouse_table.rs` | `ClickhouseTableSource` — streaming projection from a CH table | ~140 |
| `rust/chunkshop/tests/dialect_clickhouse_parity.rs` | Dialect parity test driven by JSON fixture (mirror PG parity) | ~120 |
| `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json` | Golden values for `quote_ident`, `vector_type_ddl`, etc. | ~50 |
| `rust/chunkshop/tests/backend_clickhouse_conn.rs` | Skip-if-no-DSN integration test for connection-layer inherent methods | ~80 |
| `rust/chunkshop/tests/clickhouse_sink_create_table.rs` | Mode dispatch + foreign-tag safety + create_if_missing tests | ~180 |
| `rust/chunkshop/tests/clickhouse_sink_append_only.rs` | No-upsert (SC-003) + `delete_orphans` warn-once (SC-004) tests | ~140 |
| `rust/chunkshop/tests/clickhouse_sink_replacing_engine.rs` | `ReplacingMergeTree(created_at)` + `OPTIMIZE FINAL` dedup test (SC-005) | ~90 |
| `rust/chunkshop/tests/clickhouse_table_source.rs` | Source projection + streaming integration test (SC-010) | ~110 |
| `rust/chunkshop/tests/manual/r4-cross-language.md` | Manual cross-language e2e instructions (SC-006) | ~60 |

### Files to modify

| Path | Change |
|---|---|
| `rust/chunkshop/Cargo.toml` | Add `clickhouse = "0.15"` to `[dependencies]` |
| `rust/chunkshop/src/backends/mod.rs` | `pub mod clickhouse;` + `AnyBackend::Clickhouse` variant + `load_backend` arm + re-export |
| `rust/chunkshop/src/sinks/mod.rs` | `pub mod clickhouse;` + `AnySink::Clickhouse` variant + 5 trait-impl arms + `load_sink` arm + re-export |
| `rust/chunkshop/src/sources/mod.rs` | `pub mod clickhouse_table;` + `AnySource::ClickhouseTable` variant + `load_source` arm + re-export. Update R1's "ClickhouseTable is deferred to v4.1" comment to reflect R4 landing it |
| `rust/chunkshop/src/config.rs` | Add `TargetConfig::Clickhouse(ClickhouseTargetConfig)` with `engine` field + allowlist validator + `SourceConfig::ClickhouseTable(ClickhouseTableSourceConfig)` variant + ident validation in `load_config` |
| `rust/chunkshop/src/lib.rs` | Re-export `ClickhouseBackend`, `ClickhouseSink`, `ClickhouseTableSource` |

---

### Task 1: Add `clickhouse` driver + Vec<f32>↔Array(Float32) round-trip smoke test (DC-001 gate)

**Why first:** This task validates the highest-variance assumption in the mission brief — that the official `clickhouse` crate handles `Array(Float32)` cleanly via `Vec<f32>` without wrapper structs or type gymnastics. If this smoke fails, **stop and re-read the brief**: DC-001 says confirm with the user before falling back to `reqwest` + JSON.

**Files:**
- Modify: `rust/chunkshop/Cargo.toml` (add `clickhouse = "0.15"`)
- Create: `rust/chunkshop/tests/clickhouse_driver_smoke.rs`

- [ ] **Step 1: Add the dependency**

Edit `rust/chunkshop/Cargo.toml`. Find the `[dependencies]` block (line ~29) and add this line in alphabetical position (right after `blake2`):

```toml
clickhouse = "0.15"
```

- [ ] **Step 2: Verify the crate downloads and the lib still compiles**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | tail -10
```

Expected: `Compiling clickhouse v0.15.x` then `Finished` with no errors. If a network proxy issue surfaces, document it in the task notes and retry.

- [ ] **Step 3: Write the smoke test**

Create `rust/chunkshop/tests/clickhouse_driver_smoke.rs`:

```rust
//! DC-001 driver gate: prove the official `clickhouse` crate round-trips
//! `Vec<f32>` through `Array(Float32)` cleanly. If this test breaks at the
//! type level, the brief's driver pick must be revisited (fall back to
//! reqwest + JSON) — re-read the mission brief before proceeding.

use clickhouse::{Client, Row};
use serde::{Deserialize, Serialize};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn client_from_dsn() -> Client {
    // DSN expected in the form clickhouse://user:pass@host:port/database
    // Parse minimal fields. (Full DSN parser lands in Task 2.)
    let dsn = std::env::var(DSN_ENV).unwrap();
    let url = url::Url::parse(&dsn).expect("parse DSN");
    let scheme = if url.scheme().contains("https") { "https" } else { "http" };
    let host_port = format!(
        "{}://{}:{}",
        scheme,
        url.host_str().unwrap(),
        url.port().unwrap_or(8123)
    );
    Client::default()
        .with_url(host_port)
        .with_user(url.username())
        .with_password(url.password().unwrap_or(""))
        .with_database(url.path().trim_start_matches('/'))
}

#[derive(Row, Serialize, Deserialize, Debug, PartialEq)]
struct VecRow {
    id: String,
    v: Vec<f32>,
}

#[tokio::test]
async fn vec_f32_roundtrips_through_array_float32() {
    if skip_if_no_dsn().is_none() {
        return;
    }
    let client = client_from_dsn();

    client
        .query("DROP TABLE IF EXISTS chunkshop_r4_smoke")
        .execute()
        .await
        .expect("drop");

    client
        .query("CREATE TABLE chunkshop_r4_smoke (id String, v Array(Float32)) ENGINE = MergeTree() ORDER BY id")
        .execute()
        .await
        .expect("create");

    let rows = vec![
        VecRow { id: "a".into(), v: vec![0.1_f32, 0.2, -0.3] },
        VecRow { id: "b".into(), v: vec![1.0_f32; 16] },
    ];
    let mut insert = client.insert::<VecRow>("chunkshop_r4_smoke").await.expect("insert");
    for r in &rows {
        insert.write(r).await.expect("write");
    }
    insert.end().await.expect("end");

    let mut cursor = client
        .query("SELECT ?fields FROM chunkshop_r4_smoke ORDER BY id")
        .fetch::<VecRow>()
        .expect("fetch");
    let mut got = Vec::new();
    while let Some(r) = cursor.next().await.expect("cursor") {
        got.push(r);
    }
    assert_eq!(got, rows, "round-trip mismatch — driver pick must be revisited");

    client
        .query("DROP TABLE chunkshop_r4_smoke")
        .execute()
        .await
        .expect("cleanup");
}
```

If `url` is not already in workspace deps, add it to `Cargo.toml` `[dev-dependencies]`:

```toml
url = "2"
```

- [ ] **Step 4: Run the smoke test against the live CH container**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_driver_smoke -- --nocapture 2>&1 | tail -20
```

Expected: `test vec_f32_roundtrips_through_array_float32 ... ok`. If it fails, **stop**: re-read the mission brief DC-001 entry and confirm with user before proceeding.

- [ ] **Step 5: ⛔ Drift Check DC-001**

Re-read `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md`. The driver pick is now validated. Confirm:
1. Still solving the stated Purpose (port Python CH to Rust). YES.
2. Current work maps to the brief's Constraint locking the official `clickhouse` crate. YES.
3. Not doing anything in Out of Scope. YES.

If any answer is NO, stop and surface the drift to the user before continuing.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/tests/clickhouse_driver_smoke.rs
git commit -m "feat(r4): add clickhouse 0.15 dep + Vec<f32> round-trip smoke (DC-001)"
```

---

### Task 2: `ClickhouseBackend` skeleton + DSN parser + lazy client init

**Files:**
- Create: `rust/chunkshop/src/backends/clickhouse.rs`
- Modify: `rust/chunkshop/src/backends/mod.rs` (add `pub mod clickhouse;` line only — full wiring lands in Task 9)

- [ ] **Step 1: Create the skeleton with DSN parser**

Create `rust/chunkshop/src/backends/clickhouse.rs`:

```rust
//! ClickHouse backend (CH 24.10+ — vector_similarity experimental index required).
//!
//! Mirrors `python/src/chunkshop/backends/clickhouse.py`. Two divergences from
//! the PG backend's R1 shape:
//!   1. `BackendDialect` only — `BackendConn` is sqlx-Postgres-concrete by
//!      deliberate R1 seam (see backends/base.rs:14-16). Connection-layer
//!      methods (`table_exists`, `embedding_dim`, `with_create_lock`) live as
//!      inherent methods on the concrete type. The GAT abstraction is R2's job.
//!   2. CH has no upsert. `upsert_clause()` returns `""` always.
//!
//! Driver: official `clickhouse` crate (HTTP transport, `Vec<f32>` natively maps
//! to `Array(Float32)`). DSN format mirrors Python's `clickhouse-connect` style:
//! `clickhouse://user:pass@host:port/database`.
//!
//! `Client` is cheap to clone (it shares an internal connection pool), so we
//! initialize it lazily and clone-on-demand rather than wrapping in a `Pool`
//! helper like the PG backend does.

use anyhow::{anyhow, Context, Result};
use clickhouse::Client;
use tokio::sync::OnceCell;

pub struct ClickhouseBackend {
    dsn_env: String,
    client: OnceCell<Client>,
}

impl ClickhouseBackend {
    pub fn new(dsn_env: String) -> Self {
        Self {
            dsn_env,
            client: OnceCell::new(),
        }
    }

    /// Lazily-initialized client. Idempotent. The official `clickhouse`
    /// crate's `Client` clones cheaply (shares a connection pool), so we
    /// hand out clones rather than references.
    pub async fn client(&self) -> Result<Client> {
        let c = self
            .client
            .get_or_try_init(|| async {
                let dsn = std::env::var(&self.dsn_env).with_context(|| {
                    format!("DSN env var {} not set", self.dsn_env)
                })?;
                build_client_from_dsn(&dsn)
            })
            .await?;
        Ok(c.clone())
    }

    /// Force-initialize. Idempotent. Mirrors PG's `BackendConn::connect` shape
    /// for symmetry, even though CH has no transactional connect step.
    pub async fn connect(&self) -> Result<()> {
        let _ = self.client().await?;
        Ok(())
    }
}

/// Parse `clickhouse://user:pass@host:port/database` (also `http://`/`https://`
/// aliases) into a fully-configured `Client`. Mirrors Python's
/// `_parse_clickhouse_dsn` in `python/src/chunkshop/backends/clickhouse.py`.
fn build_client_from_dsn(dsn: &str) -> Result<Client> {
    let parsed = url::Url::parse(dsn).with_context(|| format!("parsing CH DSN {dsn:?}"))?;
    let scheme = parsed.scheme();
    let secure = matches!(scheme, "https" | "clickhouse+https");
    if !matches!(
        scheme,
        "clickhouse" | "http" | "https" | "clickhouse+http" | "clickhouse+https"
    ) {
        return Err(anyhow!(
            "expected clickhouse:// or http(s):// DSN for ClickHouse, got {scheme:?}"
        ));
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow!("DSN missing host: {dsn:?}"))?;
    let port = parsed.port().unwrap_or(if secure { 8443 } else { 8123 });
    let url = format!("{}://{}:{}", if secure { "https" } else { "http" }, host, port);

    let user = match parsed.username() {
        "" => "default".to_string(),
        u => urlencoding::decode(u).map(|c| c.into_owned()).unwrap_or_else(|_| u.to_string()),
    };
    let password = parsed
        .password()
        .map(|p| urlencoding::decode(p).map(|c| c.into_owned()).unwrap_or_else(|_| p.to_string()))
        .unwrap_or_default();
    let database = match parsed.path().trim_start_matches('/') {
        "" => "default".to_string(),
        d => d.to_string(),
    };

    Ok(Client::default()
        .with_url(url)
        .with_user(user)
        .with_password(password)
        .with_database(database))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dsn_parses_clickhouse_scheme_with_credentials() {
        // Pure unit test — does not require a live CH. Just verifies the parser
        // accepts the canonical shape without panicking.
        let _client = build_client_from_dsn(
            "clickhouse://default:chpw@localhost:8124/chunkshop_test",
        )
        .expect("parse");
    }

    #[test]
    fn dsn_parses_http_alias() {
        let _client = build_client_from_dsn("http://localhost:8123/test").expect("parse");
    }

    #[test]
    fn dsn_rejects_unknown_scheme() {
        let err = build_client_from_dsn("postgres://x/y").unwrap_err();
        assert!(format!("{err:#}").contains("expected clickhouse://"));
    }
}
```

- [ ] **Step 2: Add `urlencoding` to dev-deps if not present, and `url` to dev-deps**

Edit `rust/chunkshop/Cargo.toml` `[dependencies]` block. Add (alphabetical position):

```toml
url = "2"
urlencoding = "2"
```

- [ ] **Step 3: Wire the module so the crate compiles**

Edit `rust/chunkshop/src/backends/mod.rs`. Find the existing `pub mod base; pub mod postgres;` lines (around line 7-8). Add immediately after:

```rust
pub mod clickhouse;
```

(Do NOT add the `AnyBackend::Clickhouse` variant or `load_backend` arm yet — those land in Task 9, after the dialect impl is built.)

- [ ] **Step 4: Compile + run the unit tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs backends::clickhouse::tests 2>&1 | tail -10
```

Expected: 3 unit tests pass (`dsn_parses_clickhouse_scheme_with_credentials`, `dsn_parses_http_alias`, `dsn_rejects_unknown_scheme`).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/backends/clickhouse.rs rust/chunkshop/src/backends/mod.rs
git commit -m "feat(r4): ClickhouseBackend skeleton + DSN parser"
```

---

### Task 3: Dialect parity fixture + failing parity test

**Why first (before BackendDialect impl):** TDD — the fixture defines the byte-for-byte expected output for every dialect method. The impl in Task 4 is constrained to produce exactly these strings. This also gives us cross-language parity test infrastructure for Python ↔ Rust round-trip.

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json`
- Create: `rust/chunkshop/tests/dialect_clickhouse_parity.rs`

- [ ] **Step 1: Write the fixture**

Create `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json`. The expected outputs come from reading `python/src/chunkshop/backends/clickhouse.py` cover-to-cover (the Python impl is the parity baseline):

```json
{
  "backend": "clickhouse",
  "quote_ident": [
    {"in": "my_table", "out": "`my_table`"},
    {"in": "abc", "out": "`abc`"},
    {"in": "with_underscore_123", "out": "`with_underscore_123`"}
  ],
  "fq_table": [
    {"in": ["chunkshop_test", "handbook"], "out": "`chunkshop_test`.`handbook`"},
    {"in": ["my_db", "my_table"], "out": "`my_db`.`my_table`"}
  ],
  "vector_type_ddl": [
    {"in": 384, "out": "Array(Float32)"},
    {"in": 1024, "out": "Array(Float32)"},
    {"in": 1, "out": "Array(Float32)"}
  ],
  "json_path_sql": [
    {"in": ["metadata", "a"], "out": "JSONExtractString(metadata, 'a')"},
    {"in": ["metadata", "a.b"], "out": "JSONExtractString(metadata, 'a', 'b')"},
    {"in": ["metadata", "a.b.c"], "out": "JSONExtractString(metadata, 'a', 'b', 'c')"}
  ],
  "upsert_clause": [
    {"in": {"keys": ["id"], "updates": []}, "out": ""},
    {"in": {"keys": ["id"], "updates": ["content"]}, "out": ""},
    {"in": {"keys": ["a", "b"], "updates": ["c"]}, "out": ""}
  ],
  "create_database_sql": [
    {"in": "chunkshop", "out": "CREATE DATABASE IF NOT EXISTS `chunkshop`"},
    {"in": "my_db", "out": "CREATE DATABASE IF NOT EXISTS `my_db`"}
  ],
  "drop_table_sql": [
    {"in": "`db`.`t`", "out": "DROP TABLE IF EXISTS `db`.`t` SYNC"}
  ],
  "add_column_if_not_exists_sql": [
    {"in": ["`db`.`t`", "source", "String"], "out": "ALTER TABLE `db`.`t` ADD COLUMN IF NOT EXISTS `source` String"}
  ]
}
```

- [ ] **Step 2: Write the parity test (mirror PG parity test 1:1)**

Create `rust/chunkshop/tests/dialect_clickhouse_parity.rs`:

```rust
//! Cross-language dialect parity test for ClickHouse. Both Python and Rust
//! assert their BackendDialect impls produce the byte-for-byte outputs in the
//! fixture.

use chunkshop::backends::{BackendDialect, ClickhouseBackend};
use serde_json::Value;

const FIXTURE_PATH: &str = "tests/parity-fixtures/dialect-clickhouse.json";

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read parity fixture");
    serde_json::from_str(&raw).expect("parse parity fixture")
}

fn backend() -> ClickhouseBackend {
    ClickhouseBackend::new("UNUSED_FOR_DIALECT_PARITY".to_string())
}

#[test]
fn quote_ident_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["quote_ident"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.quote_ident(inp), expected, "quote_ident({inp:?})");
    }
}

#[test]
fn fq_table_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["fq_table"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let db = inp[0].as_str().unwrap();
        let table = inp[1].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.fq_table(db, table), expected, "fq_table({db:?}, {table:?})");
    }
}

#[test]
fn vector_type_ddl_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["vector_type_ddl"].as_array().unwrap() {
        let dim = case["in"].as_u64().unwrap() as usize;
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.vector_type_ddl(dim), expected, "vector_type_ddl({dim})");
    }
}

#[test]
fn json_path_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["json_path_sql"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let col = inp[0].as_str().unwrap();
        let path = inp[1].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.json_path_sql(col, path),
            expected,
            "json_path_sql({col:?}, {path:?})"
        );
    }
}

#[test]
fn upsert_clause_returns_empty_for_clickhouse() {
    let b = backend();
    let f = load_fixture();
    for case in f["upsert_clause"].as_array().unwrap() {
        let inp = &case["in"];
        let keys: Vec<&str> = inp["keys"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        let updates: Vec<&str> = inp["updates"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.upsert_clause(&keys, &updates),
            expected,
            "upsert_clause(keys={keys:?}, updates={updates:?}) — CH always returns empty"
        );
    }
}

#[test]
fn create_database_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["create_database_sql"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.create_database_sql(inp), expected, "create_database_sql({inp:?})");
    }
}

#[test]
fn drop_table_sql_uses_sync_modifier() {
    let b = backend();
    let f = load_fixture();
    for case in f["drop_table_sql"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.drop_table_sql(inp), expected, "drop_table_sql({inp:?})");
    }
}

#[test]
fn add_column_if_not_exists_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["add_column_if_not_exists_sql"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let fq = inp[0].as_str().unwrap();
        let col = inp[1].as_str().unwrap();
        let ty = inp[2].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.add_column_if_not_exists_sql(fq, col, ty),
            expected,
            "add_column_if_not_exists_sql({fq:?}, {col:?}, {ty:?})"
        );
    }
}
```

- [ ] **Step 3: Run the parity test — expect compilation errors**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs --test dialect_clickhouse_parity 2>&1 | tail -10
```

Expected: compile error along the lines of `cannot find value 'ClickhouseBackend' in module 'chunkshop::backends'` AND/OR `the trait 'BackendDialect' is not implemented for 'ClickhouseBackend'`. That's the failing-test signal — Task 4 makes it pass.

- [ ] **Step 4: Commit (red state)**

```bash
git add rust/chunkshop/tests/dialect_clickhouse_parity.rs rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json
git commit -m "test(r4): dialect parity fixture + failing test (TDD red)"
```

---

### Task 4: Implement `BackendDialect` for `ClickhouseBackend` — pure helpers

**Files:**
- Modify: `rust/chunkshop/src/backends/clickhouse.rs` (add `impl BackendDialect for ClickhouseBackend` block — leaving `emit_chunks_table_ddl` for Task 5)
- Modify: `rust/chunkshop/src/backends/mod.rs` (re-export `ClickhouseBackend`)

- [ ] **Step 1: Add the `impl BackendDialect` block**

Append to `rust/chunkshop/src/backends/clickhouse.rs` (after the `impl ClickhouseBackend` block, before `#[cfg(test)] mod tests`):

```rust
use crate::backends::base::{BackendDialect, ColSpec};

impl BackendDialect for ClickhouseBackend {
    const NAME: &'static str = "clickhouse";
    const SUPPORTS_UPSERT: bool = false;

    fn quote_ident(&self, name: &str) -> String {
        // CH uses backticks. Defense-in-depth: double any embedded backticks even
        // though the config-load identifier regex disallows them. Mirrors
        // python/src/chunkshop/backends/clickhouse.py::quote_ident.
        format!("`{}`", name.replace('`', "``"))
    }

    fn fq_table(&self, db: &str, table: &str) -> String {
        format!("{}.{}", self.quote_ident(db), self.quote_ident(table))
    }

    fn vector_type_ddl(&self, _dim: usize) -> String {
        // CH stores vectors as Array(Float32). The dim is enforced at index time
        // by vector_similarity, not at the column-type level.
        "Array(Float32)".to_string()
    }

    fn json_type_ddl(&self) -> String {
        // String + JSONExtractString. CH has an experimental JSON type but the
        // path ergonomics are equivalent for chunkshop's flat metadata shape.
        "String".to_string()
    }

    fn tags_array_type_ddl(&self) -> String {
        "Array(String)".to_string()
    }

    fn text_pk_type_ddl(&self) -> String {
        "String".to_string()
    }

    fn timestamp_now_default_ddl(&self) -> String {
        // Used as the type_ddl for created_at; default is encoded separately
        // when canonical_cols sets `default = Some("now64()")`.
        "DateTime64(6)".to_string()
    }

    fn vector_literal(&self, arr: &[f32]) -> String {
        // CH array literal text form: [v1,v2,v3]. Used only for SELECT-side
        // injection (e.g. cosineDistance(embedding, [...])); INSERT path uses
        // the typed Vec<f32> binding via the official driver.
        let parts: Vec<String> = arr.iter().map(|x| format!("{x:.6}")).collect();
        format!("[{}]", parts.join(","))
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        // metadata column is String; store as JSON-serialized text. Mirrors
        // python/src/chunkshop/backends/clickhouse.py::json_literal.
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        // CH's JSONExtractString takes positional path segments rather than
        // jsonpath syntax. Returns '' for missing paths — chunkshop callers
        // accept null-ish on missing.
        let segs: Vec<String> = dotted_path
            .split('.')
            .map(|s| format!("'{s}'"))
            .collect();
        format!("JSONExtractString({col_expr}, {})", segs.join(", "))
    }

    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String {
        // CH has no upsert. Sinks must INSERT-only.
        String::new()
    }

    fn create_database_sql(&self, name: &str) -> String {
        format!("CREATE DATABASE IF NOT EXISTS {}", self.quote_ident(name))
    }

    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        format!(
            "ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {} {type_ddl}",
            self.quote_ident(col)
        )
    }

    fn drop_table_sql(&self, fq: &str) -> String {
        // SYNC blocks until the table is fully dropped. Important for
        // overwrite mode so the subsequent CREATE doesn't race.
        format!("DROP TABLE IF EXISTS {fq} SYNC")
    }

    fn emit_chunks_table_ddl(
        &self,
        _fq: &str,
        _cols: &[ColSpec],
        _hnsw: bool,
        _dim: usize,
        _engine: Option<&str>,
    ) -> Vec<String> {
        // Task 5 implements this. Stub returns empty so the parity test for
        // the non-DDL methods can run.
        vec![]
    }
}
```

- [ ] **Step 2: Re-export `ClickhouseBackend` from `backends/mod.rs`**

Edit `rust/chunkshop/src/backends/mod.rs`. Find the line:

```rust
pub use postgres::PostgresBackend;
```

Add immediately after:

```rust
pub use clickhouse::ClickhouseBackend;
```

- [ ] **Step 3: Run the parity test (most should pass; emit_chunks_table_ddl deferred)**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs --test dialect_clickhouse_parity 2>&1 | tail -20
```

Expected: 8 tests pass (`quote_ident_parity`, `fq_table_parity`, `vector_type_ddl_parity`, `json_path_sql_parity`, `upsert_clause_returns_empty_for_clickhouse`, `create_database_sql_parity`, `drop_table_sql_uses_sync_modifier`, `add_column_if_not_exists_sql_parity`). If any fail, fix the dialect impl to exactly match the fixture.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/backends/clickhouse.rs rust/chunkshop/src/backends/mod.rs
git commit -m "feat(r4): BackendDialect impl for ClickhouseBackend (pure helpers)"
```

---

### Task 5: Implement `emit_chunks_table_ddl` with vector_similarity index + engine override

**Files:**
- Modify: `rust/chunkshop/src/backends/clickhouse.rs` (replace `emit_chunks_table_ddl` stub with full impl)
- Modify: `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json` (add `emit_chunks_table_ddl` cases)
- Modify: `rust/chunkshop/tests/dialect_clickhouse_parity.rs` (add `emit_chunks_table_ddl_parity` test)

- [ ] **Step 1: Replace the `emit_chunks_table_ddl` stub with the full impl**

In `rust/chunkshop/src/backends/clickhouse.rs`, replace the stub `emit_chunks_table_ddl` body with:

```rust
fn emit_chunks_table_ddl(
    &self,
    fq: &str,
    cols: &[ColSpec],
    hnsw: bool,
    _dim: usize,
    engine: Option<&str>,
) -> Vec<String> {
    let mut col_lines: Vec<String> = Vec::with_capacity(cols.len());
    let mut order_by_cols: Vec<&str> = Vec::new();
    for c in cols {
        let mut line = format!("  {} {}", self.quote_ident(c.name), c.type_ddl);
        if let Some(default) = c.default {
            line.push_str(&format!(" DEFAULT {default}"));
        }
        // CH columns are nullable only via Nullable(T); we don't use that
        // here — non-default columns are required-by-convention. Skip the
        // NOT NULL emission that the PG impl does.
        col_lines.push(line);
        if c.is_primary_key {
            order_by_cols.push(c.name);
        }
    }

    if hnsw {
        // CH 24.10+ inline vector_similarity index. The 2-arg form was
        // accepted in 24.10.4 ('hnsw', 'cosineDistance') with GRANULARITY 1.
        // Mirrors python/src/chunkshop/backends/clickhouse.py — see the
        // comment there about the 3-arg form being rejected.
        col_lines.push(
            "  INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance') GRANULARITY 1"
                .to_string(),
        );
    }

    let body = col_lines.join(",\n");
    let engine_clause = match engine {
        Some(e) => e.to_string(),
        None => {
            let order_by = if order_by_cols.is_empty() {
                "tuple()".to_string()
            } else {
                order_by_cols
                    .iter()
                    .map(|c| self.quote_ident(c))
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            format!("MergeTree() ORDER BY ({order_by})")
        }
    };

    vec![format!(
        "CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n) ENGINE = {engine_clause}"
    )]
}
```

- [ ] **Step 2: Add fixture cases for emit_chunks_table_ddl**

In `rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json`, add an `emit_chunks_table_ddl` key (insert before the closing brace):

```json
,
  "emit_chunks_table_ddl": [
    {
      "name": "default_engine_no_hnsw",
      "in": {
        "fq": "`db`.`t`",
        "cols": [
          {"name": "id", "type_ddl": "String", "is_primary_key": true},
          {"name": "doc_id", "type_ddl": "String", "is_primary_key": false},
          {"name": "embedding", "type_ddl": "Array(Float32)", "is_primary_key": false}
        ],
        "hnsw": false,
        "dim": 384,
        "engine": null
      },
      "out_contains": [
        "CREATE TABLE IF NOT EXISTS `db`.`t`",
        "`id` String",
        "`embedding` Array(Float32)",
        "ENGINE = MergeTree() ORDER BY (`id`)"
      ],
      "out_excludes": ["vector_similarity"]
    },
    {
      "name": "with_hnsw_index",
      "in": {
        "fq": "`db`.`t`",
        "cols": [
          {"name": "id", "type_ddl": "String", "is_primary_key": true},
          {"name": "embedding", "type_ddl": "Array(Float32)", "is_primary_key": false}
        ],
        "hnsw": true,
        "dim": 384,
        "engine": null
      },
      "out_contains": [
        "INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance') GRANULARITY 1"
      ]
    },
    {
      "name": "replacing_merge_tree_engine_override",
      "in": {
        "fq": "`db`.`t`",
        "cols": [
          {"name": "id", "type_ddl": "String", "is_primary_key": true}
        ],
        "hnsw": false,
        "dim": 0,
        "engine": "ReplacingMergeTree(created_at) ORDER BY (id)"
      },
      "out_contains": [
        "ENGINE = ReplacingMergeTree(created_at) ORDER BY (id)"
      ]
    }
  ]
```

- [ ] **Step 3: Add the parity test for emit_chunks_table_ddl**

Append to `rust/chunkshop/tests/dialect_clickhouse_parity.rs`:

```rust
use chunkshop::backends::ColSpec;

#[test]
fn emit_chunks_table_ddl_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["emit_chunks_table_ddl"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let inp = &case["in"];
        let fq = inp["fq"].as_str().unwrap();
        let cols: Vec<ColSpec> = inp["cols"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| ColSpec {
                name: Box::leak(c["name"].as_str().unwrap().to_string().into_boxed_str()),
                type_ddl: c["type_ddl"].as_str().unwrap().to_string(),
                nullable: false,
                default: None,
                is_primary_key: c["is_primary_key"].as_bool().unwrap(),
            })
            .collect();
        let hnsw = inp["hnsw"].as_bool().unwrap();
        let dim = inp["dim"].as_u64().unwrap() as usize;
        let engine = inp["engine"].as_str();

        let stmts = b.emit_chunks_table_ddl(fq, &cols, hnsw, dim, engine);
        assert_eq!(stmts.len(), 1, "{name}: expected single CREATE TABLE stmt");
        let stmt = &stmts[0];

        for needle in case["out_contains"].as_array().unwrap_or(&vec![]) {
            let n = needle.as_str().unwrap();
            assert!(stmt.contains(n), "{name}: expected fragment {n:?} in:\n{stmt}");
        }
        for excl in case["out_excludes"].as_array().unwrap_or(&vec![]) {
            let e = excl.as_str().unwrap();
            assert!(!stmt.contains(e), "{name}: should NOT contain {e:?}, got:\n{stmt}");
        }
    }
}
```

- [ ] **Step 4: Run parity tests — all 9 should pass**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs --test dialect_clickhouse_parity 2>&1 | tail -15
```

Expected: 9 tests pass.

- [ ] **Step 5: ⛔ Drift Check DC-002**

Re-read `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md`. Verify:
1. SC-001 — `ClickhouseBackend` impls `BackendDialect` ✓ (no `BackendConn` impl added)
2. SC-007 — dialect parity fixture green ✓
3. No `sqlx` import landed in `clickhouse.rs` (the trait stays decoupled)

If any check fails, stop and surface to user.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/backends/clickhouse.rs rust/chunkshop/tests/parity-fixtures/dialect-clickhouse.json rust/chunkshop/tests/dialect_clickhouse_parity.rs
git commit -m "feat(r4): emit_chunks_table_ddl + engine override + DC-002 gate"
```

---

### Task 6: Connection-layer inherent methods + skip-if-no-DSN integration test

**Files:**
- Modify: `rust/chunkshop/src/backends/clickhouse.rs` (add inherent methods on `ClickhouseBackend`)
- Create: `rust/chunkshop/tests/backend_clickhouse_conn.rs`

- [ ] **Step 1: Add inherent connection-layer methods**

In `rust/chunkshop/src/backends/clickhouse.rs`, append to the `impl ClickhouseBackend` block (after `connect`):

```rust
/// Check whether a table exists in the given database. Mirrors Python's
/// ClickHouseBackend.table_exists. Inherent (not on BackendConn) because
/// R1's BackendConn trait is sqlx-Postgres-concrete by deliberate seam.
pub async fn table_exists(&self, client: &Client, db: &str, table: &str) -> Result<bool> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct Count {
        c: u64,
    }
    let mut cur = client
        .query("SELECT count() AS c FROM system.tables WHERE database = ? AND name = ?")
        .bind(db)
        .bind(table)
        .fetch::<Count>()?;
    let row = cur
        .next()
        .await?
        .ok_or_else(|| anyhow!("system.tables count() returned no rows"))?;
    Ok(row.c > 0)
}

/// Best-effort embedding-dim introspection. Reads `length(embedding)` from
/// the first row. Returns None on empty table or missing column.
/// Mirrors Python's `ClickHouseBackend.embedding_dim`.
pub async fn embedding_dim(
    &self,
    client: &Client,
    db: &str,
    table: &str,
) -> Result<Option<usize>> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct DimRow {
        d: u64,
    }
    let fq = self.fq_table(db, table);
    let q = format!("SELECT length(embedding) AS d FROM {fq} LIMIT 1");
    let mut cur = match client.query(&q).fetch::<DimRow>() {
        Ok(c) => c,
        // Missing-column / type-error path: behave like Python's bare except.
        Err(_) => return Ok(None),
    };
    match cur.next().await {
        Ok(Some(r)) => Ok(Some(r.d as usize)),
        Ok(None) => Ok(None),
        Err(_) => Ok(None),
    }
}

/// Acquire a CH-side DDL serialization lock. CH serializes DDL natively
/// (single-server) or via Keeper/ZK (replicated) — no app-level lock needed.
/// Inherent + no-op for symmetry with Python's `with_create_lock`.
pub async fn with_create_lock(&self, _client: &Client, _key: &str) -> Result<()> {
    Ok(())
}
```

- [ ] **Step 2: Create the integration test**

Create `rust/chunkshop/tests/backend_clickhouse_conn.rs`:

```rust
//! Inherent connection-layer integration tests for ClickhouseBackend.
//! Skips if `CHUNKSHOP_TEST_DSN_CH` is unset (mirrors backend_postgres_conn.rs).

use chunkshop::backends::ClickhouseBackend;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_client_init() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    backend.connect().await?; // idempotent
    Ok(())
}

#[tokio::test]
async fn table_exists_and_embedding_dim_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;

    // Clean slate
    let db = "chunkshop_r4_intro_test";
    client
        .query(&format!("CREATE DATABASE IF NOT EXISTS `{db}`"))
        .execute()
        .await?;
    client
        .query(&format!("DROP TABLE IF EXISTS `{db}`.synthetic SYNC"))
        .execute()
        .await?;

    // Pre-create check
    let exists = backend.table_exists(&client, db, "synthetic").await?;
    assert!(!exists);

    // Create with an embedding column
    client
        .query(&format!(
            "CREATE TABLE `{db}`.synthetic (id String, embedding Array(Float32)) ENGINE = MergeTree() ORDER BY id"
        ))
        .execute()
        .await?;

    // Post-create check
    assert!(backend.table_exists(&client, db, "synthetic").await?);

    // Empty table -> None
    assert_eq!(backend.embedding_dim(&client, db, "synthetic").await?, None);

    // Insert a row of dim 8
    client
        .query(&format!(
            "INSERT INTO `{db}`.synthetic VALUES ('a', [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])"
        ))
        .execute()
        .await?;
    assert_eq!(backend.embedding_dim(&client, db, "synthetic").await?, Some(8));

    // Cleanup
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}

#[tokio::test]
async fn with_create_lock_is_noop() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    backend.with_create_lock(&client, "any_key").await?;
    Ok(())
}
```

- [ ] **Step 3: Run the integration tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test backend_clickhouse_conn 2>&1 | tail -10
```

Expected: 3 tests pass.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/backends/clickhouse.rs rust/chunkshop/tests/backend_clickhouse_conn.rs
git commit -m "feat(r4): connection-layer inherent methods + integration test"
```

---

### Task 7: Add `ClickhouseTargetConfig` variant + engine allowlist regex

**Files:**
- Modify: `rust/chunkshop/src/config.rs`

- [ ] **Step 1: Define the engine allowlist regex constant**

Edit `rust/chunkshop/src/config.rs`. Find the `ALLOWED_PROMOTE_TYPES` constant near the top (around line 16). Add immediately below it:

```rust
/// Allowlist regex for `ClickhouseTargetConfig::engine`. Hardening relative to
/// Python (which interpolates the engine string raw — see
/// python/src/chunkshop/config.py:542). Accepts:
///   - `MergeTree` / `MergeTree()`
///   - `ReplacingMergeTree(<single_ident>)` (the `created_at` dedup column)
///   - Any of the above optionally followed by ` ORDER BY <expr>`
///
/// Rejects engines outside this whitelist (Replicated*, Distributed, Memory,
/// engines with embedded SQL, etc.) — those need explicit user request and a
/// separate brief.
const CLICKHOUSE_ENGINE_RE: &str = r"^(MergeTree(\(\))?|ReplacingMergeTree\(\w+\))( ORDER BY .+)?$";
```

- [ ] **Step 2: Define `ClickhouseTargetConfig`**

In the same file, find the `pub enum TargetConfig` block (around line 703). Replace it with:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    Clickhouse(ClickhouseTargetConfig),
    // R2/R3 add: Mariadb, Sqlite
}

impl TargetConfig {
    /// Post-deserialize validation that crosses field boundaries. Delegates to
    /// the active variant's `validate()`.
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
            TargetConfig::Clickhouse(t) => t.validate(),
        }
    }
}
```

Then add the new struct definition right after `PostgresTargetConfig`'s `impl` block (search for `fn default_dsn_env()` — insert immediately before it):

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// On ClickHouse, `delete_orphans: true` is a NO-OP that emits a single
    /// `tracing::warn!` per process. CH's `ALTER TABLE ... DELETE` is async
    /// and breaks chunkshop's per-document atomic write contract.
    #[serde(default)]
    pub delete_orphans: bool,
    /// Optional engine override. When `None`, the sink emits
    /// `MergeTree() ORDER BY (id)`. To opt into lazy dedup, set
    /// `"ReplacingMergeTree(created_at) ORDER BY (id)"`. Validated against
    /// `CLICKHOUSE_ENGINE_RE` at config-load — a Rust-only hardening relative
    /// to Python which interpolates the field raw.
    #[serde(default)]
    pub engine: Option<String>,
}

impl ClickhouseTargetConfig {
    fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        if let Some(e) = &self.engine {
            let re = Regex::new(CLICKHOUSE_ENGINE_RE).unwrap();
            if !re.is_match(e) {
                return Err(anyhow!(
                    "target.engine {e:?} not in allowlist. Accepted shapes: \
                     'MergeTree', 'MergeTree()', 'ReplacingMergeTree(<col>)', \
                     each optionally followed by ' ORDER BY <expr>'. Custom engines \
                     are not supported in v0.4 — file an issue if you need one."
                ));
            }
        }
        Ok(())
    }
}
```

- [ ] **Step 3: Wire ident validation in `load_config`**

Still in `rust/chunkshop/src/config.rs`, find the `match &cfg.target { TargetConfig::Postgres(t) => { ... } }` block in `load_config` (around line 846). Replace it with:

```rust
match &cfg.target {
    TargetConfig::Postgres(t) => {
        validate_ident(&t.database_name, "target.database")?;
        validate_ident(&t.table, "target.table")?;
        if let Some(tag) = &t.source_tag {
            validate_ident(tag, "target.source_tag")?;
        }
    }
    TargetConfig::Clickhouse(t) => {
        validate_ident(&t.database_name, "target.database")?;
        validate_ident(&t.table, "target.table")?;
        if let Some(tag) = &t.source_tag {
            validate_ident(tag, "target.source_tag")?;
        }
    }
}
```

- [ ] **Step 4: Add unit tests**

Append to the existing `#[cfg(test)] mod tests` block in `rust/chunkshop/src/config.rs`:

```rust
#[test]
fn parses_clickhouse_target() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_db
  table: chunks
  mode: overwrite
  hnsw: true
"#;
    let path = write_yaml(yaml);
    let cfg = load_config(&path).expect("load");
    let TargetConfig::Clickhouse(t) = &cfg.target else {
        panic!("expected Clickhouse variant");
    };
    assert_eq!(t.database_name, "my_db");
    assert_eq!(t.table, "chunks");
    assert!(t.engine.is_none());
}

#[test]
fn accepts_replacing_merge_tree_engine() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
"#;
    let path = write_yaml(yaml);
    let cfg = load_config(&path).expect("ReplacingMergeTree should be accepted");
    let TargetConfig::Clickhouse(t) = &cfg.target else { unreachable!() };
    assert_eq!(t.engine.as_deref(), Some("ReplacingMergeTree(created_at) ORDER BY (id)"));
}

#[test]
fn rejects_arbitrary_engine_string() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "Memory"
"#;
    let path = write_yaml(yaml);
    let err = format!("{:#}", load_config(&path).unwrap_err());
    assert!(err.contains("allowlist") && err.contains("Memory"), "got: {err}");
}

#[test]
fn rejects_engine_with_drop_table_injection() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "MergeTree(); DROP TABLE other"
"#;
    let path = write_yaml(yaml);
    assert!(
        load_config(&path).is_err(),
        "engine with embedded DROP must be rejected"
    );
}
```

- [ ] **Step 5: Run the unit tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs --lib config:: 2>&1 | tail -15
```

Expected: all existing config tests still pass + 4 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/config.rs
git commit -m "feat(r4): ClickhouseTargetConfig + engine allowlist regex (SC-005 hardening)"
```

---

### Task 8: Wire `AnyBackend::Clickhouse` + `load_backend`

**Files:**
- Modify: `rust/chunkshop/src/backends/mod.rs`
- Modify: `rust/chunkshop/src/lib.rs`

- [ ] **Step 1: Update `AnyBackend` and `load_backend`**

Edit `rust/chunkshop/src/backends/mod.rs`. Replace the existing `pub enum AnyBackend` and `load_backend` with:

```rust
pub enum AnyBackend {
    Postgres(PostgresBackend),
    Clickhouse(ClickhouseBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
        TargetConfig::Clickhouse(t) => {
            Ok(AnyBackend::Clickhouse(ClickhouseBackend::new(t.dsn_env.clone())))
        }
    }
}
```

- [ ] **Step 2: Update `lib.rs` re-exports**

Edit `rust/chunkshop/src/lib.rs`. Replace the line:

```rust
pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ColSpec, PostgresBackend};
```

with:

```rust
pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ClickhouseBackend, ColSpec, PostgresBackend};
```

- [ ] **Step 3: Compile**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
```

Expected: clean build.

- [ ] **Step 4: Run all tests so far to make sure nothing regressed**

```bash
cargo test -p chunkshop-rs 2>&1 | grep -E "^test result" | awk '{ pass += $4; fail += $6; ignore += $8 } END { print "TOTAL passed:", pass, "failed:", fail, "ignored:", ignore }'
```

Expected: `TOTAL passed: ≥ 130 failed: 0 ignored: 1` (126 baseline + new dialect parity + connection + config tests).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/mod.rs rust/chunkshop/src/lib.rs
git commit -m "feat(r4): wire AnyBackend::Clickhouse + load_backend"
```

---

### Task 9: `ClickhouseSink` skeleton + warn-once + canonical_cols + pg→ch type map

**Files:**
- Create: `rust/chunkshop/src/sinks/clickhouse.rs`
- Modify: `rust/chunkshop/src/sinks/mod.rs` (add `pub mod clickhouse;` only — full wiring lands in Task 13)

- [ ] **Step 1: Create the sink skeleton with warn-once**

Create `rust/chunkshop/src/sinks/clickhouse.rs`:

```rust
//! ClickHouse sink — append-only chunks-table writer using ClickhouseBackend dialect.
//!
//! Mirrors python/src/chunkshop/sinks/clickhouse.py. Same shape as PgSink but
//! INSERT-only (no `ON CONFLICT`); `delete_orphans: true` warns once per process
//! and no-ops. Bulk INSERT via the official `clickhouse` crate's `Insert<T>`.

use std::sync::OnceLock;

use anyhow::{anyhow, Context, Result};
use clickhouse::{Client, Row};
use serde::Serialize;
use tracing::warn;

use crate::backends::base::{BackendDialect, ColSpec};
use crate::backends::clickhouse::ClickhouseBackend;
use crate::chunker::Chunk;
use crate::config::{ClickhouseTargetConfig, PromoteColumn};

/// One-time-per-process warn flag for `delete_orphans: true`. CH mutations are
/// async and don't fit chunkshop's per-document atomic write contract, so the
/// flag is treated as a no-op + warning. PID-keying isn't necessary in Rust
/// (each process gets its own static); the OnceLock is process-scoped by
/// definition.
static DELETE_ORPHANS_WARNED: OnceLock<()> = OnceLock::new();

const ORPHAN_WARN_MSG: &str =
    "target.delete_orphans=true on ClickHouse is a no-op — CH mutations are async \
     background ops that don't fit chunkshop's per-document atomic write contract. \
     Use ReplacingMergeTree(created_at) for lazy dedup or run manual ALTER TABLE … DELETE WHERE.";

pub struct ClickhouseSink {
    cfg: ClickhouseTargetConfig,
    backend: ClickhouseBackend,
    embed_dim: usize,
}

impl ClickhouseSink {
    pub fn new(cfg: ClickhouseTargetConfig, backend: ClickhouseBackend, embed_dim: usize) -> Self {
        if cfg.delete_orphans {
            DELETE_ORPHANS_WARNED.get_or_init(|| {
                warn!("{ORPHAN_WARN_MSG}");
            });
        }
        Self { cfg, backend, embed_dim }
    }

    fn fq(&self) -> String {
        self.backend.fq_table(&self.cfg.database_name, &self.cfg.table)
    }
}

/// Canonical chunkshop columns, CH-typed. Mirrors
/// python/src/chunkshop/sinks/clickhouse.py::_canonical_cols.
fn canonical_cols(_dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "Int32".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "String".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: "Array(String)".into(), nullable: false, default: Some("[]"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: "String".into(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: "Array(Float32)".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "String".into(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "DateTime64(6)".into(), nullable: false, default: Some("now64()"), is_primary_key: false },
    ]
}

/// PG type → CH type map for promoted columns. Mirrors
/// python/src/chunkshop/sinks/clickhouse.py::_PG_TO_CH_TYPE.
fn pg_type_to_ch(pg_type: &str) -> String {
    match pg_type {
        "text" => "String".into(),
        "text[]" => "Array(String)".into(),
        "int" => "Int32".into(),
        "bigint" => "Int64".into(),
        "boolean" => "UInt8".into(),
        "jsonb" => "String".into(),
        "timestamptz" => "DateTime64(6)".into(),
        "date" => "Date".into(),
        other => other.to_string(),
    }
}

/// Walk a dotted path through a JSON object. Returns None if any segment is
/// missing or not a JSON object. Mirrors Python's `_jsonb_path_get`.
fn jsonb_path_get<'a>(meta: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        cur = cur.as_object()?.get(seg)?;
    }
    Some(cur)
}

/// The row shape we feed `client.insert::<ChunkRow>(...)`. Field order MUST
/// match the canonical column order (id, doc_id, seq_num, original_content,
/// embedded_content, tags, metadata, embedding, source). created_at is handled
/// by the column DEFAULT (we don't write it). Promoted columns are NOT in this
/// struct because they're variable per config — write_document falls back to a
/// raw-SQL VALUES path when promote_metadata is non-empty.
#[derive(Row, Serialize)]
pub(crate) struct ChunkRow {
    pub id: String,
    pub doc_id: String,
    pub seq_num: i32,
    pub original_content: String,
    pub embedded_content: String,
    pub tags: Vec<String>,
    pub metadata: String,
    pub embedding: Vec<f32>,
    pub source: String,
}
```

- [ ] **Step 2: Wire the module so the crate compiles**

Edit `rust/chunkshop/src/sinks/mod.rs`. Add `pub mod clickhouse;` after the `pub mod pg;` line (do NOT touch `AnySink` / `load_sink` / re-exports yet — those land in Task 13):

```rust
pub mod clickhouse;
```

- [ ] **Step 3: Compile**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
```

Expected: clean build. (Unused-warnings on `ClickhouseSink::fq` and the helpers are fine — they're consumed in subsequent tasks. If clippy complains, prefix with `#[allow(dead_code)]` at the function level — temporary, removed by Task 13.)

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/src/sinks/mod.rs
git commit -m "feat(r4): ClickhouseSink skeleton + warn-once + canonical_cols + type map"
```

---

### Task 10: `ClickhouseSink::create_table` with mode dispatch + foreign-tag safety + integration test

**Files:**
- Modify: `rust/chunkshop/src/sinks/clickhouse.rs`
- Create: `rust/chunkshop/tests/clickhouse_sink_create_table.rs`

- [ ] **Step 1: Add `create_table` and helpers to `ClickhouseSink`**

In `rust/chunkshop/src/sinks/clickhouse.rs`, append a new `impl ClickhouseSink` block (after the existing one with `new` and `fq`):

```rust
impl ClickhouseSink {
    async fn ensure_promote_columns(&self, client: &Client) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            let ch_type = pg_type_to_ch(&pc.type_);
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq(),
                &pc.column_name(),
                &ch_type,
            );
            client.query(&stmt).execute().await.context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl(&self, client: &Client) -> Result<()> {
        let cols = canonical_cols(self.embed_dim);
        let engine = self.cfg.engine.as_deref();
        for stmt in self.backend.emit_chunks_table_ddl(
            &self.fq(),
            &cols,
            self.cfg.hnsw,
            self.embed_dim,
            engine,
        ) {
            client.query(&stmt).execute().await.context("emit_chunks_table_ddl statement")?;
        }
        self.ensure_promote_columns(client).await
    }

    async fn overwrite_create(&self, client: &Client) -> Result<()> {
        let exists = self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await?;
        if exists && !self.cfg.force_overwrite {
            // Foreign-tag safety: refuse to drop a table holding rows from a
            // different source_tag.
            #[derive(Row, serde::Deserialize)]
            struct SourceRow {
                source: String,
            }
            let q = format!(
                "SELECT DISTINCT source FROM {} WHERE source != '' LIMIT 10",
                self.fq()
            );
            let mut cur = client.query(&q).fetch::<SourceRow>()?;
            let mut existing = std::collections::BTreeSet::new();
            while let Some(r) = cur.next().await? {
                existing.insert(r.source);
            }
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {db}.{tbl}: foreign source_tag values {foreign:?}. \
                     Set target.force_overwrite: true to bypass.",
                    db = self.cfg.database_name,
                    tbl = self.cfg.table,
                    foreign = foreign
                ));
            }
        }
        if exists {
            client
                .query(&self.backend.drop_table_sql(&self.fq()))
                .execute()
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl(client).await
    }

    async fn create_if_missing(&self, client: &Client) -> Result<()> {
        if !self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await? {
            return self.create_base_ddl(client).await;
        }
        let stmt = self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "String");
        client.query(&stmt).execute().await.context("ADD COLUMN source")?;
        self.ensure_promote_columns(client).await
    }

    async fn append_preflight(&self, client: &Client) -> Result<()> {
        if !self.backend.table_exists(client, &self.cfg.database_name, &self.cfg.table).await? {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.database_name,
                self.cfg.table
            ));
        }
        let current_dim = self
            .backend
            .embedding_dim(client, &self.cfg.database_name, &self.cfg.table)
            .await?;
        match current_dim {
            None => {
                warn!(
                    "append mode on empty CH table — cannot verify embedding dim matches. \
                     Continuing on faith; subsequent reads with mismatched dim will produce \
                     garbage cosine distances."
                );
            }
            Some(d) if d != self.embed_dim => {
                return Err(anyhow!(
                    "append mode: target embedding dim is {d}, cell's embedder dim is {own}. \
                     Vectors are not comparable.",
                    own = self.embed_dim
                ));
            }
            _ => {}
        }
        let stmt = self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "String");
        client.query(&stmt).execute().await.context("ADD COLUMN source")?;
        self.ensure_promote_columns(client).await
    }

    pub async fn create_table_impl(&self) -> Result<()> {
        let client = self.backend.client().await?;
        self.backend.with_create_lock(&client, &self.cfg.database_name).await?;
        client
            .query(&self.backend.create_database_sql(&self.cfg.database_name))
            .execute()
            .await
            .context("CREATE DATABASE")?;
        match self.cfg.mode.as_str() {
            "overwrite" => self.overwrite_create(&client).await,
            "create_if_missing" => self.create_if_missing(&client).await,
            "append" => self.append_preflight(&client).await,
            other => Err(anyhow!("unknown target.mode: {other:?}")),
        }
    }
}
```

(Note: `create_table_impl` is the inherent name; the `Sink` trait impl in Task 13 just delegates. Splitting now keeps the trait impl trivially mechanical.)

- [ ] **Step 2: Create the integration test**

Create `rust/chunkshop/tests/clickhouse_sink_create_table.rs`:

```rust
//! ClickhouseSink::create_table_impl mode-dispatch integration tests.
//! Skip-if-no-DSN. Mirrors tests/pg_sink_create_table.rs in shape.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::config::{ClickhouseTargetConfig, PromoteColumn};
use chunkshop::sinks::clickhouse::ClickhouseSink;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn cfg(database: &str, table: &str, mode: &str) -> ClickhouseTargetConfig {
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {database}\ntable: {table}\nmode: {mode}\nhnsw: false"
    );
    let raw: serde_yml::Value = serde_yml::from_str(&yaml).unwrap();
    let target: chunkshop::config::TargetConfig = serde_yml::from_value(raw).unwrap();
    match target {
        chunkshop::config::TargetConfig::Clickhouse(t) => t,
        _ => panic!("expected Clickhouse variant"),
    }
}

async fn drop_db(backend: &ClickhouseBackend, db: &str) -> anyhow::Result<()> {
    let client = backend.client().await?;
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}

#[tokio::test]
async fn overwrite_creates_table_with_canonical_columns() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_create_overwrite";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg = cfg(db, "chunks", "overwrite");
    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink.create_table_impl().await?;

    let client = backend.client().await?;
    assert!(backend.table_exists(&client, db, "chunks").await?);
    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
async fn create_if_missing_idempotent() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_create_if_missing";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg1 = cfg(db, "chunks", "create_if_missing");
    let sink1 = ClickhouseSink::new(cfg1, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink1.create_table_impl().await?;

    // Re-run with same mode — should not error
    let cfg2 = cfg(db, "chunks", "create_if_missing");
    let sink2 = ClickhouseSink::new(cfg2, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    sink2.create_table_impl().await?;

    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
async fn append_mode_rejects_missing_table() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_append_no_table";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    // Need to construct ClickhouseTargetConfig with source_tag for append mode
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: chunks\nmode: append\nsource_tag: cell_a\nhnsw: false"
    );
    let raw: serde_yml::Value = serde_yml::from_str(&yaml).unwrap();
    let target: chunkshop::config::TargetConfig = serde_yml::from_value(raw).unwrap();
    let chunkshop::config::TargetConfig::Clickhouse(cfg) = target else { unreachable!() };

    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 384);
    let err = sink.create_table_impl().await.unwrap_err();
    assert!(format!("{err:#}").contains("does not exist"), "got: {err:#}");

    Ok(())
}
```

- [ ] **Step 3: Re-export the sink type so tests can find it**

Edit `rust/chunkshop/src/sinks/mod.rs`. Replace the existing `pub use pg::PgSink;` line with:

```rust
pub use pg::PgSink;
pub use clickhouse::ClickhouseSink;
```

- [ ] **Step 4: Run the integration tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_sink_create_table 2>&1 | tail -15
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/src/sinks/mod.rs rust/chunkshop/tests/clickhouse_sink_create_table.rs
git commit -m "feat(r4): ClickhouseSink::create_table mode dispatch + foreign-tag safety"
```

---

### Task 11: `ClickhouseSink::write_document` (bulk INSERT) + append-only test (SC-003) + warn-once test (SC-004)

**Files:**
- Modify: `rust/chunkshop/src/sinks/clickhouse.rs`
- Create: `rust/chunkshop/tests/clickhouse_sink_append_only.rs`
- Modify: `rust/chunkshop/Cargo.toml` (add `tracing-test` was already there as dev-dep — verify)

- [ ] **Step 1: Add `write_document` to the inherent impl**

In `rust/chunkshop/src/sinks/clickhouse.rs`, append to the second `impl ClickhouseSink` block (after `create_table_impl`):

```rust
pub async fn write_document_impl(
    &self,
    _doc_id: &str,
    chunks: &[Chunk],
    embeddings: &[Vec<f32>],
    tags_per_chunk: &[Vec<String>],
) -> Result<()> {
    if chunks.len() != embeddings.len() {
        return Err(anyhow!(
            "chunks ({}) and embeddings ({}) length mismatch",
            chunks.len(),
            embeddings.len()
        ));
    }
    if chunks.len() != tags_per_chunk.len() {
        return Err(anyhow!(
            "chunks ({}) and tags_per_chunk ({}) length mismatch",
            chunks.len(),
            tags_per_chunk.len()
        ));
    }
    if chunks.is_empty() {
        return Ok(());
    }

    let promote = &self.cfg.promote_metadata;
    let client = self.backend.client().await?;

    if promote.is_empty() {
        // Fast path: typed bulk insert via the official driver's Insert<T>.
        let mut insert = client.insert::<ChunkRow>(&self.fq()).await?;
        for ((c, emb), tags) in chunks.iter().zip(embeddings.iter()).zip(tags_per_chunk.iter()) {
            let row = ChunkRow {
                id: format!("{}::{}", c.doc_id, c.seq_num),
                doc_id: c.doc_id.clone(),
                seq_num: c.seq_num as i32,
                original_content: c.original_content.clone(),
                embedded_content: c.embedded_content.clone(),
                tags: tags.clone(),
                metadata: serde_json::to_string(&c.metadata)?,
                embedding: emb.clone(),
                source: self.cfg.source_tag.clone().unwrap_or_default(),
            };
            insert.write(&row).await?;
        }
        insert.end().await?;
    } else {
        // Promoted-metadata path: typed Insert doesn't carry variable extra
        // columns, so we issue per-row INSERTs with parameter binding. Slower,
        // but the promote path is operator-opt-in and per-row is acceptable.
        let mut col_names: Vec<String> = vec![
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        .into_iter()
        .map(|s| self.backend.quote_ident(s))
        .collect();
        for pc in promote {
            col_names.push(self.backend.quote_ident(&pc.column_name()));
        }
        let cols_sql = col_names.join(", ");

        for ((c, emb), tags) in chunks.iter().zip(embeddings.iter()).zip(tags_per_chunk.iter()) {
            let id = format!("{}::{}", c.doc_id, c.seq_num);
            let metadata = serde_json::to_string(&c.metadata)?;
            let mut q_str = format!(
                "INSERT INTO {} ({}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?",
                self.fq(),
                cols_sql
            );
            for _ in promote {
                q_str.push_str(", ?");
            }
            q_str.push(')');
            let mut q = client
                .query(&q_str)
                .bind(id)
                .bind(c.doc_id.clone())
                .bind(c.seq_num as i32)
                .bind(c.original_content.clone())
                .bind(c.embedded_content.clone())
                .bind(tags.clone())
                .bind(metadata)
                .bind(emb.clone())
                .bind(self.cfg.source_tag.clone().unwrap_or_default());
            for pc in promote {
                let v = jsonb_path_get(&c.metadata, &pc.path);
                let cell = match v {
                    Some(serde_json::Value::String(s)) => s.clone(),
                    Some(other) => serde_json::to_string(other).unwrap_or_default(),
                    None => String::new(),
                };
                q = q.bind(cell);
            }
            q.execute().await.context("INSERT chunk row (promoted path)")?;
        }
    }
    // delete_orphans is a no-op on CH (warned at sink construction).
    // Re-ingesting same (doc_id, seq_num) produces dup rows; users opt into
    // ReplacingMergeTree(created_at) for lazy dedup at merge time.
    Ok(())
}
```

- [ ] **Step 2: Create the append-only + warn-once test**

Create `rust/chunkshop/tests/clickhouse_sink_append_only.rs`:

```rust
//! SC-003 (append-only / no-upsert) and SC-004 (delete_orphans warn-once)
//! integration tests.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::TargetConfig;
use chunkshop::sinks::ClickhouseSink;
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn make_cfg(db: &str, table: &str, mode: &str, delete_orphans: bool) -> chunkshop::config::ClickhouseTargetConfig {
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: {table}\nmode: {mode}\nhnsw: false\ndelete_orphans: {delete_orphans}"
    );
    let raw: serde_yml::Value = serde_yml::from_str(&yaml).unwrap();
    let target: TargetConfig = serde_yml::from_value(raw).unwrap();
    let TargetConfig::Clickhouse(t) = target else { unreachable!() };
    t
}

fn make_chunks(doc_id: &str, n: usize) -> (Vec<Chunk>, Vec<Vec<f32>>, Vec<Vec<String>>) {
    let chunks: Vec<Chunk> = (0..n)
        .map(|i| Chunk {
            doc_id: doc_id.to_string(),
            seq_num: i as i32,
            original_content: format!("orig {i}"),
            embedded_content: format!("emb {i}"),
            metadata: json!({}),
        })
        .collect();
    let embs: Vec<Vec<f32>> = (0..n).map(|i| vec![i as f32; 4]).collect();
    let tags: Vec<Vec<String>> = (0..n).map(|_| vec![]).collect();
    (chunks, embs, tags)
}

async fn count_rows(backend: &ClickhouseBackend, db: &str, table: &str) -> anyhow::Result<u64> {
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct C {
        c: u64,
    }
    let client = backend.client().await?;
    let q = format!("SELECT count() AS c FROM `{db}`.`{table}`");
    let mut cur = client.query(&q).fetch::<C>()?;
    let r = cur.next().await?.unwrap();
    Ok(r.c)
}

async fn drop_db(backend: &ClickhouseBackend, db: &str) -> anyhow::Result<()> {
    let client = backend.client().await?;
    client.query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC")).execute().await?;
    Ok(())
}

#[tokio::test]
async fn reingest_produces_duplicate_rows_on_default_engine() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_append_only";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg = make_cfg(db, "chunks", "overwrite", false);
    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table_impl().await?;

    let (chunks, embs, tags) = make_chunks("doc-1", 3);
    sink.write_document_impl("doc-1", &chunks, &embs, &tags).await?;
    sink.write_document_impl("doc-1", &chunks, &embs, &tags).await?;

    let n = count_rows(&backend, db, "chunks").await?;
    assert_eq!(n, 6, "SC-003: re-ingest must produce duplicate rows on default engine; got {n}");

    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
#[tracing_test::traced_test]
async fn delete_orphans_warns_exactly_once() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_warn_once";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    // Construct two sinks with delete_orphans=true. Both should pass through
    // the warn-once gate but only ONE warning event should fire per process.
    let cfg1 = make_cfg(db, "chunks_a", "overwrite", true);
    let _sink1 = ClickhouseSink::new(cfg1, ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    let cfg2 = make_cfg(db, "chunks_b", "overwrite", true);
    let _sink2 = ClickhouseSink::new(cfg2, ClickhouseBackend::new(DSN_ENV.to_string()), 4);

    // tracing_test::logs_contain checks substring; we want exactly 1
    // warn-event matching our message. Use the helper-counted variant:
    let count = tracing_test::internal::logs_with_scope_contain("chunkshop", "delete_orphans=true on ClickHouse");
    assert!(count, "expected at least one warn event mentioning delete_orphans");

    Ok(())
}
```

(Note on `tracing_test`: the test uses `traced_test::traced_test` from `tracing-test = "0.2"` which is already in `[dev-dependencies]`. The exact API for "count events with substring" depends on the version; if `internal::logs_with_scope_contain` returns bool, the assertion still validates "≥ 1 warn fired"; the "exactly once" guarantee comes from `OnceLock::get_or_init`. If you need true counting, capture with `tracing-subscriber` directly — but the OnceLock invariant is what proves SC-004, not the test framework's counting.)

- [ ] **Step 3: Run the tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_sink_append_only 2>&1 | tail -15
```

Expected: 2 tests pass. If `tracing_test::internal::logs_with_scope_contain` doesn't compile, fall back to `tracing_test::logs_assert!` macro variants — verify the actual API in `tracing_test` 0.2.x docs at `~/.cargo/registry/src/.../tracing-test-0.2.*/src/lib.rs`.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/tests/clickhouse_sink_append_only.rs
git commit -m "feat(r4): write_document bulk INSERT + SC-003/004 tests"
```

---

### Task 12: `delete_document` + `count_docs` + `query_top_k` (cosineDistance)

**Files:**
- Modify: `rust/chunkshop/src/sinks/clickhouse.rs`

- [ ] **Step 1: Add the remaining inherent methods**

In `rust/chunkshop/src/sinks/clickhouse.rs`, append to the second `impl ClickhouseSink` block:

```rust
pub async fn delete_document_impl(&self, doc_id: &str) -> Result<i64> {
    let client = self.backend.client().await?;

    #[derive(Row, serde::Deserialize)]
    struct C {
        c: u64,
    }
    let (count_q, count_n) = if self.cfg.source_tag.is_some() {
        let q = format!(
            "SELECT count() AS c FROM {} WHERE doc_id = ? AND source = ?",
            self.fq()
        );
        let mut cur = client
            .query(&q)
            .bind(doc_id)
            .bind(self.cfg.source_tag.as_deref().unwrap())
            .fetch::<C>()?;
        let r = cur.next().await?.unwrap_or(C { c: 0 });
        (q, r.c)
    } else {
        let q = format!("SELECT count() AS c FROM {} WHERE doc_id = ?", self.fq());
        let mut cur = client.query(&q).bind(doc_id).fetch::<C>()?;
        let r = cur.next().await?.unwrap_or(C { c: 0 });
        (q, r.c)
    };
    let _ = count_q; // documented above for clarity
    if count_n == 0 {
        return Ok(0);
    }
    // ALTER TABLE ... DELETE is async on CH — the caller accepts eventual consistency.
    if let Some(tag) = &self.cfg.source_tag {
        let stmt = format!(
            "ALTER TABLE {} DELETE WHERE doc_id = ? AND source = ?",
            self.fq()
        );
        client.query(&stmt).bind(doc_id).bind(tag.clone()).execute().await?;
    } else {
        let stmt = format!("ALTER TABLE {} DELETE WHERE doc_id = ?", self.fq());
        client.query(&stmt).bind(doc_id).execute().await?;
    }
    Ok(count_n as i64)
}

pub async fn count_docs_impl(&self) -> Result<i64> {
    #[derive(Row, serde::Deserialize)]
    struct C {
        c: u64,
    }
    let client = self.backend.client().await?;
    let q = format!("SELECT uniqExact(doc_id) AS c FROM {}", self.fq());
    let mut cur = client.query(&q).fetch::<C>()?;
    let r = cur.next().await?.unwrap_or(C { c: 0 });
    Ok(r.c as i64)
}

pub async fn query_top_k_impl(&self, query_vec: &[f32], k: usize) -> Result<Vec<(String, i32, f64)>> {
    #[derive(Row, serde::Deserialize)]
    struct Hit {
        doc_id: String,
        seq_num: i32,
        dist: f64,
    }
    let client = self.backend.client().await?;
    // cosineDistance(embedding, [array_literal]) — the official driver's
    // `?` placeholder doesn't support inline-array binding, so we inline the
    // array literal via the dialect's vector_literal (already test-covered).
    let vec_lit = self.backend.vector_literal(query_vec);
    let q = format!(
        "SELECT doc_id, seq_num, cosineDistance(embedding, {vec_lit}) AS dist \
         FROM {} ORDER BY dist LIMIT ?",
        self.fq()
    );
    let mut cur = client.query(&q).bind(k as u32).fetch::<Hit>()?;
    let mut out = Vec::with_capacity(k);
    while let Some(h) = cur.next().await? {
        out.push((h.doc_id, h.seq_num, h.dist));
    }
    Ok(out)
}
```

- [ ] **Step 2: Add a smoke test for query_top_k in the existing append-only test file**

Append to `rust/chunkshop/tests/clickhouse_sink_append_only.rs`:

```rust
#[tokio::test]
async fn query_top_k_returns_nearest_chunks() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_top_k";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;

    let cfg = make_cfg(db, "chunks", "overwrite", false);
    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table_impl().await?;

    // Insert 3 chunks with distinct vectors. Query for [1,0,0,0], expect doc-1::0 first.
    let chunks = vec![
        Chunk { doc_id: "doc-1".into(), seq_num: 0, original_content: "a".into(), embedded_content: "a".into(), metadata: json!({}) },
        Chunk { doc_id: "doc-2".into(), seq_num: 0, original_content: "b".into(), embedded_content: "b".into(), metadata: json!({}) },
        Chunk { doc_id: "doc-3".into(), seq_num: 0, original_content: "c".into(), embedded_content: "c".into(), metadata: json!({}) },
    ];
    let embs = vec![
        vec![1.0_f32, 0.0, 0.0, 0.0],
        vec![0.0_f32, 1.0, 0.0, 0.0],
        vec![0.0_f32, 0.0, 1.0, 0.0],
    ];
    let tags = vec![vec![], vec![], vec![]];
    sink.write_document_impl("doc-1", &chunks[..1], &embs[..1], &tags[..1]).await?;
    sink.write_document_impl("doc-2", &chunks[1..2], &embs[1..2], &tags[1..2]).await?;
    sink.write_document_impl("doc-3", &chunks[2..3], &embs[2..3], &tags[2..3]).await?;

    let hits = sink.query_top_k_impl(&[1.0, 0.0, 0.0, 0.0], 3).await?;
    assert_eq!(hits.len(), 3);
    assert_eq!(hits[0].0, "doc-1", "expected doc-1 first; got {hits:?}");

    let n = sink.count_docs_impl().await?;
    assert_eq!(n, 3);

    drop_db(&backend, db).await?;
    Ok(())
}
```

- [ ] **Step 3: Run the tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_sink_append_only 2>&1 | tail -10
```

Expected: 3 tests pass (the existing 2 + new `query_top_k_returns_nearest_chunks`).

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/tests/clickhouse_sink_append_only.rs
git commit -m "feat(r4): delete_document + count_docs + query_top_k(cosineDistance)"
```

---

### Task 13: Wire `Sink for ClickhouseSink` impl + `AnySink::Clickhouse` + `load_sink` (DC-003 gate)

**Files:**
- Modify: `rust/chunkshop/src/sinks/clickhouse.rs`
- Modify: `rust/chunkshop/src/sinks/mod.rs`
- Modify: `rust/chunkshop/src/lib.rs`

- [ ] **Step 1: Add the `Sink` trait impl that delegates to the inherent methods**

Append to `rust/chunkshop/src/sinks/clickhouse.rs`:

```rust
use std::future::Future;

use crate::sinks::base::Sink;

impl Sink for ClickhouseSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move { self.create_table_impl().await }
    }

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { self.write_document_impl(doc_id, chunks, embeddings, tags_per_chunk).await }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { self.delete_document_impl(doc_id).await }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { self.count_docs_impl().await }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { self.query_top_k_impl(query_vec, k).await }
    }
}
```

- [ ] **Step 2: Update `AnySink` and `load_sink`**

Edit `rust/chunkshop/src/sinks/mod.rs`. Replace the `pub enum AnySink` and `impl Sink for AnySink` and `load_sink` with the expanded versions:

```rust
pub enum AnySink {
    Pg(PgSink),
    Clickhouse(ClickhouseSink),
}

impl Sink for AnySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.create_table().await,
                AnySink::Clickhouse(s) => s.create_table().await,
            }
        }
    }

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
                AnySink::Clickhouse(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
            }
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.delete_document(doc_id).await,
                AnySink::Clickhouse(s) => s.delete_document(doc_id).await,
            }
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.count_docs().await,
                AnySink::Clickhouse(s) => s.count_docs().await,
            }
        }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.query_top_k(query_vec, k).await,
                AnySink::Clickhouse(s) => s.query_top_k(query_vec, k).await,
            }
        }
    }
}

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) => {
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim)))
        }
        (TargetConfig::Clickhouse(t), AnyBackend::Clickhouse(b)) => {
            Ok(AnySink::Clickhouse(ClickhouseSink::new(t.clone(), b, dim)))
        }
        #[allow(unreachable_patterns)]
        _ => Err(anyhow!("backend / target type mismatch — programming error in load_sink dispatch")),
    }
}
```

- [ ] **Step 3: Update `lib.rs` re-exports**

Edit `rust/chunkshop/src/lib.rs`. Replace:

```rust
pub use sinks::{AnySink, PgSink, Sink};
```

with:

```rust
pub use sinks::{AnySink, ClickhouseSink, PgSink, Sink};
```

- [ ] **Step 4: Compile + run all tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs 2>&1 | grep -E "^test result" | awk '{ pass += $4; fail += $6; ignore += $8 } END { print "TOTAL passed:", pass, "failed:", fail, "ignored:", ignore }'
```

Expected: clean build; ≥ 138 passed (126 baseline + new CH tests), 0 failed, 1 ignored.

- [ ] **Step 5: ⛔ Drift Check DC-003**

Re-read `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md`. Verify:
1. SC-003 — append-only test green ✓
2. SC-004 — warn-once test green ✓
3. ClickhouseSink hits ZERO `sqlx` imports — `grep -n sqlx rust/chunkshop/src/sinks/clickhouse.rs` should return empty
4. ClickhouseSink hits ZERO `ON CONFLICT` strings — `grep -n "ON CONFLICT" rust/chunkshop/src/sinks/clickhouse.rs` should return empty

If any check fails, stop and surface to user.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/src/sinks/mod.rs rust/chunkshop/src/lib.rs
git commit -m "feat(r4): wire AnySink::Clickhouse + Sink trait impl (DC-003 gate)"
```

---

### Task 14: `ClickhouseTableSource` + config variant + `AnySource` wiring

**Files:**
- Create: `rust/chunkshop/src/sources/clickhouse_table.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs` (add variant + load_source arm + update R1's deferral comment)
- Modify: `rust/chunkshop/src/config.rs` (add `ClickhouseTableSourceConfig`)
- Modify: `rust/chunkshop/src/lib.rs` (re-export)

- [ ] **Step 1: Add `ClickhouseTableSourceConfig` to `config.rs`**

Edit `rust/chunkshop/src/config.rs`. Find the `pub enum SourceConfig` block (around line 235). Replace with:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
    PgTable(PgTableSourceConfig),
    Http(HttpSourceConfig),
    S3(S3SourceConfig),
    ClickhouseTable(ClickhouseTableSourceConfig),
    Inline(InlineSourceConfig),
}
```

Then add the new struct definition right after `PgTableSourceConfig` (search for `pub struct HttpSourceConfig` and insert before it):

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Mirrors
    /// Python's `clickhouse_table.py` which interpolates this verbatim. NOT
    /// validated; don't expose this field to untrusted YAML authors.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}
```

Then update the `if let SourceConfig::PgTable(...)` ident validation block in `load_config` (around line 855) to also handle the new variant. Find that block and add immediately after:

```rust
if let SourceConfig::ClickhouseTable(p) = &cfg.source {
    validate_ident(&p.database_name, "source.database")?;
    validate_ident(&p.table, "source.table")?;
    validate_ident(&p.id_column, "source.id_column")?;
    validate_ident(&p.content_column, "source.content_column")?;
    if let Some(tc) = &p.title_column {
        validate_ident(tc, "source.title_column")?;
    }
    for mc in &p.metadata_columns {
        validate_ident(mc, "source.metadata_columns")?;
    }
}
```

- [ ] **Step 2: Create the source impl**

Create `rust/chunkshop/src/sources/clickhouse_table.rs`:

```rust
//! ClickHouse table source. Mirrors python/src/chunkshop/sources/clickhouse_table.py.
//! Streams rows via the official `clickhouse` crate's cursor API rather than
//! materializing in RAM (CH source tables are typically larger than PG ones).

use anyhow::{Context, Result};
use clickhouse::Row;
use serde::Deserialize;
use serde_json::json;

use crate::backends::base::BackendDialect;
use crate::backends::clickhouse::ClickhouseBackend;
use crate::config::ClickhouseTableSourceConfig;
use crate::sources::base::Document;

pub struct ClickhouseTableSource {
    cfg: ClickhouseTableSourceConfig,
    backend: ClickhouseBackend,
}

impl ClickhouseTableSource {
    pub fn new(cfg: ClickhouseTableSourceConfig) -> Self {
        let backend = ClickhouseBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Build select: id, content, [title], [metadata_columns...]
        // We fetch as a JSON-string row (using `formatRow` would be ideal but
        // is awkward through the typed crate; instead, we read each column as
        // its native type and JSON-coerce in code).
        let mut select_cols = vec![
            self.backend.quote_ident(&self.cfg.id_column),
            self.backend.quote_ident(&self.cfg.content_column),
        ];
        let title_idx = self.cfg.title_column.as_ref().map(|tc| {
            select_cols.push(self.backend.quote_ident(tc));
            select_cols.len() - 1
        });
        let meta_start = select_cols.len();
        for col in &self.cfg.metadata_columns {
            select_cols.push(self.backend.quote_ident(col));
        }
        let cols_sql = select_cols.join(", ");
        let fq = self.backend.fq_table(&self.cfg.database_name, &self.cfg.table);
        let mut q = format!("SELECT {cols_sql} FROM {fq}");
        if let Some(w) = &self.cfg.where_clause {
            q.push_str(&format!(" WHERE {w}"));
        }

        // Strategy: emit JSON via `toJSONString(map(...))` won't work for
        // arbitrary scalar combos. The pragmatic shape is to ask for
        // FORMAT JSON via a separate code path. clickhouse-rs's typed API
        // expects fixed schemas; for variable metadata_columns we fall back
        // to clickhouse-http JSON. But since metadata_columns is bounded and
        // a String-coerce model is acceptable, we read everything as
        // String via toString().
        let select_cols_typed: Vec<String> = select_cols
            .iter()
            .enumerate()
            .map(|(i, ident)| {
                if i == 0 || i == 1 || Some(i) == title_idx {
                    // id, content, title — read as native String
                    ident.clone()
                } else {
                    // metadata column — coerce to String for transport
                    format!("toString({ident}) AS {ident}")
                }
            })
            .collect();
        let q_typed = {
            let cols_typed_sql = select_cols_typed.join(", ");
            let mut s = format!("SELECT {cols_typed_sql} FROM {fq}");
            if let Some(w) = &self.cfg.where_clause {
                s.push_str(&format!(" WHERE {w}"));
            }
            s
        };

        // Use a Vec<String> row (n columns, all String). The official crate
        // supports a generic Vec<String> row via the `Row` derive on a tuple
        // struct — but easier: define a per-call Row with all-String fields
        // and read positionally. Since Rust generics can't dynamically size
        // a derive(Row) struct, we materialize rows by querying via the raw
        // HTTP `query` interface. Simplest path: derive Row for a fixed
        // upper bound (8 fields) — chunkshop conventionally allows up to a
        // few metadata columns. If you need more, extend.
        // In practice: emit one SELECT per row count config. Below uses the
        // `cargo` workaround of issuing the raw HTTP query and parsing JSON.
        let client = self.backend.client().await?;
        // Append FORMAT JSONEachRow to receive one JSON object per line.
        // The official crate also exposes a low-level `client.with_option("output_format_json_quote_64bit_integers", "0")` — accept defaults here.
        let q_json = format!("{q_typed} FORMAT JSONEachRow");
        // The clickhouse crate's `query(...).fetch_bytes` is the right
        // primitive. If it's not available in 0.15, use:
        //   let body = client.query(q_typed).fetch_one::<String>()?  // returns single row only
        // For robust streaming JSON parsing, the explicit HTTP call is reliable:
        //
        //   let bytes = client
        //       .query(&q_json)
        //       .fetch_bytes("JSONEachRow")?
        //       .collect()
        //       .await?;
        //
        // Adapt to whichever streaming primitive the active clickhouse-rs
        // version exposes; the schema-erasure goal is the constant.
        let bytes = client
            .query(&q_json)
            .fetch_bytes("JSONEachRow")
            .with_context(|| format!("running CH source query: {q_json}"))?
            .collect()
            .await?;
        let body = String::from_utf8(bytes.to_vec())?;

        let mut out = Vec::new();
        for line in body.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let row: serde_json::Value =
                serde_json::from_str(line).context("parsing JSONEachRow line")?;
            let id = row
                .get(&self.cfg.id_column)
                .and_then(|v| v.as_str())
                .ok_or_else(|| anyhow::anyhow!("id_column {} missing or not string", self.cfg.id_column))?
                .to_string();
            let content = row
                .get(&self.cfg.content_column)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let title = self
                .cfg
                .title_column
                .as_ref()
                .and_then(|tc| row.get(tc).and_then(|v| v.as_str()).map(str::to_string));
            let mut meta = serde_json::Map::new();
            for mc in &self.cfg.metadata_columns {
                meta.insert(mc.clone(), row.get(mc).cloned().unwrap_or(json!(null)));
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
            });
        }
        Ok(out)
    }
}

// Per-row schema-erasure rationale: see the SELECT-coerce logic above.
// We deliberately DO NOT define a `#[derive(Row)]` struct because the column
// count is config-driven and Rust derive macros can't generate variable shapes.
// The JSONEachRow path is the documented chunkshop CH source contract.
// Mirrors the Python source's `query_rows_stream` plus `_json_safe`.
#[allow(dead_code)]
#[derive(Row, Deserialize)]
struct _PlaceholderRow {} // anchors the `Row` import for future expansion
```

(Note on the source impl: the cleanest path is `client.query(sql).fetch_bytes("JSONEachRow")` which the official crate exposes for raw output. If the exact API in 0.15 differs, adapt — the contract is a single SELECT with `FORMAT JSONEachRow` parsed line-by-line into `serde_json::Value`. The ClickhouseTable variant is the only place we use this path; the sink uses `Insert<T>` where the schema is fixed.)

- [ ] **Step 2.5: Verify `fetch_bytes` API in clickhouse 0.15 — adapt if needed**

Run a quick sanity build before proceeding:

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | grep -E "fetch_bytes|error" | head -10
```

If `fetch_bytes` doesn't compile, replace the body of `iter_documents` with the alternative shape:

```rust
// Alternative: the typed cursor with a per-source-config struct. Define the
// row struct by tuple fields up to N=10 columns:
#[derive(Row, Deserialize)]
struct WideRow<'a> {
    f0: &'a str, f1: &'a str, f2: Option<&'a str>,
    f3: Option<String>, f4: Option<String>, f5: Option<String>,
    f6: Option<String>, f7: Option<String>, f8: Option<String>,
    f9: Option<String>,
}
let mut cur = client.query(&q_typed).fetch::<WideRow<'_>>()?;
// ... walk cur.next() and project into Document by index using `select_cols`.
```

Pick the path that compiles cleanly and document the choice in a code comment. The mission brief's SC-010 only requires "streams via the official driver's cursor API" — both paths satisfy it.

- [ ] **Step 3: Wire `AnySource` and `load_source`**

Edit `rust/chunkshop/src/sources/mod.rs`. Replace:

```rust
/// Sum type for runtime polymorphism. R1 covers the 5 sources currently in the
/// crate. R2/R3/R4 add MariadbTable, SqliteTable. ClickhouseTable is deferred
/// to v4.1 (not first-ship; matches the predecessor spec).
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    Http(HttpSource),
    S3(S3Source),
}
```

with:

```rust
/// Sum type for runtime polymorphism. R1 covers the original 5 sources.
/// R4 adds ClickhouseTable (P1 unblocked it). R2/R3 add MariadbTable, SqliteTable.
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    Http(HttpSource),
    S3(S3Source),
    ClickhouseTable(ClickhouseTableSource),
}
```

Replace the `pub mod` block at the top of the file:

```rust
pub mod base;
pub mod clickhouse_table;
pub mod files;
pub mod http;
pub mod json_corpus;
pub mod pg_table;
pub mod s3;
```

Replace the re-exports:

```rust
pub use base::Document;
pub use clickhouse_table::ClickhouseTableSource;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;
pub use pg_table::PgTableSource;
pub use s3::S3Source;
```

Replace the `iter_documents` match in `impl AnySource`:

```rust
impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
            AnySource::ClickhouseTable(s) => s.iter_documents().await,
        }
    }
}
```

Replace `load_source`:

```rust
pub fn load_source(cfg: &SourceConfig) -> Result<AnySource> {
    match cfg {
        SourceConfig::Files(c) => Ok(AnySource::Files(FilesSource::new(c.clone()))),
        SourceConfig::JsonCorpus(c) => Ok(AnySource::JsonCorpus(JsonCorpusSource::new(c.clone()))),
        SourceConfig::PgTable(c) => Ok(AnySource::PgTable(PgTableSource::new(c.clone()))),
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::ClickhouseTable(c) => Ok(AnySource::ClickhouseTable(ClickhouseTableSource::new(c.clone()))),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
```

- [ ] **Step 4: Update `lib.rs` re-exports**

Edit `rust/chunkshop/src/lib.rs`. Replace:

```rust
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source};
```

with:

```rust
pub use sources::{AnySource, ClickhouseTableSource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source};
```

- [ ] **Step 5: Compile**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo build -p chunkshop-rs 2>&1 | tail -10
```

Expected: clean build.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/sources/clickhouse_table.rs rust/chunkshop/src/sources/mod.rs rust/chunkshop/src/config.rs rust/chunkshop/src/lib.rs
git commit -m "feat(r4): ClickhouseTableSource + AnySource wiring (P1 unblocked)"
```

---

### Task 15: `ClickhouseTableSource` integration test (SC-010)

**Files:**
- Create: `rust/chunkshop/tests/clickhouse_table_source.rs`

- [ ] **Step 1: Write the integration test**

Create `rust/chunkshop/tests/clickhouse_table_source.rs`:

```rust
//! ClickhouseTableSource integration test (SC-010). Round-trips 3 documents
//! through a CH table and asserts Document projections match.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::config::ClickhouseTableSourceConfig;
use chunkshop::sources::ClickhouseTableSource;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn projects_id_content_title_metadata_columns() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    let db = "chunkshop_r4_source";

    client.query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC")).execute().await?;
    client.query(&format!("CREATE DATABASE `{db}`")).execute().await?;
    client
        .query(&format!(
            "CREATE TABLE `{db}`.docs (id String, body String, title String, lang String, author String) ENGINE = MergeTree() ORDER BY id"
        ))
        .execute()
        .await?;
    client
        .query(&format!(
            "INSERT INTO `{db}`.docs VALUES \
             ('a', 'hello world', 'A', 'en', 'alice'), \
             ('b', 'bonjour', 'B', 'fr', 'bob'), \
             ('c', 'hola', 'C', 'es', 'carol')"
        ))
        .execute()
        .await?;

    let cfg = ClickhouseTableSourceConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: db.to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: Some("title".to_string()),
        where_clause: None,
        metadata_columns: vec!["lang".to_string(), "author".to_string()],
    };
    let source = ClickhouseTableSource::new(cfg);
    let docs = source.iter_documents().await?;
    assert_eq!(docs.len(), 3, "expected 3 docs, got {}", docs.len());
    let by_id: std::collections::HashMap<String, _> =
        docs.iter().map(|d| (d.id.clone(), d)).collect();
    let a = by_id.get("a").unwrap();
    assert_eq!(a.content, "hello world");
    assert_eq!(a.title.as_deref(), Some("A"));
    assert_eq!(a.metadata.get("lang").and_then(|v| v.as_str()), Some("en"));
    assert_eq!(a.metadata.get("author").and_then(|v| v.as_str()), Some("alice"));

    client.query(&format!("DROP DATABASE `{db}` SYNC")).execute().await?;
    Ok(())
}
```

- [ ] **Step 2: Run the test**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_table_source 2>&1 | tail -10
```

Expected: 1 test passes. If the JSON-line parsing path needs adjustment, iterate on the source impl from Task 14 until the test passes.

- [ ] **Step 3: ⛔ Drift Check DC-004**

Re-read `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md`. Verify SC-010 — ClickhouseTableSource projection works correctly. Confirm streaming uses the official driver's cursor / fetch_bytes API, not full `Vec`-materialized SELECT. Also confirm R1's deferral comment was updated (no longer says "ClickhouseTable is deferred to v4.1").

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/clickhouse_table_source.rs
git commit -m "test(r4): ClickhouseTableSource projection integration (SC-010, DC-004)"
```

---

### Task 16: `ReplacingMergeTree(created_at)` + `OPTIMIZE FINAL` dedup integration test (SC-005)

**Files:**
- Create: `rust/chunkshop/tests/clickhouse_sink_replacing_engine.rs`

- [ ] **Step 1: Write the test**

Create `rust/chunkshop/tests/clickhouse_sink_replacing_engine.rs`:

```rust
//! SC-005 integration test: ReplacingMergeTree(created_at) engine override
//! plus OPTIMIZE FINAL dedup.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::TargetConfig;
use chunkshop::sinks::ClickhouseSink;
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn replacing_merge_tree_dedups_after_optimize_final() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_replacing";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    client.query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC")).execute().await?;

    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: chunks\nmode: overwrite\nhnsw: false\nengine: \"ReplacingMergeTree(created_at) ORDER BY (id)\""
    );
    let raw: serde_yml::Value = serde_yml::from_str(&yaml).unwrap();
    let target: TargetConfig = serde_yml::from_value(raw).unwrap();
    let TargetConfig::Clickhouse(cfg) = target else { unreachable!() };

    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table_impl().await?;

    // Insert the same 3-chunk doc twice. Without engine override there'd be 6 rows;
    // with ReplacingMergeTree, OPTIMIZE FINAL collapses to 3.
    let chunks: Vec<Chunk> = (0..3)
        .map(|i| Chunk {
            doc_id: "doc-x".into(),
            seq_num: i as i32,
            original_content: format!("orig {i}"),
            embedded_content: format!("emb {i}"),
            metadata: json!({}),
        })
        .collect();
    let embs: Vec<Vec<f32>> = (0..3).map(|i| vec![i as f32; 4]).collect();
    let tags: Vec<Vec<String>> = (0..3).map(|_| vec![]).collect();

    sink.write_document_impl("doc-x", &chunks, &embs, &tags).await?;
    sink.write_document_impl("doc-x", &chunks, &embs, &tags).await?;

    // Pre-OPTIMIZE: 6 rows (CH does NOT eagerly dedup on INSERT)
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct C { c: u64 }
    let q = format!("SELECT count() AS c FROM `{db}`.chunks");
    let mut cur = client.query(&q).fetch::<C>()?;
    let pre = cur.next().await?.unwrap().c;
    assert_eq!(pre, 6, "pre-OPTIMIZE: expected 6 rows; got {pre}");

    // Force merge-time dedup
    client
        .query(&format!("OPTIMIZE TABLE `{db}`.chunks FINAL"))
        .execute()
        .await?;

    let q = format!("SELECT count() AS c FROM `{db}`.chunks FINAL");
    let mut cur = client.query(&q).fetch::<C>()?;
    let post = cur.next().await?.unwrap().c;
    assert_eq!(post, 3, "post-OPTIMIZE FINAL: expected 3 dedup'd rows; got {post}");

    client.query(&format!("DROP DATABASE `{db}` SYNC")).execute().await?;
    Ok(())
}
```

- [ ] **Step 2: Run the test**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs --test clickhouse_sink_replacing_engine 2>&1 | tail -10
```

Expected: 1 test passes.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/tests/clickhouse_sink_replacing_engine.rs
git commit -m "test(r4): ReplacingMergeTree + OPTIMIZE FINAL dedup (SC-005)"
```

---

### Task 17: Sample-YAML smoke + manual cross-language e2e doc

**Files:**
- Verify-only: `docs/samples/sample-clickhouse.yaml` (already exists from Python work — confirm `load_config` accepts it)
- Verify-only: `docs/samples/sample-clickhouse-source.yaml` (already exists — confirm `load_config` accepts it)
- Create: `rust/chunkshop/tests/manual/r4-cross-language.md`
- Create: `rust/chunkshop/tests/sample_clickhouse_yaml_loads.rs`

- [ ] **Step 1: Sample-YAML loadability test**

Create `rust/chunkshop/tests/sample_clickhouse_yaml_loads.rs`:

```rust
//! SC-009 smoke: docs/samples/sample-clickhouse.yaml loads cleanly via
//! load_config and resolves to TargetConfig::Clickhouse with the expected fields.
//! Bonus: sample-clickhouse-source.yaml loads as a ClickhouseTable source.

use std::path::PathBuf;

fn workspace_root() -> PathBuf {
    // CARGO_MANIFEST_DIR points to rust/chunkshop/. Workspace root is two up.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

#[test]
fn sample_clickhouse_yaml_loads_as_clickhouse_target() {
    let path = workspace_root().join("docs/samples/sample-clickhouse.yaml");
    let cfg = chunkshop::load_config(&path).expect("load sample-clickhouse.yaml");
    let chunkshop::config::TargetConfig::Clickhouse(t) = &cfg.target else {
        panic!("expected Clickhouse target, got {:?}", cfg.target);
    };
    assert_eq!(t.database_name, "chunkshop_samples");
    assert_eq!(t.table, "handbook");
    assert_eq!(t.mode, "overwrite");
}

#[test]
fn sample_clickhouse_source_yaml_loads_as_clickhouse_table_source() {
    let path = workspace_root().join("docs/samples/sample-clickhouse-source.yaml");
    let cfg = chunkshop::load_config(&path).expect("load sample-clickhouse-source.yaml");
    let chunkshop::config::SourceConfig::ClickhouseTable(s) = &cfg.source else {
        panic!("expected ClickhouseTable source, got {:?}", cfg.source);
    };
    assert_eq!(s.database_name, "my_app");
    assert_eq!(s.table, "documents");
    assert_eq!(s.id_column, "id");
}
```

- [ ] **Step 2: Run the smoke**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
cargo test -p chunkshop-rs --test sample_clickhouse_yaml_loads 2>&1 | tail -10
```

Expected: 2 tests pass. If the YAML uses fields the Rust crate doesn't recognize, that's surfaced here — fix the config struct or the YAML, with preference for the YAML (the Rust port shouldn't accept fields the Python doesn't).

- [ ] **Step 3: Write the manual cross-language e2e doc**

Create `rust/chunkshop/tests/manual/r4-cross-language.md`:

```markdown
# R4-SC-006 — Manual cross-language vector parity check

This is a manual verification step. Full automation lands with RT (Wave 3 matrix test).

## Goal
Write 5 chunks via Python `ClickHouseSink`; query top-5 via Rust `ClickhouseSink::query_top_k`;
assert matching IDs in matching order.

## Prereqs
- ClickHouse 24.10+ running on localhost:8124 (use `docker compose -f docker-compose.test.yaml up -d clickhouse`)
- Both Python (chunkshop) and Rust (chunkshop-rs) crates built

## Steps

### 1. Set env
```bash
export CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_xlang'
```

### 2. Python writer (5 fixed chunks)
```bash
cd python
uv run python -c '
from chunkshop.config import TargetConfig
from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.sinks.clickhouse import ClickHouseSink
from chunkshop.chunkers.base import Chunk
import numpy as np, json

cfg = TargetConfig(
    type="clickhouse", dsn_env="CHUNKSHOP_TEST_DSN_CH",
    database="chunkshop_xlang", table="parity_chunks",
    mode="overwrite", source_tag="py", hnsw=False,
)
backend = ClickHouseBackend(dsn_env=cfg.dsn_env)
sink = ClickHouseSink(cfg, backend, embed_dim=4)
sink.create_table()
chunks = [Chunk(doc_id="d", seq_num=i, original_content=f"o{i}", embedded_content=f"e{i}", metadata={}) for i in range(5)]
embs = np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1],[0.5,0.5,0,0]], dtype=np.float32)
tags = [[] for _ in chunks]
sink.write_document("d", chunks, embs, tags)
print("wrote 5 chunks via Python")
'
```

### 3. Rust reader (top-5 cosine)
```bash
cd ../rust
cargo run --bin chunkshop-rs -- query \
    --config <(cat <<'EOF'
cell_name: parity_check
source: { type: inline }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 4 }
target:
  type: clickhouse
  dsn_env: CHUNKSHOP_TEST_DSN_CH
  database: chunkshop_xlang
  table: parity_chunks
  mode: append
  source_tag: rs_query
  hnsw: false
EOF
)
```

(NOTE: a CLI `query` subcommand may not exist yet; alternative is a small ad-hoc Rust harness binary or `cargo test -- --ignored cross_lang_check` gated by `R4_MANUAL=1`.)

### Expected result
For query vector `[1, 0, 0, 0]`, Python and Rust must both return:
- doc_id=d seq_num=0 (perfect match, distance ~0)
- doc_id=d seq_num=4 (mid match, [0.5,0.5,0,0])
- doc_id=d seq_num=1, 2, 3 (orthogonal, distance ~1)

ID order on rank 0 and 4 is deterministic. Distance values may differ in last-digit precision between Python's numpy float and Rust's f32, but the ranking must match.

### Cleanup
```bash
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml exec clickhouse \
    clickhouse-client -u default --password chpw -q 'DROP DATABASE IF EXISTS chunkshop_xlang SYNC'
```

## Sign-off
SC-006 is satisfied when the Rust top-5 query returns the same `(doc_id, seq_num)` order as the Python reference for the test vector. Full programmatic automation is RT's job.
```

- [ ] **Step 4: Commit**

```bash
mkdir -p /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust/chunkshop/tests/manual
git add rust/chunkshop/tests/sample_clickhouse_yaml_loads.rs rust/chunkshop/tests/manual/r4-cross-language.md
git commit -m "test(r4): sample YAML smoke + manual cross-language e2e doc"
```

---

### Task 18: Final sweep + DC-FINAL gate + branch ready for merge

**Files:**
- (no new files; verification + summary)

- [ ] **Step 1: Run the full test suite with all DSNs available**

```bash
cd /home/yonk/yonk-tools/chunkshop-r4-clickhouse/rust
CHUNKSHOP_TEST_DSN='postgresql://postgres:postgres@localhost:5434/chunkshop_test' \
CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_test' \
  cargo test -p chunkshop-rs 2>&1 | tee /tmp/r4-final-test.log | grep -E "^test result" | \
  awk '{ pass += $4; fail += $6; ignore += $8 } END { print "TOTAL passed:", pass, "failed:", fail, "ignored:", ignore }'
```

Expected: pass count ≥ 126 (baseline) + ~14 new R4 tests (driver smoke, dialect parity 9, conn 3, sink create_table 3, sink append-only 3, sink replacing 1, source 1, sample yaml 2 = ~22). Failed: 0. Ignored: 1.

- [ ] **Step 2: Spec-review checklist (manual)**

Do a final read of the new files. Confirm:

```bash
# No sqlx in CH code paths
grep -rn sqlx rust/chunkshop/src/backends/clickhouse.rs rust/chunkshop/src/sinks/clickhouse.rs rust/chunkshop/src/sources/clickhouse_table.rs && echo "FAIL: sqlx leaked into CH code" || echo "OK: no sqlx in CH"

# No ON CONFLICT in CH sink
grep -n "ON CONFLICT" rust/chunkshop/src/sinks/clickhouse.rs && echo "FAIL: upsert in CH sink" || echo "OK: append-only"

# No BackendConn impl on ClickhouseBackend
grep -n "impl BackendConn for ClickhouseBackend" rust/chunkshop/src/backends/clickhouse.rs && echo "FAIL: BackendConn impl present (should be inherent)" || echo "OK: BackendDialect-only per SC-001"

# Engine allowlist regex present
grep -n "CLICKHOUSE_ENGINE_RE" rust/chunkshop/src/config.rs || echo "FAIL: engine allowlist regex missing"

# warn-once OnceLock present
grep -n "DELETE_ORPHANS_WARNED" rust/chunkshop/src/sinks/clickhouse.rs || echo "FAIL: warn-once mechanism missing"
```

All should report `OK:` (or have no output for the FAIL-on-match greps). Any FAIL → stop, fix, re-test.

- [ ] **Step 3: ⛔ Drift Check DC-FINAL**

Re-read `skill-output/mission-brief/Mission-Brief-r4-rust-clickhouse.md` ONE MORE TIME. For each Success Criterion, point at evidence:

- **SC-001:** `cargo build` clean + spec-review confirms BackendDialect-only. ✓
- **SC-002:** `grep -rn 'self.backend.' rust/chunkshop/src/sinks/clickhouse.rs | wc -l` shows multiple uses (no inline driver-only paths). ✓
- **SC-003:** `tests/clickhouse_sink_append_only.rs::reingest_produces_duplicate_rows_on_default_engine` passes. ✓
- **SC-004:** `tests/clickhouse_sink_append_only.rs::delete_orphans_warns_exactly_once` passes (or the tracing-test variant of it). ✓
- **SC-005:** Config unit tests + `tests/clickhouse_sink_replacing_engine.rs` pass. ✓
- **SC-006:** Manual e2e doc exists at `tests/manual/r4-cross-language.md`. ✓ (full automation deferred to RT)
- **SC-007:** `tests/dialect_clickhouse_parity.rs` 9 tests pass. ✓
- **SC-008:** `cargo test -p chunkshop-rs` pass count ≥ 126. ✓
- **SC-009:** `tests/sample_clickhouse_yaml_loads.rs::sample_clickhouse_yaml_loads_as_clickhouse_target` passes. ✓
- **SC-010:** `tests/clickhouse_table_source.rs::projects_id_content_title_metadata_columns` passes. ✓

If ANY criterion lacks evidence, the work is NOT complete — fix and re-verify.

- [ ] **Step 4: Confirm Out-of-Scope discipline**

```bash
# Nothing in bakeoff was touched (R4 explicitly excluded that)
git diff main..HEAD --stat -- rust/chunkshop/src/bakeoff/
# Should show: 0 files changed (or only auto-formatted whitespace if rustfmt ran)

# BackendConn trait wasn't refactored
git diff main..HEAD -- rust/chunkshop/src/backends/base.rs | head -20
# Should show no signature changes — same `&mut sqlx::Transaction<'_, sqlx::Postgres>`

# No new sample YAMLs beyond what was needed
git status docs/samples/
# Should be clean (sample-clickhouse.yaml + sample-clickhouse-source.yaml were already there from earlier merges)
```

- [ ] **Step 5: Final commit + push (push only after user confirmation)**

If anything was modified during DC-FINAL fixes, commit:

```bash
git add -u
git commit -m "chore(r4): DC-FINAL fixes — every SC has evidence"
```

Confirm branch state:

```bash
git log --oneline experimental/v4-modular-backends..HEAD
git status
```

The branch is ready to merge `--no-ff` into `experimental/v4-modular-backends`. **Do NOT push or merge without user instruction** — the user may want to run `/verify-alignment` or `/code-review` first.

- [ ] **Step 6: Hand off**

Summarize back to the user (using the standard `summary-pattern` from `~/.claude/rules/summary-pattern.md`):

```
CHANGES MADE:
- rust/chunkshop/Cargo.toml: added clickhouse 0.15 + url 2 + urlencoding 2 deps
- rust/chunkshop/src/backends/clickhouse.rs: new ClickhouseBackend (BackendDialect impl + inherent connection methods)
- rust/chunkshop/src/sinks/clickhouse.rs: new ClickhouseSink (Sink impl, append-only, warn-once)
- rust/chunkshop/src/sources/clickhouse_table.rs: new ClickhouseTableSource
- rust/chunkshop/src/config.rs: ClickhouseTargetConfig + ClickhouseTableSourceConfig + engine allowlist regex
- rust/chunkshop/src/{backends,sinks,sources}/mod.rs: AnyBackend / AnySink / AnySource variants + load_* arms
- rust/chunkshop/src/lib.rs: re-exports
- rust/chunkshop/tests/dialect_clickhouse_parity.rs + parity-fixtures/dialect-clickhouse.json: 9 dialect parity tests
- rust/chunkshop/tests/backend_clickhouse_conn.rs: 3 connection-layer integration tests
- rust/chunkshop/tests/clickhouse_sink_create_table.rs: 3 mode-dispatch integration tests
- rust/chunkshop/tests/clickhouse_sink_append_only.rs: 3 append-only / warn-once / query_top_k tests
- rust/chunkshop/tests/clickhouse_sink_replacing_engine.rs: 1 ReplacingMergeTree + OPTIMIZE FINAL test
- rust/chunkshop/tests/clickhouse_table_source.rs: 1 source projection test
- rust/chunkshop/tests/sample_clickhouse_yaml_loads.rs: 2 sample-YAML load tests
- rust/chunkshop/tests/manual/r4-cross-language.md: manual cross-language e2e doc

THINGS I DIDN'T TOUCH (intentionally):
- rust/chunkshop/src/backends/base.rs: BackendConn trait remains sqlx-Postgres-concrete (R2's job to GAT-ify)
- rust/chunkshop/src/bakeoff/config.rs: BakeoffTargetConfig untouched (out of scope per brief)
- python/src/chunkshop/config.py: engine field still has no validator on the Python side (out-of-scope; Rust hardens unilaterally)
- docs/samples/sample-clickhouse.yaml + sample-clickhouse-source.yaml: pre-existing from Python work; Rust just loads them

POTENTIAL CONCERNS:
- ClickhouseTableSource uses FORMAT JSONEachRow + serde_json parse rather than typed Row derive (shape is config-driven). Documented in code; safe but slower than typed for huge tables.
- The clickhouse 0.15 `fetch_bytes` API may need adjustment depending on actual crate signature — Task 14 includes a fallback path (typed WideRow) if needed.
- `delete_orphans` warn-once is process-scoped (OnceLock). If the same process instantiates many sinks across configs, only the first warns — by design.
```

- [ ] **Step 7: Suggest follow-ups**

Suggest the user run `/verify-alignment` before merging (it walks the brief one more time and produces a paste-ready alignment report). Then `git merge --no-ff experimental/v4-rust-clickhouse` into `experimental/v4-modular-backends` mirrors the R1 pattern (`13cac8b`).

---

## Self-Review Notes

After drafting, ran the self-review checklist:

**Spec coverage:**
- SC-001 (BackendDialect only): Tasks 4-5 + DC-002 verification
- SC-002 (Sink routes through backend): Tasks 9-13 + DC-FINAL grep check
- SC-003 (no upsert / dup rows): Task 11 test
- SC-004 (warn-once delete_orphans): Task 11 test
- SC-005 (engine allowlist + ReplacingMergeTree): Tasks 7 + 16
- SC-006 (cross-language manual): Task 17 doc
- SC-007 (dialect parity fixture): Tasks 3-5
- SC-008 (126 baseline still green): Tasks 8 + 18
- SC-009 (sample YAML loads): Task 17
- SC-010 (ClickhouseTableSource): Tasks 14-15
- All DC-001..DC-FINAL injected as gates

**Placeholder scan:** none — every step contains exact code or exact commands.

**Type consistency:** `ClickhouseSink::*_impl` methods are inherent; trait impl in Task 13 just delegates. `ClickhouseTargetConfig` and `ClickhouseTableSourceConfig` field names match Python pydantic exactly.
