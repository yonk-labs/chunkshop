# R2 — Rust MariaDB Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a MariaDB 11.7+ backend to the Rust crate that mirrors the Python reference impl, plugging into R1's modular trait surface, while finalizing R1's deliberate seam by lifting `BackendConn` to a GAT (`type Db: sqlx::Database`).

**Architecture:** Three new files under `rust/chunkshop/src/{backends,sinks,sources}/mariadb*.rs` plus three new integration test files. One refactor of `backends/base.rs` (the GAT lift) that ripples through `postgres.rs` to set `type Db = sqlx::Postgres`. Wiring goes inline: new variants on the `AnyBackend` / `AnySink` / `AnySource` enums, new arms in `load_*` factories, new pydantic-mirrored variants on `TargetConfig` / `SourceConfig` in `config.rs`, plus lib.rs re-exports. Vector literals splice inline as `VEC_FromText('[...]')` because sqlx-mysql has no VECTOR adapter; everything else binds normally via `?` placeholders.

**Tech Stack:** Rust 1.75+, sqlx 0.8 with mysql feature, MariaDB 11.7+ native VECTOR type, pydantic-mirrored serde config, anyhow, tokio.

**Mission Brief:** `/home/yonk/yonk-tools/chunkshop-r2-mariadb/skill-output/mission-brief/Mission-Brief-r2-rust-mariadb.md`

**Reference files (read these before any task that touches their topic):**
- Python sisters: `python/src/chunkshop/{backends/mariadb.py, sinks/mariadb.py, sources/mariadb_table.py}`
- R1 reference: `rust/chunkshop/src/{backends/postgres.rs, sinks/pg.rs, sources/pg_table.rs, backends/base.rs}`
- Trait contract: `rust/chunkshop/src/backends/base.rs`
- Test patterns: `rust/chunkshop/tests/{dialect_postgres_parity.rs, backend_postgres_conn.rs, pg_table_source.rs, pg_sink_create_table.rs}`

---

## Phase A — GAT Lift on `BackendConn` (DC-001)

R1's `BackendConn` methods take `&mut sqlx::Transaction<'_, sqlx::Postgres>` concretely. The brief mandates a GAT lift so `Self::Db` resolves per-backend. PgSink's call sites do NOT need to change because `PgSink` holds a concrete `PostgresBackend`, so `<PostgresBackend as BackendConn>::Db = sqlx::Postgres` and the `&mut Transaction<'_, Postgres>` parameter still type-checks at the call site. The lift is genuinely a trait-surface-only refactor.

### Task 1: Add `sqlx mysql` feature + lift `BackendConn` to GAT

**Files:**
- Modify: `rust/chunkshop/Cargo.toml:46`
- Modify: `rust/chunkshop/src/backends/base.rs:69-101`
- Modify: `rust/chunkshop/src/backends/postgres.rs:184-264`
- Verify (no edits expected): `rust/chunkshop/src/sinks/pg.rs`, `rust/chunkshop/src/sources/pg_table.rs`

- [ ] **Step 1: Confirm baseline test count before any change**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected: `TOTAL passed=126 failed=0 ignored=1`

- [ ] **Step 2: Add `mysql` feature to sqlx in Cargo.toml**

Edit `rust/chunkshop/Cargo.toml` line 46. Replace:
```toml
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "json", "chrono"] }
```
With:
```toml
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "mysql", "json", "chrono"] }
```

- [ ] **Step 3: Verify the crate still builds with the new feature**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
```
Expected: `Finished` line, no errors.

- [ ] **Step 4: Lift `BackendConn` to a GAT in `base.rs`**

Edit `rust/chunkshop/src/backends/base.rs`. Replace the entire `BackendConn` trait block (lines 69–96) with this GAT-form. The `Db` associated type carries the per-backend `sqlx::Database` choice; `acquire_create_lock`, `table_exists`, and `embedding_dim` reference `Self::Db` instead of the concrete `sqlx::Postgres`. Update the doc comment at the top of the file (lines 12–16) to reflect that R2 has discharged the seam.

```rust
/// I/O surface. R2 lifts this to a GAT (`type Db: sqlx::Database`) so each
/// backend names its own sqlx Database. PgSink/MariadbSink hold concrete
/// backends, so `<PostgresBackend as BackendConn>::Db = sqlx::Postgres` resolves
/// at the call site without sinks needing to be generic over `<B: Backend>`.
pub trait BackendConn {
    type Db: sqlx::Database;

    /// Force-initialize the connection pool. Idempotent — second call is a no-op.
    /// The DSN is sourced from the backend struct's configuration (set when the
    /// backend is constructed), not from arguments to this method.
    fn connect(&self) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn acquire_create_lock(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        key: &str,
    ) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn table_exists(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<bool>> + Send;

    fn embedding_dim(
        &self,
        tx: &mut sqlx::Transaction<'_, Self::Db>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<Option<usize>>> + Send;
}
```

Replace the doc comment block at lines 12–16 with:
```rust
//! R1 caveat (now discharged in R2): `BackendConn` originally took a PG-concrete
//! `&mut sqlx::Transaction<'_, sqlx::Postgres>`. R2 lifts this to a GAT
//! (`type Db: sqlx::Database`) so each backend names its own sqlx Database.
```

- [ ] **Step 5: Set `type Db = sqlx::Postgres` on `PostgresBackend::BackendConn` impl**

Edit `rust/chunkshop/src/backends/postgres.rs`. Find the `impl BackendConn for PostgresBackend` block (line 184). Add `type Db = sqlx::Postgres;` as the first item inside the impl, before `fn connect`. The method bodies stay byte-for-byte identical — they continue to take `&mut Transaction<'_, Postgres>` because `Self::Db = Postgres` resolves to that.

```rust
impl BackendConn for PostgresBackend {
    type Db = sqlx::Postgres;

    fn connect(&self) -> impl Future<Output = Result<()>> + Send {
        // ... existing body unchanged ...
    }
    // acquire_create_lock, table_exists, embedding_dim — all bodies unchanged
}
```

- [ ] **Step 6: Verify the GAT lift compiles + all PG tests still pass**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected build: `Finished`. Expected test totals: `TOTAL passed=126 failed=0 ignored=1` — same as baseline. **If any PG test broke, the GAT lift leaked. STOP and reassess before continuing.**

- [ ] **Step 7: DC-001 drift check**

Re-read the mission brief at `skill-output/mission-brief/Mission-Brief-r2-rust-mariadb.md`. Confirm: SC-009 evidence captured (PG tests pass, no concrete `sqlx::Postgres` references inside `backends/base.rs` outside the trait surface itself). Run the grep guard:
```bash
grep -n "sqlx::Postgres\|sqlx::postgres::" /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust/chunkshop/src/backends/base.rs
```
Expected: no matches (the trait references `Self::Db`, not `Postgres`).

- [ ] **Step 8: Commit Phase A**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/backends/base.rs rust/chunkshop/src/backends/postgres.rs
git commit -m "$(cat <<'EOF'
refactor(backends): lift BackendConn to GAT (type Db: sqlx::Database)

R1 deliberately deferred the cross-backend abstraction until a second
concrete backend was in hand. R2 discharges the seam: BackendConn now
exposes `type Db: sqlx::Database`, with PostgresBackend setting
`type Db = sqlx::Postgres`. Method bodies unchanged because PgSink
holds a concrete PostgresBackend, so `<PostgresBackend as BackendConn>::Db`
resolves to the same `sqlx::Postgres` everywhere.

Also adds the mysql feature to sqlx in preparation for MariadbBackend.

All 126 baseline tests pass.
EOF
)"
```

---

## Phase B — `MariadbBackend` Impl (DC-002)

TDD-first: write the dialect-parity fixture, write the failing parity test, then implement.

### Task 2: Author `dialect-mariadb.json` parity fixture

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/dialect-mariadb.json`

**Reference:** `tests/parity-fixtures/dialect-postgres.json` (38 lines), `python/src/chunkshop/backends/mariadb.py:31-78`.

- [ ] **Step 1: Create the fixture file**

The fixture is the source of truth for cross-language parity. Each top-level key matches a `BackendDialect` method; each entry has `in` and `out`. Backticks must be JSON-escaped.

```json
{
  "backend": "mariadb",
  "quote_ident": [
    {"in": "my_table", "out": "`my_table`"},
    {"in": "abc", "out": "`abc`"},
    {"in": "with_underscore_123", "out": "`with_underscore_123`"}
  ],
  "fq_table": [
    {"in": ["chunkshop", "test_chunks"], "out": "`chunkshop`.`test_chunks`"},
    {"in": ["mydb", "my_table"], "out": "`mydb`.`my_table`"}
  ],
  "vector_type_ddl": [
    {"in": 384, "out": "VECTOR(384)"},
    {"in": 1024, "out": "VECTOR(1024)"},
    {"in": 1, "out": "VECTOR(1)"}
  ],
  "json_path_sql": [
    {"in": ["metadata", "a"], "out": "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a'))"},
    {"in": ["metadata", "a.b"], "out": "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a.b'))"},
    {"in": ["metadata", "a.b.c"], "out": "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a.b.c'))"}
  ],
  "upsert_clause": [
    {"in": {"keys": ["id"], "updates": []}, "out": ""},
    {"in": {"keys": ["id"], "updates": ["content"]}, "out": "ON DUPLICATE KEY UPDATE `content` = VALUES(`content`)"},
    {"in": {"keys": ["id"], "updates": ["a", "b"]}, "out": "ON DUPLICATE KEY UPDATE `a` = VALUES(`a`), `b` = VALUES(`b`)"},
    {"in": {"keys": ["a", "b"], "updates": ["c"]}, "out": "ON DUPLICATE KEY UPDATE `c` = VALUES(`c`)"}
  ],
  "create_database_sql": [
    {"in": "chunkshop", "out": "CREATE DATABASE IF NOT EXISTS `chunkshop`"},
    {"in": "my_db", "out": "CREATE DATABASE IF NOT EXISTS `my_db`"}
  ],
  "drop_table_sql": [
    {"in": "`db`.`t`", "out": "DROP TABLE `db`.`t`"}
  ],
  "add_column_if_not_exists_sql": [
    {"in": ["`db`.`t`", "source", "VARCHAR(255)"], "out": "ALTER TABLE `db`.`t` ADD COLUMN IF NOT EXISTS `source` VARCHAR(255)"}
  ],
  "vector_literal": [
    {"in": [0.1, 0.2, -0.3], "out": "VEC_FromText('[0.100000,0.200000,-0.300000]')"},
    {"in": [], "out": "VEC_FromText('[]')"}
  ]
}
```

- [ ] **Step 2: Verify JSON parses**

```bash
python3 -c "import json; json.load(open('/home/yonk/yonk-tools/chunkshop-r2-mariadb/rust/chunkshop/tests/parity-fixtures/dialect-mariadb.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/tests/parity-fixtures/dialect-mariadb.json
git commit -m "test(parity): cross-language BackendDialect fixture for MariaDB"
```

---

### Task 3: Write `dialect_mariadb_parity.rs` (red)

**Files:**
- Create: `rust/chunkshop/tests/dialect_mariadb_parity.rs`

**Reference:** `tests/dialect_postgres_parity.rs` (135 lines) — copy the structure, swap `PostgresBackend` → `MariadbBackend`, add a `vector_literal_parity` test (PG fixture didn't have it; MariaDB does).

- [ ] **Step 1: Write the failing test**

```rust
//! Cross-language dialect parity test for MariaDB. Both Python and Rust assert
//! their BackendDialect impls produce the byte-for-byte outputs in the fixture.

use chunkshop::backends::{BackendDialect, MariadbBackend};
use serde_json::Value;

const FIXTURE_PATH: &str = "tests/parity-fixtures/dialect-mariadb.json";

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read parity fixture");
    serde_json::from_str(&raw).expect("parse parity fixture")
}

fn backend() -> MariadbBackend {
    MariadbBackend::new("UNUSED_FOR_DIALECT_PARITY".to_string())
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
fn upsert_clause_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["upsert_clause"].as_array().unwrap() {
        let inp = &case["in"];
        let keys: Vec<&str> = inp["keys"].as_array().unwrap()
            .iter().map(|v| v.as_str().unwrap()).collect();
        let updates: Vec<&str> = inp["updates"].as_array().unwrap()
            .iter().map(|v| v.as_str().unwrap()).collect();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.upsert_clause(&keys, &updates),
            expected,
            "upsert_clause(keys={keys:?}, updates={updates:?})"
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
fn drop_table_sql_parity() {
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

#[test]
fn vector_literal_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["vector_literal"].as_array().unwrap() {
        let inp: Vec<f32> = case["in"].as_array().unwrap()
            .iter().map(|v| v.as_f64().unwrap() as f32).collect();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.vector_literal(&inp), expected, "vector_literal({inp:?})");
    }
}
```

- [ ] **Step 2: Verify it fails to compile (red)**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo test -p chunkshop-rs --test dialect_mariadb_parity 2>&1 | tail -10
```
Expected: compile error — `unresolved import chunkshop::backends::MariadbBackend`. This is the expected red state.

- [ ] **Step 3: Commit (red test before impl)**

```bash
git add rust/chunkshop/tests/dialect_mariadb_parity.rs
git commit -m "test(parity): RED — dialect_mariadb_parity test pending MariadbBackend impl"
```

---

### Task 4: Implement `MariadbBackend` (BackendDialect + BackendConn)

**Use a subagent for this task — it is substantive (~250 lines).** Tier-2 discipline says implementer subagent + one spec-review check.

**Files:**
- Create: `rust/chunkshop/src/backends/mariadb.rs` (target ~250 lines)
- Modify: `rust/chunkshop/src/backends/mod.rs` (add `pub mod mariadb;` + re-export of `MariadbBackend`)

**Reference (read all four before writing):**
- `python/src/chunkshop/backends/mariadb.py` (156 lines — the canonical spec)
- `rust/chunkshop/src/backends/postgres.rs` (~460 lines — Rust structural template)
- `rust/chunkshop/src/backends/base.rs` (trait surface — what to impl)
- `python/src/chunkshop/backends/postgres.py` (for cross-checking how Python's PG vs MariaDB differ)

**Subagent prompt:** "Implement `MariadbBackend` at `rust/chunkshop/src/backends/mariadb.rs` to satisfy the `Backend` super-trait (`BackendDialect` + `BackendConn`). Mirror the structure of `rust/chunkshop/src/backends/postgres.rs` exactly — struct + lazy pool + `BackendDialect` impl + `BackendConn` impl + `#[cfg(test)]` unit tests at the bottom. Source of truth for behavior is `python/src/chunkshop/backends/mariadb.py`. Read all four reference files end-to-end before writing. Critical specs are listed below."

**Critical specs the subagent MUST satisfy:**

1. **Struct shape:** `pub struct MariadbBackend { dsn_env: String, pool: tokio::sync::OnceCell<sqlx::MySqlPool> }`. Constructor `pub fn new(dsn_env: String) -> Self`. Lazy pool method `pub async fn pool(&self) -> Result<&sqlx::MySqlPool>` mirrors PG's pattern with `max_connections(1)`.

2. **`impl BackendDialect for MariadbBackend`** — every method from `base.rs:36-67`. Behavior crib sheet (matching `python/src/chunkshop/backends/mariadb.py`):
   - `NAME = "mariadb"`, `SUPPORTS_UPSERT = true`
   - `quote_ident(name)`: backticks with embedded-backtick doubling — `format!("`{}`", name.replace('`', "``"))`. Defense-in-depth even though config-load regex disallows backticks.
   - `fq_table(db, table)`: `format!("{}.{}", quote_ident(db), quote_ident(table))`
   - `vector_type_ddl(dim)`: `format!("VECTOR({dim})")`
   - `json_type_ddl()`: `"JSON".to_string()`
   - `tags_array_type_ddl()`: `"JSON".to_string()` — MariaDB has no native arrays; Python serializes tags as JSON.
   - `text_pk_type_ddl()`: `"VARCHAR(255)".to_string()`
   - `timestamp_now_default_ddl()`: `"TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()`
   - `vector_literal(arr)`: `format!("VEC_FromText('[{}]')", arr.iter().map(|x| format!("{x:.6}")).collect::<Vec<_>>().join(","))`
   - `json_literal(obj)`: `serde_json::to_string(obj).unwrap_or_else(|_| "null".into())`
   - `json_path_sql(col, path)`: `format!("JSON_UNQUOTE(JSON_EXTRACT({col},'$.{path}'))")`
   - `upsert_clause(_keys, updates)`: empty updates → `""` (sink switches to `INSERT IGNORE`); else `format!("ON DUPLICATE KEY UPDATE {sets}")` where `sets = updates.iter().map(|c| format!("{q} = VALUES({q})", q=quote_ident(c))).join(", ")`. **The `_keys` arg is ignored** (mirrors Python's `del key_cols`).
   - `create_database_sql(name)`: `format!("CREATE DATABASE IF NOT EXISTS {}", quote_ident(name))`
   - `add_column_if_not_exists_sql(fq, col, ty)`: `format!("ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {} {ty}", quote_ident(col))`
   - `drop_table_sql(fq)`: `format!("DROP TABLE {fq}")`
   - `emit_chunks_table_ddl(fq, cols, hnsw, _dim, engine)`: build column lines with optional default + nullable; primary key from `is_primary_key`; if `hnsw`, append `,\n  VECTOR INDEX ` + quote_ident("vec_idx") + ` (` + quote_ident("embedding") + `)`; always append `,\n  KEY ` + quote_ident("doc_seq_idx") + ` (` + quote_ident("doc_id") + `, ` + quote_ident("seq_num") + `)`; engine clause `ENGINE=` + (engine.unwrap_or("InnoDB")). **Returns a Vec with ONE statement** (Python's `[f"CREATE TABLE …"]`), unlike PG which returns 1+1+(1) statements.

3. **`impl BackendConn for MariadbBackend`:**
   - `type Db = sqlx::MySql;`
   - `connect()`: lazy-initialize pool. After successful pool init, run `SELECT VERSION()` and call `parse_mariadb_version` (helper below). If parsed version `(major, minor) < (11, 7)`, return `Err(anyhow!(...))` with the message: `"MariaDB 11.7+ required for native VECTOR support; got {version_string!r}"`. Mirror Python's exact phrasing if possible.
   - `acquire_create_lock(tx, key)`: MariaDB equivalent of PG advisory lock — use `GET_LOCK(?, 30)` named lock. Lock name = `format!("chunkshop_{key}")` truncated to 64 chars (`if name.len() > 64 { name.truncate(64); }`). Bind via `?`. After execution, fetch the result column; if not `1`, return `Err(anyhow!("could not acquire MariaDB lock {name!r} within 30s"))`. Note: GET_LOCK is connection-scoped, not transaction-scoped — release isn't strictly needed here because the connection is short-lived, but mirror Python's `RELEASE_LOCK` behavior at the end if practical given the trait shape.
   - `table_exists(tx, db, table)`: query `information_schema.tables` with bind for both. Return `count > 0`.
   - `embedding_dim(tx, db, table)`: query `SELECT column_type FROM information_schema.columns WHERE table_schema=? AND table_name=? AND column_name='embedding'`. If no row, `Ok(None)`. Else regex-parse `column_type` against `^vector\((\d+)\)$` (case-insensitive — MariaDB returns lowercase `vector(N)`).

4. **Helper: `fn parse_mariadb_version(s: &str) -> Result<(u32, u32)>`** — module-private, parses `"11.7.2-MariaDB-ubu2404"` → `Ok((11, 7))`; rejects malformed input with a clear error. Used by `connect()` for the min-version gate. Add unit tests covering: `"11.7.2-MariaDB-ubu2404"` → `Ok((11, 7))`; `"11.7.0"` → `Ok((11, 7))`; `"10.11.5-MariaDB"` → `Ok((10, 11))` (parser succeeds, gate rejects later); `""` and `"abc"` → `Err`.

5. **Unit tests at the bottom of `mariadb.rs`** (mirror `postgres.rs:266-460` pattern). At minimum:
   - `quote_ident_wraps_in_backticks`, `quote_ident_doubles_embedded_backtick`, `fq_table_quotes_both_segments`
   - `vector_type_ddl`, `json_type_ddl_is_json`, `tags_array_type_ddl_is_json`, `text_pk_type_ddl_is_varchar_255`
   - `vector_literal_format_matches_python`, `vector_literal_empty`
   - `json_path_sql_single_segment`, `json_path_sql_two_segments`, `json_path_sql_three_segments`
   - `upsert_clause_empty_returns_empty_string`, `upsert_clause_single_update`, `upsert_clause_composite_key`
   - `create_database_sql_uses_database_for_mariadb`
   - `add_column_if_not_exists_sql_format`, `drop_table_sql_format`
   - `emit_chunks_table_ddl_no_hnsw`, `emit_chunks_table_ddl_with_hnsw` (assert single CREATE TABLE statement, with VECTOR INDEX line when hnsw=true; ENGINE=InnoDB suffix; KEY doc_seq_idx clause always present)
   - `parse_mariadb_version_real_string`, `parse_mariadb_version_just_numbers`, `parse_mariadb_version_old_version`, `parse_mariadb_version_malformed_errors`

6. **Modify `rust/chunkshop/src/backends/mod.rs`:** add `pub mod mariadb;` after `pub mod postgres;` (line 8) and `pub use mariadb::MariadbBackend;` after the `pub use postgres::PostgresBackend;` line. **Do NOT add `AnyBackend::Mariadb` variant or `load_backend` arm yet** — those land in Task 9 (Phase D wiring).

- [ ] **Step 1: Dispatch implementer subagent**

Dispatch a `general-purpose` subagent with the spec above. Tell it to read all four reference files, write `mariadb.rs`, modify `mod.rs` (just `pub mod` + `pub use`), and report back the file paths + line counts.

- [ ] **Step 2: Spec-review pass on the diff**

After the subagent reports done, read the new `mariadb.rs` and verify:
- Every `BackendDialect` method exists with the right signature
- `BackendConn::Db = sqlx::MySql`
- `parse_mariadb_version` is module-private (no `pub fn`)
- No `pub use sqlx::*` re-exports leaking the underlying driver
- `quote_ident` uses backticks not double-quotes
- The `connect()` body checks version after pool init, not before

Compare against the fixture's expected outputs (`tests/parity-fixtures/dialect-mariadb.json`) for one or two methods (e.g., `quote_ident("my_table")` should match `` "`my_table`" ``).

- [ ] **Step 3: Run dialect parity test (should now pass — green)**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo test -p chunkshop-rs --test dialect_mariadb_parity 2>&1 | tail -15
```
Expected: 9 tests passed (one per fixture key).

- [ ] **Step 4: Run full test suite**

```bash
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected: `TOTAL passed=126 + N (where N = MariadbBackend unit tests + dialect_mariadb_parity tests, ~25-30 added) failed=0 ignored=1`.

- [ ] **Step 5: DC-002 drift check**

Re-read the mission brief. Confirm:
- SC-001 evidence: add `fn _assert_backend<B: chunkshop::Backend>() {}; #[test] fn assert_mariadb_is_backend() { _assert_backend::<MariadbBackend>(); }` to the `#[cfg(test)] mod tests` block in `mariadb.rs`. Compile success = SC-001 satisfied.
- SC-005 evidence: `dialect_mariadb_parity` 9 tests pass.
- SC-008 evidence: `parse_mariadb_version` unit tests pass.

If any of these aren't satisfied, the subagent's impl is incomplete — go back and fix before continuing.

- [ ] **Step 6: Commit Phase B**

```bash
git add rust/chunkshop/src/backends/mariadb.rs rust/chunkshop/src/backends/mod.rs
git commit -m "feat(backends): MariadbBackend impl Backend (BackendDialect + BackendConn)

Mirrors python/src/chunkshop/backends/mariadb.py. Backticks for ident
quoting; inline VEC_FromText for vector literal; ON DUPLICATE KEY UPDATE
for upsert; GET_LOCK for create-time mutex. Min-version gate rejects
MariaDB < 11.7 on connect. Single CREATE TABLE statement (vs. PG's
CREATE+CREATE INDEX sequence) because VECTOR INDEX is inline.

Satisfies R2-SC-001 (Backend impl), SC-005 (dialect parity), SC-008
(min-version), and partial SC-009 (GAT lift call site for MariaDB)."
```

---

### Task 5: `tests/backend_mariadb_conn.rs` integration test

**Files:**
- Create: `rust/chunkshop/tests/backend_mariadb_conn.rs`

**Reference:** `tests/backend_postgres_conn.rs` (79 lines).

- [ ] **Step 1: Write the integration test**

```rust
//! BackendConn integration tests for MariadbBackend.
//!
//! Skips if `CHUNKSHOP_TEST_DSN_MARIADB` is unset (matches the rest of the
//! integration test suite's skip-if-no-DSN pattern).

use chunkshop::backends::{BackendConn, MariadbBackend};
use sqlx::mysql::MySqlPoolOptions;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_pool_init_and_min_version() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    // Calling connect a second time is idempotent.
    backend.connect().await?;
    Ok(())
}

#[tokio::test]
async fn acquire_create_lock_and_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    let pool = MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&std::env::var(DSN_ENV).unwrap())
        .await?;

    let mut tx = pool.begin().await?;
    backend.acquire_create_lock(&mut tx, "chunkshop_r2_test").await?;

    // Create a temp database and table to introspect.
    sqlx::query("CREATE DATABASE IF NOT EXISTS `chunkshop_r2_test`")
        .execute(&mut *tx)
        .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert!(!exists, "synthetic should not exist yet");

    sqlx::query(
        "CREATE TABLE `chunkshop_r2_test`.`synthetic` (id VARCHAR(255) PRIMARY KEY, embedding VECTOR(8))",
    )
    .execute(&mut *tx)
    .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert!(exists, "synthetic should exist after CREATE TABLE");

    let dim = backend
        .embedding_dim(&mut tx, "chunkshop_r2_test", "synthetic")
        .await?;
    assert_eq!(dim, Some(8));

    // Cleanup
    sqlx::query("DROP DATABASE IF EXISTS `chunkshop_r2_test`")
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
```

- [ ] **Step 2: Run with DSN set**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cargo test -p chunkshop-rs --test backend_mariadb_conn 2>&1 | tail -10
```
Expected: 2 tests passed.

- [ ] **Step 3: Run without DSN to confirm skip behavior**

```bash
unset CHUNKSHOP_TEST_DSN_MARIADB
cargo test -p chunkshop-rs --test backend_mariadb_conn 2>&1 | tail -10
```
Expected: 2 tests passed (both early-return after the skip message).

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/backend_mariadb_conn.rs
git commit -m "test(backends): integration tests for MariadbBackend BackendConn"
```

---

## Phase C — `MariadbSink` Impl (DC-003)

### Task 6: Implement `MariadbSink`

**Use a subagent for this task — it is substantive (~400 lines).** Tier-2 discipline applies.

**Files:**
- Create: `rust/chunkshop/src/sinks/mariadb.rs` (target ~400 lines)
- Modify: `rust/chunkshop/src/sinks/mod.rs` (add `pub mod mariadb;` + `pub use mariadb::MariadbSink;`. **Do NOT add the `AnySink::Mariadb` variant yet** — that lands in Task 9.)

**Reference:**
- `python/src/chunkshop/sinks/mariadb.py` (247 lines — canonical spec)
- `rust/chunkshop/src/sinks/pg.rs` (~410 lines — Rust structural template)

**Subagent prompt:** "Implement `MariadbSink` at `rust/chunkshop/src/sinks/mariadb.rs` to satisfy the `Sink` trait (5 methods: `create_table`, `write_document`, `delete_document`, `count_docs`, `query_top_k`). Mirror the structure of `rust/chunkshop/src/sinks/pg.rs` exactly. Source of truth for behavior is `python/src/chunkshop/sinks/mariadb.py`. Read both reference files end-to-end before writing. Critical specs are listed below."

**Critical specs the subagent MUST satisfy:**

1. **Struct shape:** `pub struct MariadbSink { cfg: MariadbTargetConfig, backend: MariadbBackend, embed_dim: usize }` — concrete-typed, like `PgSink`. Constructor: `pub fn new(cfg: MariadbTargetConfig, backend: MariadbBackend, embed_dim: usize) -> Self`.

   **Note:** `MariadbTargetConfig` does not yet exist in `config.rs`. Task 9 adds it. **For Task 6**, define a placeholder local struct in `mariadb.rs` for now OR (preferred) edit `config.rs` to add the struct (without the `TargetConfig::Mariadb` enum arm — just the struct itself) so the sink can reference the real type. The subagent should add the struct definition only — leave the enum variant for Task 9. Field shape:
   ```rust
   #[derive(Debug, Clone, Deserialize)]
   pub struct MariadbTargetConfig {
       pub dsn_env: String,
       #[serde(rename = "database")]
       pub database_name: String,
       pub table: String,
       #[serde(default)]
       pub overwrite: bool,
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
       #[serde(default)]
       pub delete_orphans: bool,
   }

   impl MariadbTargetConfig {
       fn validate(&self) -> Result<()> {
           if self.mode == "append" && self.source_tag.is_none() {
               return Err(anyhow!(
                   "target.mode='append' requires target.source_tag to identify this cell"
               ));
           }
           Ok(())
       }
   }
   ```
   Keep `validate()` private to `config.rs` and matched in the eventual `TargetConfig::validate` arm — Task 9 wires that.

2. **`canonical_cols(b, dim)`** mirroring `pg.rs:43-56` but with MariaDB-typed columns (matching `python/src/chunkshop/sinks/mariadb.py:24-36`):
   - `id` → `VARCHAR(255)`, PK, NOT NULL
   - `doc_id` → `VARCHAR(255)`, NOT NULL
   - `seq_num` → `INT`, NOT NULL
   - `original_content` → `LONGTEXT`, NOT NULL
   - `embedded_content` → `LONGTEXT`, NOT NULL
   - `tags` → `JSON`, NOT NULL, default `(JSON_ARRAY())`
   - `metadata` → `JSON`, NOT NULL, default `(JSON_OBJECT())`
   - `embedding` → `VECTOR({dim})`, NOT NULL
   - `source` → `VARCHAR(255)`, nullable
   - `created_at` → `TIMESTAMP`, NOT NULL, default `CURRENT_TIMESTAMP`

3. **`Sink::create_table()` mode dispatch** — mirror `pg.rs:189-218` structure but adapt for MariaDB transaction semantics. Critical differences from PG:
   - **No `CREATE EXTENSION` step** — MariaDB has VECTOR built in (since 11.7).
   - `create_database_sql` produces `CREATE DATABASE IF NOT EXISTS \`name\``, not `CREATE SCHEMA …`.
   - `acquire_create_lock` uses GET_LOCK semantics; the trait surface stays the same (`&mut Transaction<'_, MySql>` because `Self::Db = MySql`).
   - All schema-setup statements run inside ONE transaction (same atomicity goal as PG): foreign-tag check + drop + create + add columns.

4. **`overwrite_create_in_tx`** — mirror `pg.rs:76-113`:
   - If table exists AND `!force_overwrite`: query `SELECT DISTINCT source FROM <fq> WHERE source IS NOT NULL LIMIT 10`, collect into `BTreeSet<String>`, compute `foreign = existing - {my_tag}`. If non-empty, return `Err(anyhow!("overwrite refuses to drop {db}.{table}: ..."))` with the same message structure as PG.
   - If table exists: `DROP TABLE`.
   - Call `create_base_ddl_in_tx`.

5. **`create_if_missing_in_tx`** — mirror `pg.rs:115-124`. If not exists → `create_base_ddl_in_tx`. Else → `ADD COLUMN IF NOT EXISTS source VARCHAR(255)` + `ensure_promote_columns_in_tx`.

6. **`append_preflight_in_tx`** — mirror `pg.rs:126-156`. Refuse if table doesn't exist (require `create_if_missing` for first cell). Refuse if no embedding column. Refuse if dim mismatch. Add source column + promote columns.

7. **`ensure_promote_columns_in_tx`** — mirror `pg.rs:158-175`. Loop `cfg.promote_metadata`; emit `ADD COLUMN IF NOT EXISTS` with `_pg_type_to_mariadb(pc.type_)` mapping (mirror Python's `_PG_TO_MARIADB_TYPE` dict at `python/src/chunkshop/sinks/mariadb.py:233-242`):
   ```rust
   fn pg_type_to_mariadb(pg_type: &str) -> &str {
       match pg_type {
           "text" => "TEXT",
           "text[]" => "JSON",      // MariaDB has no native array
           "int" => "INT",
           "bigint" => "BIGINT",
           "boolean" => "BOOLEAN",
           "jsonb" => "JSON",
           "timestamptz" => "TIMESTAMP",
           "date" => "DATE",
           other => other,
       }
   }
   ```

8. **`Sink::write_document` — the load-bearing method.** Mirror `pg.rs:220-342` but with these MariaDB-specific differences:
   - **Vector splice INLINE.** Build the SQL string per-row, replacing the `embedding` column's placeholder with `backend.vector_literal(emb)` (which yields `VEC_FromText('[...]')`). Other columns bind as `?` placeholders. Mirror `python/src/chunkshop/sinks/mariadb.py:148-167`.
   - **Placeholders are `?`, not `$1..$N`.** No casts (PG's `::jsonb`, `::<type>`); MariaDB infers from column type.
   - **`tags` binds as a JSON string**, not as a `Vec<String>`: `serde_json::to_string(tags_vec)?`. Bind that string to a JSON column.
   - **`metadata` binds as a JSON string**: same pattern, `serde_json::to_string(&c.metadata)?`.
   - **Upsert clause:** uses `ON DUPLICATE KEY UPDATE` set list excluding `id`/`doc_id`/`seq_num` AND `source` (write-once). Mirror `pg.rs:275-285`'s exclusion list.
   - **Insert SQL is per-row** because the vector literal is inline. Loop chunks; build one INSERT string per chunk; bind non-vector params; execute; collect any error.
   - **delete_orphans:** same shape as PG but `?` placeholders: `DELETE FROM <fq> WHERE doc_id = ? AND seq_num >= ?`.
   - All inside ONE transaction; commit at end.

9. **`Sink::delete_document(doc_id)`** — mirror `pg.rs:344-359`. With `source_tag`: `DELETE FROM <fq> WHERE doc_id = ? AND source = ?`. Without: `DELETE FROM <fq> WHERE doc_id = ?`. Return `rows_affected as i64`.

10. **`Sink::count_docs()`** — `SELECT COUNT(DISTINCT doc_id) FROM <fq>`. Same as PG.

11. **`Sink::query_top_k(query_vec, k)`** — mirror `python/src/chunkshop/sinks/mariadb.py:215-230`. **Inline vector literal** as `VEC_FromText('[...]')`:
    ```sql
    SELECT doc_id, seq_num, VEC_DISTANCE_COSINE(embedding, VEC_FromText('[...]')) AS distance
    FROM <fq> ORDER BY distance LIMIT ?
    ```
    Bind only `k`. Return `Vec<(String, i32, f64)>`.

12. **`pub async fn pool(&self) -> Result<&MySqlPool>`** inherent helper on `MariadbSink` (mirroring `pg.rs:70` accessor) — used only for tests' direct cleanup. NOT on the `Sink` trait.

13. **No `sqlx::MySql` outside `backends/mariadb.rs` and `sinks/mariadb.rs`.** SC-002 requires this. Verified by post-impl grep.

- [ ] **Step 1: Add `MariadbTargetConfig` struct to `config.rs`**

This is the only inline edit before the subagent dispatch. Edit `rust/chunkshop/src/config.rs`. After `PostgresTargetConfig` (currently ending around line 750) and before `default_dsn_env`, add:

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. Same shape
    /// as PostgresTargetConfig.
    #[serde(default)]
    pub overwrite: bool,
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
    #[serde(default)]
    pub delete_orphans: bool,
}

impl MariadbTargetConfig {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        Ok(())
    }
}
```

**Do NOT add the `TargetConfig::Mariadb` enum variant yet** — that's Task 9. The struct is defined but not yet routable from YAML. This is intentional: it lets MariadbSink reference the type without cascading into wiring.

- [ ] **Step 2: Verify `cargo build -p chunkshop-rs` still clean**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -3
```
Expected: `Finished`. There may be a "struct `MariadbTargetConfig` is never constructed" warning — that's expected and resolved when the sink uses it.

- [ ] **Step 3: Dispatch implementer subagent for `MariadbSink`**

Dispatch a `general-purpose` subagent with the full spec above. Tell it to:
1. Read `python/src/chunkshop/sinks/mariadb.py` and `rust/chunkshop/src/sinks/pg.rs` end-to-end first.
2. Create `rust/chunkshop/src/sinks/mariadb.rs` matching the spec.
3. Modify `rust/chunkshop/src/sinks/mod.rs`: add `pub mod mariadb;` and `pub use mariadb::MariadbSink;`. Do NOT add the `AnySink::Mariadb` variant or `load_sink` arm.
4. Report file paths and line counts.

- [ ] **Step 4: Spec-review the diff**

After the subagent reports done, read `mariadb.rs` and verify:
- All 5 `Sink` trait methods implemented
- `write_document` builds per-row INSERT SQL with inline `VEC_FromText` (grep for `VEC_FromText` in the file)
- `ON DUPLICATE KEY UPDATE` set list excludes `id`, `doc_id`, `seq_num`, AND `source` (grep for the update-cols list)
- No `sqlx::Postgres` references (grep `sqlx::Postgres` should yield nothing in `mariadb.rs`)
- `pool()` returns `&MySqlPool`, not `&PgPool`

Run the SC-002 grep guard:
```bash
grep -rn "sqlx::MySql\|sqlx::mysql::" rust/chunkshop/src/ | grep -v "src/backends/mariadb.rs\|src/sinks/mariadb.rs"
```
Expected: empty (no leakage).

- [ ] **Step 5: Verify the crate compiles**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -5
```
Expected: `Finished`. Warnings about unused are acceptable until Task 9 wires them.

- [ ] **Step 6: Commit Phase C**

```bash
git add rust/chunkshop/src/config.rs rust/chunkshop/src/sinks/mariadb.rs rust/chunkshop/src/sinks/mod.rs
git commit -m "feat(sinks): MariadbSink impl Sink (5 methods + mode dispatch)

Mirrors python/src/chunkshop/sinks/mariadb.py. Vector splice inline as
VEC_FromText('[...]') because sqlx-mysql has no VECTOR adapter. ON
DUPLICATE KEY UPDATE excludes source (write-once provenance contract,
matches PG's behavior). delete_orphans, mode dispatch (overwrite/append/
create_if_missing), foreign-tag safety, append preflight: all mirror PG.

Adds MariadbTargetConfig to config.rs (without the TargetConfig::Mariadb
enum arm — Task 9 wires that).

Satisfies R2-SC-002 (Sink impl) and partial SC-007 (config struct in place)."
```

---

### Task 7: `tests/mariadb_sink_create_table.rs` integration test

**Files:**
- Create: `rust/chunkshop/tests/mariadb_sink_create_table.rs`

**Reference:** `tests/pg_sink_create_table.rs` (45 lines).

- [ ] **Step 1: Write the test**

```rust
//! Sanity integration test for MariadbSink::create_table.

use chunkshop::backends::MariadbBackend;
use chunkshop::config::MariadbTargetConfig;
use chunkshop::sinks::{MariadbSink, Sink};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn create_table_overwrite_mode() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }

    let cfg = MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_r2_sink".to_string(),
        table: "ct".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("r2-test".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    };
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    let sink = MariadbSink::new(cfg, backend, 8);
    sink.create_table().await?;

    // Cleanup
    let pool = sink.pool().await?;
    sqlx::query("DROP DATABASE IF EXISTS `chunkshop_r2_sink`")
        .execute(pool)
        .await?;
    Ok(())
}
```

- [ ] **Step 2: Run with DSN**

```bash
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo test -p chunkshop-rs --test mariadb_sink_create_table 2>&1 | tail -10
```
Expected: 1 test passed.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/tests/mariadb_sink_create_table.rs
git commit -m "test(sinks): integration test for MariadbSink::create_table overwrite mode"
```

---

## Phase D — Source + Wiring (DC-004)

### Task 8: Implement `MariadbTableSource` + integration test

**Files:**
- Create: `rust/chunkshop/src/sources/mariadb_table.rs` (~110 lines)
- Modify: `rust/chunkshop/src/sources/mod.rs` — add `pub mod mariadb_table;` and `pub use mariadb_table::MariadbTableSource;`. **Do NOT add the `AnySource::MariadbTable` variant** — Task 9.
- Create: `rust/chunkshop/tests/mariadb_table_source.rs`
- Modify: `rust/chunkshop/src/config.rs` — add `MariadbTableSourceConfig` struct (without the `SourceConfig::MariadbTable` enum arm)

**Reference:**
- `python/src/chunkshop/sources/mariadb_table.py` (56 lines)
- `rust/chunkshop/src/sources/pg_table.rs` (106 lines)
- `tests/pg_table_source.rs` (162 lines)

This is small enough to do inline (no subagent).

- [ ] **Step 1: Add `MariadbTableSourceConfig` to `config.rs`**

Edit `rust/chunkshop/src/config.rs`. After `PgTableSourceConfig` (around line 293), add:

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Same
    /// contract as PgTableSourceConfig.where_clause — NOT validated.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}
```

- [ ] **Step 2: Create `rust/chunkshop/src/sources/mariadb_table.rs`**

```rust
//! MariaDB source. Mirrors `python/src/chunkshop/sources/mariadb_table.py`.

use anyhow::{Context, Result};
use serde_json::json;

use crate::backends::base::{BackendConn, BackendDialect};
use crate::backends::mariadb::MariadbBackend;
use crate::config::MariadbTableSourceConfig;
use crate::sources::base::Document;

pub struct MariadbTableSource {
    cfg: MariadbTableSourceConfig,
    backend: MariadbBackend,
}

impl MariadbTableSource {
    pub fn new(cfg: MariadbTableSourceConfig) -> Self {
        let backend = MariadbBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut select = format!(
            "SELECT {id_col}, {content_col}",
            id_col = self.backend.quote_ident(&self.cfg.id_column),
            content_col = self.backend.quote_ident(&self.cfg.content_column),
        );
        let mut title_idx: Option<usize> = None;
        if let Some(tc) = &self.cfg.title_column {
            title_idx = Some(2);
            select.push_str(&format!(", {}", self.backend.quote_ident(tc)));
        }
        let meta_start = if title_idx.is_some() { 3 } else { 2 };
        for col in &self.cfg.metadata_columns {
            select.push_str(&format!(", {}", self.backend.quote_ident(col)));
        }
        select.push_str(&format!(
            " FROM {fq}",
            fq = self.backend.fq_table(&self.cfg.database_name, &self.cfg.table)
        ));
        if let Some(w) = &self.cfg.where_clause {
            select.push_str(&format!(" WHERE {w}"));
        }

        self.backend.connect().await?;
        let pool = self.backend.pool().await?;
        let rows = sqlx::query(&select)
            .fetch_all(pool)
            .await
            .with_context(|| format!("running query: {select}"))?;

        let mut out = Vec::with_capacity(rows.len());
        for row in rows {
            use sqlx::Row;
            let id: String = row
                .try_get::<String, _>(0)
                .or_else(|_| row.try_get::<i64, _>(0).map(|n| n.to_string()))
                .or_else(|_| row.try_get::<i32, _>(0).map(|n| n.to_string()))
                .with_context(|| "reading id column from row".to_string())?;
            let content: String = row.try_get(1).context("reading content column")?;
            let title: Option<String> = match title_idx {
                Some(i) => row.try_get::<Option<String>, _>(i).unwrap_or(None),
                None => None,
            };
            let mut meta = serde_json::Map::new();
            for (i, col) in self.cfg.metadata_columns.iter().enumerate() {
                let idx = meta_start + i;
                let v = read_meta_value(&row, idx);
                meta.insert(col.clone(), v);
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

fn read_meta_value(row: &sqlx::mysql::MySqlRow, idx: usize) -> serde_json::Value {
    use sqlx::Row;
    if let Ok(v) = row.try_get::<Option<String>, _>(idx) {
        return v.map(serde_json::Value::String).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<i32>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<f64>, _>(idx) {
        return v.map(|n| json!(n)).unwrap_or(serde_json::Value::Null);
    }
    if let Ok(v) = row.try_get::<Option<bool>, _>(idx) {
        return v.map(|b| json!(b)).unwrap_or(serde_json::Value::Null);
    }
    serde_json::Value::Null
}
```

- [ ] **Step 3: Update `sources/mod.rs`**

Edit `rust/chunkshop/src/sources/mod.rs`. Add `pub mod mariadb_table;` after `pub mod pg_table;` (currently line 11) and `pub use mariadb_table::MariadbTableSource;` after the `pub use pg_table::PgTableSource;` line. **Do not** modify the `AnySource` enum or the `iter_documents` / `load_source` arms — Task 9.

- [ ] **Step 4: Write integration test**

Create `rust/chunkshop/tests/mariadb_table_source.rs`:

```rust
//! Integration test for MariadbTableSource. Mirrors pg_table_source.rs.
//! Skips when CHUNKSHOP_TEST_DSN_MARIADB is unset.

use std::env;

use chunkshop::config::MariadbTableSourceConfig;
use chunkshop::sources::MariadbTableSource;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

#[tokio::test]
async fn mariadb_table_source_emits_three_rows() {
    let dsn = match env::var(DSN_ENV) {
        Ok(v) => v,
        Err(_) => {
            eprintln!("{DSN_ENV} not set; skipping");
            return;
        }
    };
    let pool = sqlx::mysql::MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let database = "chunkshop_mariadb_source_test";
    let table = "rows";
    let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`")).execute(&pool).await;
    sqlx::query(&format!("CREATE DATABASE `{database}`")).execute(&pool).await.expect("create db");
    sqlx::query(&format!(
        "CREATE TABLE `{database}`.`{table}` (
            doc_id VARCHAR(255) PRIMARY KEY,
            body LONGTEXT NOT NULL,
            heading VARCHAR(255)
         )"
    ))
    .execute(&pool)
    .await
    .expect("create table");

    for (id, body, heading) in [
        ("alpha", "Body alpha.", Some("Alpha")),
        ("bravo", "Body bravo.", Some("Bravo")),
        ("charlie", "Body charlie.", None::<&str>),
    ] {
        sqlx::query(&format!(
            "INSERT INTO `{database}`.`{table}` (doc_id, body, heading) VALUES (?, ?, ?)"
        ))
        .bind(id)
        .bind(body)
        .bind(heading)
        .execute(&pool)
        .await
        .expect("insert");
    }

    let cfg = MariadbTableSourceConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: database.to_string(),
        table: table.to_string(),
        id_column: "doc_id".to_string(),
        content_column: "body".to_string(),
        title_column: Some("heading".to_string()),
        where_clause: None,
        metadata_columns: vec![],
    };
    let docs = MariadbTableSource::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 3);

    let mut sorted: Vec<_> = docs.iter().collect();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    assert_eq!(sorted[0].id, "alpha");
    assert_eq!(sorted[0].content, "Body alpha.");
    assert_eq!(sorted[0].title.as_deref(), Some("Alpha"));
    assert_eq!(sorted[2].title, None);

    let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`"))
        .execute(&pool)
        .await;
}
```

- [ ] **Step 5: Verify build + test**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -3
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cargo test -p chunkshop-rs --test mariadb_table_source 2>&1 | tail -5
```
Expected: build clean, 1 test passed.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/sources/mariadb_table.rs rust/chunkshop/src/sources/mod.rs rust/chunkshop/src/config.rs rust/chunkshop/tests/mariadb_table_source.rs
git commit -m "feat(sources): MariadbTableSource (mirrors PgTableSource)

Same column-projection logic as PgTableSource (id, content, optional
title, metadata_columns). Inherits the trusted-operator contract on
where_clause. Satisfies R2-SC-003."
```

---

### Task 9: Wire `AnyBackend::Mariadb` / `AnySink::Mariadb` / `AnySource::MariadbTable` + config enum variants + lib.rs re-exports

This is mechanical wiring. All inline edits, no subagent.

**Files:**
- Modify: `rust/chunkshop/src/backends/mod.rs`
- Modify: `rust/chunkshop/src/sinks/mod.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`
- Modify: `rust/chunkshop/src/config.rs`
- Modify: `rust/chunkshop/src/lib.rs`

- [ ] **Step 1: Add `AnyBackend::Mariadb` and `load_backend` arm**

Edit `rust/chunkshop/src/backends/mod.rs`. Replace the file body (currently 27 lines) with:

```rust
//! Backend module — connection management + dialect helpers per DB engine.

use anyhow::Result;

use crate::config::TargetConfig;

pub mod base;
pub mod mariadb;
pub mod postgres;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use mariadb::MariadbBackend;
pub use postgres::PostgresBackend;

/// Transport sum type — used by the loader to hand a backend to load_sink,
/// where it's pattern-matched back to a concrete type. Sinks store concrete
/// backends (PgSink holds PostgresBackend, MariadbSink holds MariadbBackend),
/// not AnyBackend. So this enum does NOT impl Backend / BackendDialect /
/// BackendConn — no match-delegate boilerplate. R3/R4 add new variants.
pub enum AnyBackend {
    Postgres(PostgresBackend),
    Mariadb(MariadbBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
        TargetConfig::Mariadb(t) => Ok(AnyBackend::Mariadb(MariadbBackend::new(t.dsn_env.clone()))),
    }
}
```

- [ ] **Step 2: Add `AnySink::Mariadb` variant + Sink trait arms + load_sink arm**

Edit `rust/chunkshop/src/sinks/mod.rs`. Replace the entire file (currently 87 lines) with:

```rust
//! Sinks — chunkshop's per-backend data-model semantics layer.

use std::future::Future;

use anyhow::{anyhow, Result};

use crate::backends::AnyBackend;
use crate::chunker::Chunk;
use crate::config::TargetConfig;

pub mod base;
pub mod mariadb;
pub mod pg;

pub use base::Sink;
pub use mariadb::MariadbSink;
pub use pg::PgSink;

/// Sum type for runtime polymorphism. Pipeline holds `AnySink` and calls
/// trait methods through the match-delegate impl below.
pub enum AnySink {
    Pg(PgSink),
    Mariadb(MariadbSink),
}

impl Sink for AnySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.create_table().await,
                AnySink::Mariadb(s) => s.create_table().await,
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
                AnySink::Mariadb(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
            }
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.delete_document(doc_id).await,
                AnySink::Mariadb(s) => s.delete_document(doc_id).await,
            }
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.count_docs().await,
                AnySink::Mariadb(s) => s.count_docs().await,
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
                AnySink::Mariadb(s) => s.query_top_k(query_vec, k).await,
            }
        }
    }
}

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) => {
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim)))
        }
        (TargetConfig::Mariadb(t), AnyBackend::Mariadb(b)) => {
            Ok(AnySink::Mariadb(MariadbSink::new(t.clone(), b, dim)))
        }
        // Cross-variant mismatches are programming errors (load_backend +
        // load_sink are always called paired with the same TargetConfig).
        #[allow(unreachable_patterns)]
        _ => Err(anyhow!("backend / target type mismatch — programming error in load_sink dispatch")),
    }
}
```

- [ ] **Step 3: Add `AnySource::MariadbTable` variant + iter_documents arm + load_source arm**

Edit `rust/chunkshop/src/sources/mod.rs`. Replace the file body with:

```rust
//! Sources — input document iterators per backing store.

use anyhow::{anyhow, Result};

use crate::config::SourceConfig;

pub mod base;
pub mod files;
pub mod http;
pub mod json_corpus;
pub mod mariadb_table;
pub mod pg_table;
pub mod s3;

pub use base::Document;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;
pub use mariadb_table::MariadbTableSource;
pub use pg_table::PgTableSource;
pub use s3::S3Source;

/// Sum type for runtime polymorphism. R2 adds MariadbTable. R3/R4 add Sqlite.
/// ClickhouseTable is deferred to v4.1.
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    MariadbTable(MariadbTableSource),
    Http(HttpSource),
    S3(S3Source),
}

impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::MariadbTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
        }
    }
}

pub fn load_source(cfg: &SourceConfig) -> Result<AnySource> {
    match cfg {
        SourceConfig::Files(c) => Ok(AnySource::Files(FilesSource::new(c.clone()))),
        SourceConfig::JsonCorpus(c) => Ok(AnySource::JsonCorpus(JsonCorpusSource::new(c.clone()))),
        SourceConfig::PgTable(c) => Ok(AnySource::PgTable(PgTableSource::new(c.clone()))),
        SourceConfig::MariadbTable(c) => Ok(AnySource::MariadbTable(MariadbTableSource::new(c.clone()))),
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
```

- [ ] **Step 4: Add `TargetConfig::Mariadb` and `SourceConfig::MariadbTable` enum variants**

Edit `rust/chunkshop/src/config.rs`. Update the `SourceConfig` enum (around line 235):

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
    PgTable(PgTableSourceConfig),
    MariadbTable(MariadbTableSourceConfig),
    Http(HttpSourceConfig),
    S3(S3SourceConfig),
    Inline(InlineSourceConfig),
}
```

Update the `TargetConfig` enum (around line 703):

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    Mariadb(MariadbTargetConfig),
    // R3/R4 add: Sqlite, Clickhouse
}

impl TargetConfig {
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
            TargetConfig::Mariadb(t) => t.validate(),
        }
    }
}
```

Then update the `validate_source_idents` and similar helpers if any reference `SourceConfig::*` arms (search for `SourceConfig::PgTable` in the file and add a parallel `SourceConfig::MariadbTable` arm where applicable — they validate `database_name`, `table`, etc. via `validate_ident`).

```bash
grep -n "SourceConfig::PgTable\|TargetConfig::Postgres\|PostgresTargetConfig" /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust/chunkshop/src/config.rs
```

For each arm found that operates on PG, add the parallel MariaDB arm. Specifically:
- The block around line 855 (`if let SourceConfig::PgTable(p) = &cfg.source`) — add a parallel `if let SourceConfig::MariadbTable(p) = &cfg.source { validate_ident(&p.database_name, "source.database")?; validate_ident(&p.table, "source.table")?; ... }`.
- The block around line 1025 (`let TargetConfig::Postgres(t) = &cfg.target`) — refactor to a `match` covering both variants, validating database_name/table/source_tag/promote_metadata.path the same way.

- [ ] **Step 5: Update `lib.rs` re-exports**

Edit `rust/chunkshop/src/lib.rs`. Replace lines 18 and 25–26:

Line 18:
```rust
pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ColSpec, MariadbBackend, PostgresBackend};
```

Line 25:
```rust
pub use sinks::{AnySink, MariadbSink, PgSink, Sink};
```

Line 26:
```rust
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, MariadbTableSource, PgTableSource, S3Source};
```

- [ ] **Step 6: Verify the crate builds and full test suite passes**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
cargo build -p chunkshop-rs 2>&1 | tail -3
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected: build clean. Test totals = 126 baseline + Phase B/C/D additions, 0 failures.

- [ ] **Step 7: DC-004 drift check**

Re-read the mission brief. Confirm SC-002 grep guard still passes:
```bash
grep -rn "sqlx::MySql\|sqlx::mysql::" rust/chunkshop/src/ | grep -v "src/backends/mariadb.rs\|src/sinks/mariadb.rs\|src/sources/mariadb_table.rs"
```
Expected: empty. The source file is allowed to use `sqlx::mysql::MySqlRow` for typed row reading, so it's listed in the exception.

- [ ] **Step 8: Commit Phase D**

```bash
git add rust/chunkshop/src/backends/mod.rs rust/chunkshop/src/sinks/mod.rs rust/chunkshop/src/sources/mod.rs rust/chunkshop/src/config.rs rust/chunkshop/src/lib.rs
git commit -m "feat(wiring): expose MariaDB through AnyBackend/AnySink/AnySource enums

Adds the variants + factory arms + config enum cases + lib.rs re-exports.
target.type: mariadb and source.type: mariadb_table are now accepted in
YAML and route to the correct concrete impls."
```

---

## Phase E — Cross-Language Parity + Sample Verification

### Task 10: Author cross-language parity fixture

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/mariadb-cross-lang.json`
- Create: `python/scripts/seed_mariadb_cross_lang_fixture.py` (a small helper, not a test)

The fixture is 5 chunks with hand-authored deterministic 8-dim embeddings (no embedder needed — keeps the test fast and reproducible). The Python helper script seeds the table when run; the Rust test reads it back.

- [ ] **Step 1: Create the fixture JSON**

```bash
mkdir -p /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust/chunkshop/tests/parity-fixtures
```

Create `rust/chunkshop/tests/parity-fixtures/mariadb-cross-lang.json`:

```json
{
  "embed_dim": 8,
  "chunks": [
    {
      "doc_id": "doc-alpha",
      "seq_num": 0,
      "original_content": "Alpha original",
      "embedded_content": "Alpha embedded",
      "tags": ["alpha"],
      "metadata": {"kind": "test"},
      "embedding": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    },
    {
      "doc_id": "doc-bravo",
      "seq_num": 0,
      "original_content": "Bravo original",
      "embedded_content": "Bravo embedded",
      "tags": ["bravo"],
      "metadata": {"kind": "test"},
      "embedding": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    },
    {
      "doc_id": "doc-charlie",
      "seq_num": 0,
      "original_content": "Charlie original",
      "embedded_content": "Charlie embedded",
      "tags": ["charlie"],
      "metadata": {"kind": "test"},
      "embedding": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    },
    {
      "doc_id": "doc-delta",
      "seq_num": 0,
      "original_content": "Delta original",
      "embedded_content": "Delta embedded",
      "tags": ["delta"],
      "metadata": {"kind": "test"},
      "embedding": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    },
    {
      "doc_id": "doc-echo",
      "seq_num": 0,
      "original_content": "Echo original",
      "embedded_content": "Echo embedded",
      "tags": ["echo"],
      "metadata": {"kind": "test"},
      "embedding": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    }
  ],
  "queries": [
    {
      "name": "alpha-direction",
      "vec": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
      "expected_top_5_ids_in_order": ["doc-alpha::0", "doc-bravo::0", "doc-charlie::0", "doc-delta::0", "doc-echo::0"]
    }
  ]
}
```

Note on expected ordering: with cosine distance and one-hot embeddings, the alpha-direction query has distance 0 to alpha (perfect match) and distance 1 to all others (orthogonal). Sub-orderings of the orthogonal cases may not be deterministic. **The test should assert position 0 is `doc-alpha::0`, then assert the remaining 4 IDs (positions 1-4) form the set {bravo, charlie, delta, echo}, without strict ordering.**

- [ ] **Step 2: Create the Python seeder helper**

Create `python/scripts/seed_mariadb_cross_lang_fixture.py`:

```python
#!/usr/bin/env python3
"""Seed the cross-language parity fixture into MariaDB via the Python sink.

Used by the Rust integration test tests/mariadb_cross_lang_parity.rs to verify
that vectors written by Python's MariaDbSink are readable+queryable by the
Rust crate.

Usage:
    export CHUNKSHOP_TEST_DSN_MARIADB=mysql://root:rootpw@localhost:3307/chunkshop_test
    python python/scripts/seed_mariadb_cross_lang_fixture.py
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures" / "mariadb-cross-lang.json"

sys.path.insert(0, str(REPO_ROOT / "python" / "src"))
from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.sinks.mariadb import MariaDbSink
from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk


def main() -> int:
    if "CHUNKSHOP_TEST_DSN_MARIADB" not in os.environ:
        print("CHUNKSHOP_TEST_DSN_MARIADB not set — aborting", file=sys.stderr)
        return 2
    fixture = json.loads(FIXTURE.read_text())
    dim = fixture["embed_dim"]

    cfg = TargetConfig(
        type="mariadb",
        dsn_env="CHUNKSHOP_TEST_DSN_MARIADB",
        database="chunkshop_xlang",
        table="parity",
        mode="overwrite",
        source_tag="cross-lang-fixture",
        hnsw=False,
    )
    backend = MariaDBBackend(dsn_env=cfg.dsn_env)
    sink = MariaDbSink(cfg=cfg, backend=backend, embed_dim=dim)
    sink.create_table()

    chunks = [
        Chunk(
            doc_id=c["doc_id"],
            seq_num=c["seq_num"],
            original_content=c["original_content"],
            embedded_content=c["embedded_content"],
            metadata=c["metadata"],
        )
        for c in fixture["chunks"]
    ]
    embs = np.array([c["embedding"] for c in fixture["chunks"]], dtype=np.float32)
    tags = [c["tags"] for c in fixture["chunks"]]

    # Write each chunk as its own "document" — mirrors the test's expectation
    # of independent doc_ids.
    for chunk, emb, tag in zip(chunks, embs, tags):
        sink.write_document(chunk.doc_id, [chunk], np.array([emb]), [tag])

    print(f"Seeded {len(chunks)} chunks into chunkshop_xlang.parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: actual `TargetConfig` / `Chunk` constructor signatures may differ from the placeholder above — verify against `python/src/chunkshop/config.py` and `python/src/chunkshop/chunkers/base.py` while implementing.

- [ ] **Step 3: Verify Python script works**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/python
uv sync --extra dev --extra extractors 2>&1 | tail -3
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
uv run python ../python/scripts/seed_mariadb_cross_lang_fixture.py
```
Expected: `Seeded 5 chunks into chunkshop_xlang.parity`

- [ ] **Step 4: Commit fixture + helper**

```bash
git add rust/chunkshop/tests/parity-fixtures/mariadb-cross-lang.json python/scripts/seed_mariadb_cross_lang_fixture.py
git commit -m "test(parity): cross-language MariaDB fixture + Python seeder"
```

---

### Task 11: Rust cross-language parity test

**Files:**
- Create: `rust/chunkshop/tests/mariadb_cross_lang_parity.rs`

This test assumes `seed_mariadb_cross_lang_fixture.py` ran first (the test prints a clear message instructing how to run it if the table doesn't exist).

- [ ] **Step 1: Write the test**

```rust
//! Cross-language vector parity test for MariaDB. Reads chunks written by the
//! Python sink (via python/scripts/seed_mariadb_cross_lang_fixture.py) and
//! asserts the Rust crate's query_top_k returns the expected ordering.

use std::env;

use chunkshop::backends::MariadbBackend;
use chunkshop::config::MariadbTargetConfig;
use chunkshop::sinks::{MariadbSink, Sink};
use serde_json::Value;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";
const FIXTURE_PATH: &str = "tests/parity-fixtures/mariadb-cross-lang.json";

#[tokio::test]
async fn cross_language_top_k_parity() -> anyhow::Result<()> {
    if env::var(DSN_ENV).is_err() {
        eprintln!("{DSN_ENV} not set; skipping cross-lang parity test");
        return Ok(());
    }

    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read fixture");
    let f: Value = serde_json::from_str(&raw).expect("parse fixture");
    let dim = f["embed_dim"].as_u64().unwrap() as usize;

    // Verify the table exists. If not, instruct the user to seed it first.
    let pool = sqlx::mysql::MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&env::var(DSN_ENV).unwrap())
        .await?;
    let exists: (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM information_schema.tables \
         WHERE table_schema='chunkshop_xlang' AND table_name='parity'",
    )
    .fetch_one(&pool)
    .await?;
    if exists.0 == 0 {
        panic!(
            "chunkshop_xlang.parity does not exist. Seed it first:\n\
             uv run python python/scripts/seed_mariadb_cross_lang_fixture.py"
        );
    }

    let cfg = MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_xlang".to_string(),
        table: "parity".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "create_if_missing".to_string(),
        source_tag: Some("cross-lang-fixture".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    };
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    let sink = MariadbSink::new(cfg, backend, dim);

    let q = &f["queries"][0];
    let qvec: Vec<f32> = q["vec"].as_array().unwrap()
        .iter().map(|v| v.as_f64().unwrap() as f32).collect();
    let expected: Vec<&str> = q["expected_top_5_ids_in_order"].as_array().unwrap()
        .iter().map(|v| v.as_str().unwrap()).collect();

    let results = sink.query_top_k(&qvec, 5).await?;
    assert_eq!(results.len(), 5, "expected top-5 results");

    // Build the canonical id key (doc_id::seq_num) and compare to expected.
    let actual_ids: Vec<String> = results.iter()
        .map(|(doc, seq, _)| format!("{}::{}", doc, seq))
        .collect();

    // Position 0 must be doc-alpha::0 (cos-distance = 0, perfect match).
    assert_eq!(actual_ids[0], expected[0], "top-1 must be alpha");

    // Positions 1-4: orthogonal vectors all have cos-distance = 1.0; ordering
    // among them is implementation-defined. Assert the SET matches.
    use std::collections::BTreeSet;
    let actual_rest: BTreeSet<&str> = actual_ids[1..].iter().map(|s| s.as_str()).collect();
    let expected_rest: BTreeSet<&str> = expected[1..].iter().copied().collect();
    assert_eq!(actual_rest, expected_rest, "remainder set mismatch");

    Ok(())
}
```

- [ ] **Step 2: Seed fixture then run the test**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
uv --project python run python python/scripts/seed_mariadb_cross_lang_fixture.py
cd rust
cargo test -p chunkshop-rs --test mariadb_cross_lang_parity 2>&1 | tail -10
```
Expected: 1 test passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb
git add rust/chunkshop/tests/mariadb_cross_lang_parity.rs
git commit -m "test(parity): cross-language vector parity Rust integration test (SC-004a)"
```

---

### Task 12: Sample YAML run + cross-lang walkthrough doc (SC-007 + SC-004b)

**Files:**
- Verify (no edit needed): `docs/samples/sample-mariadb.yaml` (already exists, verify shape matches the brief's expectation)
- Create: `docs/cross-lang-mariadb-parity.md`

- [ ] **Step 1: Verify `sample-mariadb.yaml` shape**

Read `docs/samples/sample-mariadb.yaml`. Confirm: `target.type: mariadb`, `chunker.type: hierarchy`, `embedder.type: fastembed`, `embedder.model_name: Xenova/bge-base-en-v1.5-int8`, `target.mode: overwrite`. If any field needs adjustment (e.g. `dsn_env: CHUNKSHOP_DSN` is correct?), make the minimal edit.

If a `source_tag: samples` line is present and the doc says `mode: overwrite`, that's fine — overwrite mode allows source_tag without requiring it. Mark this verified.

- [ ] **Step 2: Run the sample end-to-end**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb
export CHUNKSHOP_DSN="mysql://root:rootpw@localhost:3307/chunkshop_samples"
# Pre-create the database since the sink uses CREATE DATABASE IF NOT EXISTS
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw -e "CREATE DATABASE IF NOT EXISTS chunkshop_samples"
cd rust
cargo run -p chunkshop-rs --release -- ingest --config ../docs/samples/sample-mariadb.yaml 2>&1 | tail -15
```

Expected: ingest log shows N documents written. If it errors (e.g. "VECTOR INDEX cannot be created on non-VECTOR column"), capture the error in a follow-up and adjust the sample's `hnsw: true` → `hnsw: false` (HNSW is out of scope per the brief if it doesn't translate cleanly).

- [ ] **Step 3: Verify rows landed**

```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_samples \
  -e "SELECT COUNT(DISTINCT doc_id), COUNT(*) FROM handbook" 2>&1 | tail -3
```
Expected: 4 distinct doc_ids (the four `handbook-*.md` + `release-notes.md` files matching `*-*.md`), N total chunks.

- [ ] **Step 4: Author the manual walkthrough doc**

Create `docs/cross-lang-mariadb-parity.md`:

```markdown
# Cross-Language MariaDB Vector Parity Walkthrough

**Date:** 2026-05-07 (R2 ship date — refresh on re-run)
**Purpose:** Manual proof that vectors written by Python's `MariaDbSink`
are byte-for-byte readable and queryable by the Rust `MariadbSink`.

## Setup

```bash
docker compose -f docker-compose.test.yaml up -d mariadb
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"

# Python venv
cd python
uv sync --extra dev --extra extractors
```

## Step 1: Python writes 5 chunks via MariaDbSink

```bash
cd /home/yonk/yonk-tools/chunkshop
uv --project python run python python/scripts/seed_mariadb_cross_lang_fixture.py
# → Seeded 5 chunks into chunkshop_xlang.parity
```

## Step 2: Inspect rows directly

```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_xlang \
  -e "SELECT id, doc_id, seq_num, source FROM parity ORDER BY doc_id"
```

Expected output:
```
+-----------------+-------------+---------+--------------------+
| id              | doc_id      | seq_num | source             |
+-----------------+-------------+---------+--------------------+
| doc-alpha::0    | doc-alpha   |       0 | cross-lang-fixture |
| doc-bravo::0    | doc-bravo   |       0 | cross-lang-fixture |
| doc-charlie::0  | doc-charlie |       0 | cross-lang-fixture |
| doc-delta::0    | doc-delta   |       0 | cross-lang-fixture |
| doc-echo::0     | doc-echo    |       0 | cross-lang-fixture |
+-----------------+-------------+---------+--------------------+
```

## Step 3: Rust queries top-K via the same vectors

```bash
cd rust
cargo test -p chunkshop-rs --test mariadb_cross_lang_parity -- --nocapture 2>&1 | tail -5
```

Expected: `1 passed`. The test asserts position 0 is `doc-alpha::0` for the
one-hot alpha query; positions 1-4 are the orthogonal set (bravo/charlie/delta/echo).

## Step 4: Raw SQL distance check (manual cross-check)

```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_xlang -e "
SELECT id, ROUND(VEC_DISTANCE_COSINE(embedding,
  VEC_FromText('[1.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000]')), 6) AS d
FROM parity ORDER BY d LIMIT 5"
```

Expected first row: `doc-alpha::0  0.000000` (perfect cosine match). Rows 2-5 will all have distance `1.000000` (orthogonal).

## Conclusion

Python and Rust agree on:
- vector storage format (`VEC_FromText('[...]')` text input)
- ID convention (`doc_id::seq_num`)
- query semantics (`VEC_DISTANCE_COSINE` ordering)

R2-SC-004 satisfied: cross-language vector parity verified by both an
automated Rust integration test and this manual walkthrough.
```

- [ ] **Step 5: Commit walkthrough**

```bash
git add docs/cross-lang-mariadb-parity.md
git commit -m "docs(parity): manual cross-language MariaDB vector parity walkthrough (SC-004b)"
```

---

## Phase F — Final Verify + Merge (DC-FINAL)

### Task 13: Verify-alignment pass + merge `--no-ff`

- [ ] **Step 1: Run the FULL test suite end-to-end**

```bash
cd /home/yonk/yonk-tools/chunkshop-r2-mariadb/rust
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected: `failed=0`. Pass count = 126 baseline + R2 additions (estimate ~155-165 total).

- [ ] **Step 2: SC checklist with evidence**

Re-read the mission brief. For each SC, point to evidence:

- **SC-001:** `_assert_backend::<MariadbBackend>()` compile-time assertion in `src/backends/mariadb.rs` tests block. Build clean.
- **SC-002:** Run `grep -rn "sqlx::MySql\|sqlx::mysql::" rust/chunkshop/src/ | grep -v "src/backends/mariadb.rs\|src/sinks/mariadb.rs\|src/sources/mariadb_table.rs"`. Empty output = pass.
- **SC-003:** `cargo test -p chunkshop-rs --test mariadb_table_source` → 1 passed.
- **SC-004(a):** `cargo test -p chunkshop-rs --test mariadb_cross_lang_parity` → 1 passed.
- **SC-004(b):** `docs/cross-lang-mariadb-parity.md` exists and is dated.
- **SC-005:** `cargo test -p chunkshop-rs --test dialect_mariadb_parity` → 9 passed.
- **SC-006:** Total test count includes 126 baseline preserved. Run `cargo test -p chunkshop-rs --tests 2>&1 | grep "test result" | awk '{p+=$4} END {print p}'` and confirm ≥ 126.
- **SC-007:** `cargo run -p chunkshop-rs --release -- ingest --config docs/samples/sample-mariadb.yaml` ran clean in Task 12.
- **SC-008:** Unit tests in `src/backends/mariadb.rs` for `parse_mariadb_version` cover all four cases.
- **SC-009:** No concrete `sqlx::Postgres` references in `src/backends/base.rs` (other than within doc comments). PG tests pass unchanged.

If any SC lacks evidence, **STOP** and address before merging.

- [ ] **Step 3: Out-of-scope confirmation**

Verify no out-of-scope items were touched:

```bash
git diff main...experimental/v4-rust-mariadb -- rust/chunkshop/src/bakeoff/ 2>&1 | head -5
```
Expected: empty (bakeoff config not modified).

```bash
git diff main...experimental/v4-rust-mariadb -- .github/ 2>&1 | head -5
```
Expected: empty (CI not modified).

- [ ] **Step 4: Merge into integration branch with `--no-ff`**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4
git fetch --all
git checkout experimental/v4-modular-backends
git merge --no-ff experimental/v4-rust-mariadb -m "$(cat <<'EOF'
Merge R2: Rust MariaDB backend + GAT lift on BackendConn

R2 mirrors Python's MariaDbSink/MariaDBBackend/MariaDbTableSource on the
Rust side, satisfies all R2-SC-001..009, and discharges R1's deliberate
seam by lifting BackendConn to a GAT (type Db: sqlx::Database). PgSink
call sites are byte-for-byte unchanged because PgSink holds a concrete
PostgresBackend.

Vector literals splice inline as VEC_FromText('[...]') because sqlx-mysql
has no VECTOR adapter — Python uses the same pattern. ON DUPLICATE KEY
UPDATE excludes source (write-once provenance contract). Min-version
check rejects MariaDB <11.7 on connect.

Cross-language parity verified by a Rust integration test consuming
fixture data written by Python (tests/mariadb_cross_lang_parity.rs)
plus a manual walkthrough at docs/cross-lang-mariadb-parity.md.

After R2: R3 (SQLite) and R4 (ClickHouse) can run in parallel against
the now-cross-backend BackendConn surface.
EOF
)"
```

- [ ] **Step 5: Final test sweep on integration branch**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/rust
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cargo test -p chunkshop-rs 2>&1 | awk '/test result/ {pass+=$4; fail+=$6; ignored+=$8} END {print "TOTAL passed="pass" failed="fail" ignored="ignored}'
```
Expected: same totals as Step 1.

- [ ] **Step 6: Worktree cleanup**

The R2 worktree at `/home/yonk/yonk-tools/chunkshop-r2-mariadb/` is no longer needed once merged. Per project convention, leave it for the user to remove (`git worktree remove ../chunkshop-r2-mariadb`) — DO NOT auto-remove.

---

## Self-Review (post-write)

**Spec coverage check:**
- SC-001 (Backend trait impl) → Task 4 step 5
- SC-002 (Sink trait impl, no leak) → Task 6 step 4 + Task 9 step 7 + Task 13 step 2
- SC-003 (MariadbTableSource) → Task 8
- SC-004(a) (cross-lang fixture test) → Tasks 10 + 11
- SC-004(b) (manual walkthrough) → Task 12 step 4
- SC-005 (dialect parity fixture) → Tasks 2 + 3 + 4 step 3
- SC-006 (baseline preserved) → Task 1 step 1 + Task 4 step 4 + Task 9 step 6 + Task 13 step 1
- SC-007 (sample YAML runs) → Task 12 steps 1-3
- SC-008 (min-version check) → Task 4 step 5 + Task 13 step 2
- SC-009 (GAT lift PG-clean) → Task 1 + Task 13 step 2

All SCs have at least one task covering them. ✅

**Drift checkpoints:**
- DC-001 (after GAT lift) → Task 1 step 7
- DC-002 (after MariadbBackend) → Task 4 step 5
- DC-003 (after MariadbSink) → Task 6 step 4 (spec review)
- DC-004 (after wiring) → Task 9 step 7
- DC-FINAL (before merge) → Task 13 steps 1-3

All drift checkpoints have a task step. ✅

**Type consistency:**
- `MariadbBackend::new(dsn_env: String) -> Self` referenced consistently in Tasks 4, 5, 7, 11, 12.
- `MariadbSink::new(cfg, backend, dim)` referenced consistently.
- `MariadbTargetConfig` field shape matches between Tasks 6 (definition) and 7, 11 (use sites).
- `MariadbTableSourceConfig` field shape matches between Tasks 8 (definition) and use site.
- All test files use `CHUNKSHOP_TEST_DSN_MARIADB` env var.

No naming inconsistencies. ✅
