# R3 — Rust SQLite Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SQLite + sqlite-vec backend to `chunkshop-rs`, mirroring the Python reference at `python/src/chunkshop/{backends,sinks,sources}/sqlite*.py`. Behavioral parity with Python is the bar; the architectural twist is the two-table layout (chunks + `{table}_vec` virtual table) and the sync `rusqlite` driver wrapped behind async via `tokio::task::spawn_blocking`.

**Architecture:** `SQLiteBackend` impls `BackendDialect` cleanly. Connection-management methods (`connect`, `table_exists`, `embedding_dim`, `with_create_lock`) live as inherent `async` methods on the struct (NOT on the PG-shaped `BackendConn` trait — see Mission Brief, R3-SC-001). State held in `Arc<Mutex<rusqlite::Connection>>`. `SqliteSink` writes to BOTH the chunks table AND a `{table}_vec` virtual table per cell; `vec0` refuses UPSERT, so re-writes follow DELETE-by-id then INSERT inside the same `rusqlite::Transaction`.

**Tech Stack:** Rust 1.93 (workspace edition), `rusqlite` 0.32 (bundled SQLite + load_extension feature), `sqlite-vec` 0.1.x crate (extension distribution), `tokio` for async wrapping, `anyhow`, `tracing`. Plus existing crate deps. Worktree: `/home/yonk/yonk-tools/chunkshop-r3-sqlite` on branch `experimental/v4-rust-sqlite`.

**Mission Brief:** [`skill-output/mission-brief/Mission-Brief-r3-rust-sqlite.md`](../../../skill-output/mission-brief/Mission-Brief-r3-rust-sqlite.md) — 10 success criteria (R3-SC-001..010) and 4 drift checkpoints (DC-001..DC-FINAL).

**Working directory for commands:** `/home/yonk/yonk-tools/chunkshop-r3-sqlite/rust`. All `cargo` commands run from there. Worktree root is `/home/yonk/yonk-tools/chunkshop-r3-sqlite/`; YAML samples live under `docs/samples/` from the worktree root.

**Commit style:** `type(scope): subject` — matches existing repo convention. Types: `feat`, `chore`, `refactor`, `docs`, `test`. Common scopes: `backends`, `sinks`, `sources`, `config`, `parity`, `samples`, `lib`, `tests`.

**Drift Checkpoints (MUST execute when reached):**
- ⛔ DC-001 after Task 5 (backend dialect + parity fixture green)
- ⛔ DC-002 after Task 12 (sink fully implemented)
- ⛔ DC-003 before Task 16 (cross-language test scaffolding)
- ⛔ DC-FINAL Task 17 (final sweep, every SC has cited evidence)

---

## File Structure

**New files (rust/chunkshop):**

| File | Responsibility | Lines (approx) |
|---|---|---|
| `src/backends/sqlite.rs` | `SQLiteBackend` struct; `BackendDialect` impl; inherent `connect/table_exists/embedding_dim/with_create_lock` async methods over `Arc<Mutex<rusqlite::Connection>>` | ~330 |
| `src/sinks/sqlite.rs` | `SqliteSink` struct; mode dispatch; HNSW warning once-per-process; canonical_cols; create_table (3 modes); write_document (two-table dance); delete_document (both tables); count_docs; query_top_k (vec0 MATCH JOIN) | ~480 |
| `src/sources/sqlite_table.rs` | `SqliteTableSource` — column-projection SELECT, JSON metadata, async iter | ~110 |
| `tests/parity-fixtures/dialect-sqlite.json` | Same input/output cases as `dialect-postgres.json` but for SQLite outputs | ~60 |
| `tests/dialect_sqlite_parity.rs` | Asserts byte-for-byte equality against the fixture | ~140 |
| `tests/backend_sqlite_conn.rs` | In-process integration: connect, table_exists, embedding_dim against `:memory:` and tempdir DBs | ~100 |
| `tests/sqlite_sink_create_table.rs` | All three modes; both tables exist; promote_metadata columns added | ~130 |
| `tests/sqlite_sink_two_table_dance.rs` | write_document twice; vec rows replaced not duplicated; delete_orphans on both | ~130 |
| `tests/sqlite_sink_query_top_k.rs` | query_top_k returns ordered (doc_id, seq_num, distance) tuples | ~70 |
| `tests/sqlite_sink_delete_document.rs` | delete_document removes from both tables; source_tag scope honored | ~90 |
| `tests/sqlite_sink_modes.rs` | append preflight (missing table, missing _vec, dim mismatch); overwrite foreign-tag refuse; HNSW warns once | ~140 |
| `tests/sqlite_table_source.rs` | Plant rows; iterate; assert metadata round-trip | ~80 |
| `tests/cross_language_sqlite_parity.rs` | Shell-out to `uv run python -c '...'`; Rust opens `.db`; query_top_k matches | ~120 |

**Modified files:**

| File | Change |
|---|---|
| `rust/chunkshop/Cargo.toml` | Add `rusqlite = { version = "0.32", features = ["bundled", "load_extension"] }`, `sqlite-vec = "0.1"` |
| `rust/chunkshop/src/backends/mod.rs` | `pub mod sqlite;` + `AnyBackend::Sqlite(SQLiteBackend)` + `load_backend` arm |
| `rust/chunkshop/src/sinks/mod.rs` | `pub mod sqlite;` + `AnySink::Sqlite(SqliteSink)` + 5 trait-impl arms + `load_sink` arm |
| `rust/chunkshop/src/sources/mod.rs` | `pub mod sqlite_table;` + `AnySource::SqliteTable(SqliteTableSource)` + `iter_documents` arm + `load_source` arm |
| `rust/chunkshop/src/config.rs` | `TargetConfig::Sqlite(SqliteTargetConfig)` + `SourceConfig::SqliteTable(SqliteTableSourceConfig)`; mirror Python's pydantic shape; ident validation |
| `rust/chunkshop/src/lib.rs` | Re-export `SQLiteBackend`, `SqliteSink`, `SqliteTableSource` |
| `docs/samples/sample-sqlite.yaml` | Files-source → sentence_aware → fastembed bge-small-en-v1.5-int8 → sqlite target |

---

## Phase A — Foundations (deps, config, dialect)

### Task 1: Add deps + sqlite-vec smoke test

**Files:**
- Modify: `rust/chunkshop/Cargo.toml`
- Create: `rust/chunkshop/tests/sqlite_vec_smoke.rs`

- [ ] **Step 1: Verify `sqlite-vec` and `rusqlite` versions on crates.io**

Run: `cd /home/yonk/yonk-tools/chunkshop-r3-sqlite/rust && cargo search sqlite-vec rusqlite --limit 5`

Note the latest `sqlite-vec` 0.x version and the latest `rusqlite` 0.x version. If `sqlite-vec` ≥ 0.2 exists, prefer it. If the API of `sqlite-vec` has shifted away from `sqlite_vec::sqlite3_vec_init`, follow the new shape — the rest of this plan uses the canonical 0.1 API. Commit only the dep choice that builds.

- [ ] **Step 2: Add deps to `Cargo.toml`**

Edit `rust/chunkshop/Cargo.toml`. After the existing `sqlx = ...` line, add:

```toml
rusqlite = { version = "0.32", features = ["bundled", "load_extension"] }
sqlite-vec = "0.1"
```

Run: `cargo build -p chunkshop-rs 2>&1 | tail -20`

Expected: `Compiling rusqlite ...`, `Compiling sqlite-vec ...`, `Finished` clean. If any non-obvious compile error, do NOT widen flags — stop and ask the user.

- [ ] **Step 3: Write a smoke test that proves sqlite-vec loads**

Create `rust/chunkshop/tests/sqlite_vec_smoke.rs`:

```rust
//! Smoke test: prove the `sqlite-vec` extension actually loads on this build.
//! If this fails, the rest of R3 is moot.

#[test]
fn sqlite_vec_loads_on_memory_connection() {
    let conn = rusqlite::Connection::open_in_memory().expect("open :memory:");

    // Enable + load extension. The exact symbol comes from the sqlite-vec crate.
    unsafe {
        conn.load_extension_enable().expect("enable load_extension");
        // Register sqlite-vec via auto_extension so subsequent CREATE VIRTUAL
        // TABLE statements see vec0. The sqlite-vec crate exposes
        // `sqlite3_vec_init` as a C-callable symbol.
        rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
            sqlite_vec::sqlite3_vec_init as *const (),
        )));
    }

    // Verify a vec0 virtual table can be created.
    conn.execute_batch(
        "CREATE VIRTUAL TABLE smoke_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[3])",
    )
    .expect("create vec0 table");

    // Insert + query a single vector.
    conn.execute(
        "INSERT INTO smoke_vec (id, embedding) VALUES ('a', ?)",
        rusqlite::params!["[1.0, 0.0, 0.0]"],
    )
    .expect("insert vector");

    let mut stmt = conn
        .prepare("SELECT id FROM smoke_vec WHERE embedding MATCH ? AND k = 1")
        .expect("prepare match");
    let rows: Vec<String> = stmt
        .query_map(rusqlite::params!["[1.0, 0.0, 0.0]"], |r| r.get::<_, String>(0))
        .expect("query")
        .map(|r| r.unwrap())
        .collect();
    assert_eq!(rows, vec!["a".to_string()]);
}
```

- [ ] **Step 4: Run the smoke test**

Run: `cargo test -p chunkshop-rs --test sqlite_vec_smoke -- --nocapture`

Expected: `test sqlite_vec_loads_on_memory_connection ... ok`. If it fails with a symbol-name complaint (`sqlite3_vec_init` not found), check the `sqlite-vec` crate's docs for the actual symbol — recent versions sometimes rename it. If it fails with `no such module: vec0`, the auto_extension didn't register — try the manual `conn.load_extension(path, None)` path with the bundled `.so`. Do not proceed past this task until the smoke test is green.

- [ ] **Step 5: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-r3-sqlite
git add rust/chunkshop/Cargo.toml rust/chunkshop/Cargo.lock rust/chunkshop/tests/sqlite_vec_smoke.rs
git commit -m "$(cat <<'EOF'
chore(deps): add rusqlite + sqlite-vec, prove vec0 loads

R3 foundation: rusqlite 0.32 bundled with load_extension, sqlite-vec 0.1
auto-registered via sqlite3_auto_extension. Smoke test creates a vec0
virtual table and runs a MATCH query on a :memory: connection. If this
test ever fails, the rest of R3 is moot — this is the gate.
EOF
)"
```

---

### Task 2: Add `SqliteTargetConfig` + `SqliteTableSourceConfig` to `config.rs`

**Files:**
- Modify: `rust/chunkshop/src/config.rs`

- [ ] **Step 1: Write the failing config-parse tests**

Append to `rust/chunkshop/src/config.rs` inside the existing `#[cfg(test)] mod tests` block:

```rust
    #[test]
    fn parses_sqlite_target_config() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.target {
            TargetConfig::Sqlite(t) => {
                assert_eq!(t.dsn_env, "SQLITE_PATH");
                assert_eq!(t.database_name, "ignored");
                assert_eq!(t.table, "chunks");
                assert_eq!(t.mode, "overwrite");
            }
            _ => panic!("expected Sqlite target"),
        }
    }

    #[test]
    fn rejects_sqlite_append_without_source_tag() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: append, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(err.contains("source_tag"), "expected source_tag mention, got: {err}");
    }

    #[test]
    fn parses_sqlite_table_source_config() {
        let yaml = r#"
cell_name: t
source:
  type: sqlite_table
  dsn_env: SQLITE_PATH
  database: ignored
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.source {
            SourceConfig::SqliteTable(s) => {
                assert_eq!(s.dsn_env, "SQLITE_PATH");
                assert_eq!(s.table, "docs");
                assert_eq!(s.id_column, "id");
            }
            _ => panic!("expected SqliteTable source"),
        }
    }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cargo test -p chunkshop-rs config:: -- --nocapture 2>&1 | tail -30`

Expected: 3 new tests fail with errors mentioning `unknown variant 'sqlite'` and `unknown variant 'sqlite_table'`.

- [ ] **Step 3: Add `SqliteTargetConfig` + `SqliteTableSourceConfig` and wire them into the unions**

Edit `rust/chunkshop/src/config.rs`.

Find the `TargetConfig` enum near line 703 and add the `Sqlite` variant:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    Sqlite(SqliteTargetConfig),
    // R2/R4 add: Mariadb, Clickhouse
}

impl TargetConfig {
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
            TargetConfig::Sqlite(t) => t.validate(),
        }
    }
}
```

Add the new struct (place after `PostgresTargetConfig` ends, near line 765):

```rust
/// SQLite target. Mirrors Python's `chunkshop.config.SqliteTarget`. Identifier
/// safety + per-field semantics match Postgres where applicable. `database` is
/// validated as a non-empty ident at config-load (loose parity) but ignored at
/// runtime — SQLite has no schema/database namespace concept.
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTargetConfig {
    /// Env var holding the path to the SQLite file (or `:memory:`).
    pub dsn_env: String,
    /// Mirrors PG's `database` field. Validated as ident; runtime-ignored.
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    #[serde(default)]
    pub overwrite: bool,
    /// `target.hnsw=true` is a no-op on SQLite (sqlite-vec is brute-force KNN).
    /// We accept the flag for YAML symmetry with PG; the sink emits a one-time
    /// warning on `create_table` when it's set.
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

impl SqliteTargetConfig {
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

Find the `SourceConfig` enum near line 234 and add the `SqliteTable` variant:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
    PgTable(PgTableSourceConfig),
    SqliteTable(SqliteTableSourceConfig),
    Http(HttpSourceConfig),
    S3(S3SourceConfig),
    Inline(InlineSourceConfig),
}
```

Add the new struct (place after `PgTableSourceConfig`, near line 293):

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTableSourceConfig {
    pub dsn_env: String,
    /// Validated as ident; runtime-ignored (SQLite has no schemas).
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}
```

Find the `load_config` function near line 834 and extend the per-target ident validation:

```rust
    match &cfg.target {
        TargetConfig::Postgres(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Sqlite(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
    }
```

And the per-source ident validation:

```rust
    if let SourceConfig::PgTable(p) = &cfg.source { /* ... existing block ... */ }
    if let SourceConfig::SqliteTable(s) = &cfg.source {
        validate_ident(&s.database_name, "source.database")?;
        validate_ident(&s.table, "source.table")?;
        validate_ident(&s.id_column, "source.id_column")?;
        validate_ident(&s.content_column, "source.content_column")?;
        if let Some(tc) = &s.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // where_clause intentionally NOT validated — see PgTableSourceConfig docstring.
    }
```

- [ ] **Step 4: Run the tests to verify they pass + R1 baseline still passes**

Run: `cargo test -p chunkshop-rs config:: 2>&1 | tail -20`

Expected: all 3 new tests pass; the existing 19 config tests still pass.

Run the full library tests too: `cargo test -p chunkshop-rs --lib 2>&1 | grep "test result"`

Expected: `80+ passed; 0 failed; 0 ignored`.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/config.rs
git commit -m "$(cat <<'EOF'
feat(config): add SqliteTargetConfig + SqliteTableSourceConfig

Mirrors Python's pydantic shapes. database_name validated as ident at
config-load (loose parity with PG) but runtime-ignored — SQLite has no
schema namespace. append-mode source_tag requirement enforced like PG.
EOF
)"
```

---

### Task 3: Wire `AnyBackend::Sqlite`, `AnySink::Sqlite`, `AnySource::SqliteTable` enum stubs

**Files:**
- Modify: `rust/chunkshop/src/backends/mod.rs`
- Modify: `rust/chunkshop/src/sinks/mod.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`
- Create: `rust/chunkshop/src/backends/sqlite.rs` (stub)
- Create: `rust/chunkshop/src/sinks/sqlite.rs` (stub)
- Create: `rust/chunkshop/src/sources/sqlite_table.rs` (stub)

This task lands compile-clean stubs so the next tasks can fill in real impls without compile churn. Each stub has just enough for the enums and factories to dispatch.

- [ ] **Step 1: Create stub `rust/chunkshop/src/backends/sqlite.rs`**

```rust
//! SQLite backend (placeholder — Tasks 4 + 6 fill this in).

use crate::backends::base::BackendDialect;

pub struct SQLiteBackend {
    pub(crate) dsn_env: String,
}

impl SQLiteBackend {
    pub fn new(dsn_env: String) -> Self {
        Self { dsn_env }
    }
}

impl BackendDialect for SQLiteBackend {
    const NAME: &'static str = "sqlite";
    const SUPPORTS_UPSERT: bool = true;

    // Placeholder bodies — Task 4 fills these in.
    fn quote_ident(&self, name: &str) -> String {
        format!("\"{}\"", name.replace('"', "\"\""))
    }
    fn fq_table(&self, _db: &str, table: &str) -> String { self.quote_ident(table) }
    fn vector_type_ddl(&self, dim: usize) -> String { format!("FLOAT[{dim}]") }
    fn json_type_ddl(&self) -> String { "TEXT".to_string() }
    fn tags_array_type_ddl(&self) -> String { "TEXT".to_string() }
    fn text_pk_type_ddl(&self) -> String { "TEXT".to_string() }
    fn timestamp_now_default_ddl(&self) -> String {
        "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()
    }
    fn vector_literal(&self, arr: &[f32]) -> String {
        let parts: Vec<String> = arr.iter().map(|x| format!("{x}")).collect();
        format!("[{}]", parts.join(", "))
    }
    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }
    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        format!("json_extract({col_expr},'$.{dotted_path}')")
    }
    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String {
        // Task 4 fills this in
        String::new()
    }
    fn create_database_sql(&self, _name: &str) -> String {
        "SELECT 1 -- chunkshop: SQLite has no database/schema concept".to_string()
    }
    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        format!("ALTER TABLE {fq} ADD COLUMN {} {type_ddl}", self.quote_ident(col))
    }
    fn drop_table_sql(&self, fq: &str) -> String { format!("DROP TABLE {fq}") }
    fn emit_chunks_table_ddl(
        &self, _fq: &str, _cols: &[crate::backends::base::ColSpec],
        _hnsw: bool, _dim: usize, _engine: Option<&str>,
    ) -> Vec<String> {
        // Task 4 fills this in
        Vec::new()
    }
}
```

- [ ] **Step 2: Create stub `rust/chunkshop/src/sinks/sqlite.rs`**

```rust
//! SQLite sink (placeholder — Tasks 7-12 fill this in).

use std::future::Future;
use anyhow::Result;
use crate::backends::sqlite::SQLiteBackend;
use crate::chunker::Chunk;
use crate::config::SqliteTargetConfig;
use crate::sinks::base::Sink;

pub struct SqliteSink {
    pub(crate) cfg: SqliteTargetConfig,
    pub(crate) backend: SQLiteBackend,
    pub(crate) embed_dim: usize,
}

impl SqliteSink {
    pub fn new(cfg: SqliteTargetConfig, backend: SQLiteBackend, embed_dim: usize) -> Self {
        Self { cfg, backend, embed_dim }
    }
}

impl Sink for SqliteSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow::anyhow!("SqliteSink::create_table not yet implemented")) }
    }
    fn write_document(
        &self, _doc_id: &str, _chunks: &[Chunk],
        _embeddings: &[Vec<f32>], _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow::anyhow!("SqliteSink::write_document not yet implemented")) }
    }
    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow::anyhow!("SqliteSink::delete_document not yet implemented")) }
    }
    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow::anyhow!("SqliteSink::count_docs not yet implemented")) }
    }
    fn query_top_k(
        &self, _query_vec: &[f32], _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { Err(anyhow::anyhow!("SqliteSink::query_top_k not yet implemented")) }
    }
}
```

- [ ] **Step 3: Create stub `rust/chunkshop/src/sources/sqlite_table.rs`**

```rust
//! SQLite source (placeholder — Task 14 fills this in).

use anyhow::Result;
use crate::config::SqliteTableSourceConfig;
use crate::sources::base::Document;

pub struct SqliteTableSource {
    pub(crate) cfg: SqliteTableSourceConfig,
}

impl SqliteTableSource {
    pub fn new(cfg: SqliteTableSourceConfig) -> Self { Self { cfg } }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        Err(anyhow::anyhow!("SqliteTableSource::iter_documents not yet implemented"))
    }
}
```

- [ ] **Step 4: Wire `backends/mod.rs`**

Replace `rust/chunkshop/src/backends/mod.rs` with:

```rust
//! Backend module — connection management + dialect helpers per DB engine.

use anyhow::Result;

use crate::config::TargetConfig;

pub mod base;
pub mod postgres;
pub mod sqlite;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;
pub use sqlite::SQLiteBackend;

pub enum AnyBackend {
    Postgres(PostgresBackend),
    Sqlite(SQLiteBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
        TargetConfig::Sqlite(t) => Ok(AnyBackend::Sqlite(SQLiteBackend::new(t.dsn_env.clone()))),
    }
}
```

- [ ] **Step 5: Wire `sinks/mod.rs`**

Replace the body of `rust/chunkshop/src/sinks/mod.rs`:

```rust
//! Sinks — chunkshop's per-backend data-model semantics layer.

use std::future::Future;

use anyhow::{anyhow, Result};

use crate::backends::AnyBackend;
use crate::chunker::Chunk;
use crate::config::TargetConfig;

pub mod base;
pub mod pg;
pub mod sqlite;

pub use base::Sink;
pub use pg::PgSink;
pub use sqlite::SqliteSink;

pub enum AnySink {
    Pg(PgSink),
    Sqlite(SqliteSink),
}

impl Sink for AnySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.create_table().await,
                AnySink::Sqlite(s) => s.create_table().await,
            }
        }
    }

    fn write_document(
        &self, doc_id: &str, chunks: &[Chunk],
        embeddings: &[Vec<f32>], tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
                AnySink::Sqlite(s) => s.write_document(doc_id, chunks, embeddings, tags_per_chunk).await,
            }
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.delete_document(doc_id).await,
                AnySink::Sqlite(s) => s.delete_document(doc_id).await,
            }
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.count_docs().await,
                AnySink::Sqlite(s) => s.count_docs().await,
            }
        }
    }

    fn query_top_k(
        &self, query_vec: &[f32], k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.query_top_k(query_vec, k).await,
                AnySink::Sqlite(s) => s.query_top_k(query_vec, k).await,
            }
        }
    }
}

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) => {
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim)))
        }
        (TargetConfig::Sqlite(t), AnyBackend::Sqlite(b)) => {
            Ok(AnySink::Sqlite(SqliteSink::new(t.clone(), b, dim)))
        }
        _ => Err(anyhow!("backend / target type mismatch — programming error in load_sink dispatch")),
    }
}
```

- [ ] **Step 6: Wire `sources/mod.rs`**

Edit `rust/chunkshop/src/sources/mod.rs`. Add `pub mod sqlite_table;` and `pub use sqlite_table::SqliteTableSource;` near the existing `pub mod files;` block. Add the variant + dispatch:

```rust
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    SqliteTable(SqliteTableSource),
    Http(HttpSource),
    S3(S3Source),
}

impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::SqliteTable(s) => s.iter_documents().await,
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
        SourceConfig::SqliteTable(c) => Ok(AnySource::SqliteTable(SqliteTableSource::new(c.clone()))),
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
```

- [ ] **Step 7: Verify the workspace still builds + tests still pass**

Run: `cargo build -p chunkshop-rs 2>&1 | tail -10`

Expected: `Finished` clean. Some warnings about unused stub fields are OK at this stage.

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3`

Expected: existing tests still pass; sqlite_vec_smoke still passes; config tests pass. No new failures.

- [ ] **Step 8: Commit**

```bash
git add rust/chunkshop/src/backends/sqlite.rs \
        rust/chunkshop/src/backends/mod.rs \
        rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/src/sinks/mod.rs \
        rust/chunkshop/src/sources/sqlite_table.rs \
        rust/chunkshop/src/sources/mod.rs
git commit -m "$(cat <<'EOF'
feat(backends,sinks,sources): wire Sqlite enum variants + factories

Compile-clean stubs for SQLiteBackend / SqliteSink / SqliteTableSource.
load_backend / load_sink / load_source now dispatch SQLite variants.
Stub method bodies return 'not yet implemented' errors; subsequent
tasks fill in real behavior.
EOF
)"
```

---

### Task 4: Implement `SQLiteBackend` `BackendDialect` for real

**Files:**
- Modify: `rust/chunkshop/src/backends/sqlite.rs`

This task replaces the placeholder bodies from Task 3 with the full Python-parity implementations. Reference: `python/src/chunkshop/backends/sqlite.py` lines 18-138.

- [ ] **Step 1: Write the failing unit tests at the bottom of `backends/sqlite.rs`**

Append to `rust/chunkshop/src/backends/sqlite.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::backends::base::ColSpec;

    fn backend() -> SQLiteBackend { SQLiteBackend::new("UNUSED".to_string()) }

    #[test]
    fn quote_ident_wraps_in_double_quotes() {
        assert_eq!(backend().quote_ident("my_table"), "\"my_table\"");
    }

    #[test]
    fn quote_ident_doubles_embedded_quote() {
        assert_eq!(backend().quote_ident("a\"b"), "\"a\"\"b\"");
    }

    #[test]
    fn fq_table_returns_table_only_no_schema() {
        // SQLite has no schema concept — db arg is dropped.
        assert_eq!(backend().fq_table("ignored", "my_table"), "\"my_table\"");
    }

    #[test]
    fn vector_type_ddl_uses_float_brackets() {
        assert_eq!(backend().vector_type_ddl(384), "FLOAT[384]");
    }

    #[test]
    fn json_type_is_text() { assert_eq!(backend().json_type_ddl(), "TEXT"); }

    #[test]
    fn tags_array_type_is_text() { assert_eq!(backend().tags_array_type_ddl(), "TEXT"); }

    #[test]
    fn text_pk_type_is_text() { assert_eq!(backend().text_pk_type_ddl(), "TEXT"); }

    #[test]
    fn timestamp_default_is_current_timestamp() {
        assert_eq!(
            backend().timestamp_now_default_ddl(),
            "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        );
    }

    #[test]
    fn vector_literal_matches_python_json_array() {
        // Python: json.dumps([float(x) for x in arr]) — comma+space separator.
        let v = vec![0.1_f32, 0.2_f32, -0.3_f32];
        let lit = backend().vector_literal(&v);
        // Re-parse to avoid float-format brittleness; assert structure.
        let parsed: serde_json::Value = serde_json::from_str(&lit).unwrap();
        let arr = parsed.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert!((arr[0].as_f64().unwrap() - 0.1).abs() < 1e-6);
        assert!((arr[2].as_f64().unwrap() - (-0.3)).abs() < 1e-6);
    }

    #[test]
    fn json_path_sql_uses_json_extract_with_dollar_dot() {
        assert_eq!(
            backend().json_path_sql("metadata", "a.b.c"),
            "json_extract(metadata,'$.a.b.c')"
        );
    }

    #[test]
    fn upsert_clause_do_nothing_when_no_updates() {
        assert_eq!(
            backend().upsert_clause(&["id"], &[]),
            "ON CONFLICT (\"id\") DO NOTHING"
        );
    }

    #[test]
    fn upsert_clause_excluded_form() {
        assert_eq!(
            backend().upsert_clause(&["id"], &["content", "metadata"]),
            "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = excluded.\"content\", \
             \"metadata\" = excluded.\"metadata\""
        );
    }

    #[test]
    fn create_database_sql_is_noop_select() {
        assert_eq!(
            backend().create_database_sql("ignored"),
            "SELECT 1 -- chunkshop: SQLite has no database/schema concept"
        );
    }

    #[test]
    fn add_column_lacks_if_not_exists() {
        // SQLite ALTER TABLE has no IF NOT EXISTS clause; the sink relies on
        // catching duplicate-column errors for idempotency.
        assert_eq!(
            backend().add_column_if_not_exists_sql("\"chunks\"", "source", "TEXT"),
            "ALTER TABLE \"chunks\" ADD COLUMN \"source\" TEXT"
        );
    }

    fn canonical_cols(dim: usize) -> Vec<ColSpec> {
        vec![
            ColSpec { name: "id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: true },
            ColSpec { name: "doc_id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "seq_num", type_ddl: "INTEGER".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "embedding", type_ddl: format!("FLOAT[{dim}]"), nullable: false, default: None, is_primary_key: false },
        ]
    }

    #[test]
    fn emit_chunks_table_ddl_returns_three_statements() {
        let stmts = backend().emit_chunks_table_ddl(
            "\"chunks\"", &canonical_cols(384), false, 384, None,
        );
        assert_eq!(stmts.len(), 3, "main table + index + vec0 virtual table");
        assert!(stmts[0].starts_with("CREATE TABLE IF NOT EXISTS \"chunks\""));
        assert!(stmts[0].contains("\"id\" TEXT NOT NULL"));
        assert!(stmts[0].contains("PRIMARY KEY (\"id\")"));
        // embedding column is split out — must NOT appear in the main DDL.
        assert!(!stmts[0].contains("\"embedding\" FLOAT"));
        assert!(stmts[1].contains("CREATE INDEX IF NOT EXISTS \"chunks_doc_seq_idx\""));
        assert!(stmts[2].starts_with("CREATE VIRTUAL TABLE IF NOT EXISTS \"chunks_vec\""));
        assert!(stmts[2].contains("USING vec0("));
        assert!(stmts[2].contains("FLOAT[384]"));
    }

    #[test]
    fn emit_chunks_table_ddl_hnsw_does_not_change_output() {
        // SQLite's HNSW is a no-op at the DDL level — same statements as without.
        let no = backend().emit_chunks_table_ddl("\"c\"", &canonical_cols(8), false, 8, None);
        let yes = backend().emit_chunks_table_ddl("\"c\"", &canonical_cols(8), true, 8, None);
        assert_eq!(no, yes);
    }
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cargo test -p chunkshop-rs --lib backends::sqlite:: 2>&1 | tail -25`

Expected: most fail (placeholder upsert_clause returns empty string; emit_chunks_table_ddl returns empty Vec; vector_literal uses bare `{x}` format vs Python's float JSON).

- [ ] **Step 3: Replace the body of `backends/sqlite.rs` with the real impl**

Reference: `python/src/chunkshop/backends/sqlite.py` lines 44-138. Full Rust file:

```rust
//! SQLite backend (with sqlite-vec extension for vector storage).
//!
//! SQLite has no schema/database namespace concept — chunkshop's YAML `database`
//! field is required by config (loose parity) but ignored at runtime. The DSN
//! env var holds the file path or `:memory:`. Mirrors
//! `python/src/chunkshop/backends/sqlite.py`.
//!
//! `BackendDialect` is impl'd here. Connection-management methods (connect,
//! table_exists, embedding_dim, with_create_lock) live as inherent async methods
//! on `SQLiteBackend` — see Task 6. They do NOT impl the PG-shaped `BackendConn`
//! trait (R3 Mission Brief, R3-SC-001).

use crate::backends::base::{BackendDialect, ColSpec};

pub struct SQLiteBackend {
    pub(crate) dsn_env: String,
    // Connection state added in Task 6.
}

impl SQLiteBackend {
    pub fn new(dsn_env: String) -> Self {
        Self { dsn_env }
    }
}

impl BackendDialect for SQLiteBackend {
    const NAME: &'static str = "sqlite";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        format!("\"{}\"", name.replace('"', "\"\""))
    }

    fn fq_table(&self, _db: &str, table: &str) -> String {
        // No schemas in SQLite. Mirror Python: `del db; return self.quote_ident(table)`.
        self.quote_ident(table)
    }

    fn vector_type_ddl(&self, dim: usize) -> String {
        format!("FLOAT[{dim}]")
    }
    fn json_type_ddl(&self) -> String { "TEXT".to_string() }
    fn tags_array_type_ddl(&self) -> String { "TEXT".to_string() }
    fn text_pk_type_ddl(&self) -> String { "TEXT".to_string() }
    fn timestamp_now_default_ddl(&self) -> String {
        "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP".to_string()
    }

    fn vector_literal(&self, arr: &[f32]) -> String {
        // Python: json.dumps([float(x) for x in arr]) — produces a JSON array
        // string like "[0.1, 0.2, -0.3]" (comma+space separator). Match that
        // shape via serde_json so cross-language byte parity holds.
        let v: Vec<f64> = arr.iter().map(|x| *x as f64).collect();
        serde_json::to_string(&v).unwrap_or_else(|_| "[]".to_string())
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        format!("json_extract({col_expr},'$.{dotted_path}')")
    }

    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String {
        let keys: Vec<String> = key_cols.iter().map(|c| self.quote_ident(c)).collect();
        let keys_sql = keys.join(", ");
        if update_cols.is_empty() {
            return format!("ON CONFLICT ({keys_sql}) DO NOTHING");
        }
        let sets: Vec<String> = update_cols
            .iter()
            .map(|c| format!("{q} = excluded.{q}", q = self.quote_ident(c)))
            .collect();
        format!("ON CONFLICT ({keys_sql}) DO UPDATE SET {}", sets.join(", "))
    }

    fn create_database_sql(&self, _name: &str) -> String {
        "SELECT 1 -- chunkshop: SQLite has no database/schema concept".to_string()
    }

    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        // SQLite has no IF NOT EXISTS on ALTER TABLE; the sink catches the
        // duplicate-column error for idempotency.
        format!("ALTER TABLE {fq} ADD COLUMN {} {type_ddl}", self.quote_ident(col))
    }

    fn drop_table_sql(&self, fq: &str) -> String {
        format!("DROP TABLE {fq}")
    }

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        _hnsw: bool,
        dim: usize,
        _engine: Option<&str>,
    ) -> Vec<String> {
        // Python parity: split the embedding column out into a vec0 virtual
        // table joined by id. The main table holds everything else.
        let main_cols: Vec<&ColSpec> = cols.iter().filter(|c| c.name != "embedding").collect();

        let mut col_lines: Vec<String> = Vec::with_capacity(main_cols.len());
        let mut pk_cols: Vec<&str> = Vec::new();
        for c in &main_cols {
            let mut line = format!("  {} {}", self.quote_ident(c.name), c.type_ddl);
            if let Some(default) = c.default {
                line.push_str(&format!(" DEFAULT {default}"));
            }
            if !c.nullable {
                line.push_str(" NOT NULL");
            }
            col_lines.push(line);
            if c.is_primary_key {
                pk_cols.push(c.name);
            }
        }
        let mut body = col_lines.join(",\n");
        if !pk_cols.is_empty() {
            let pk: Vec<String> = pk_cols.iter().map(|c| self.quote_ident(c)).collect();
            body.push_str(&format!(",\n  PRIMARY KEY ({})", pk.join(", ")));
        }
        let create_main = format!("CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n)");

        // Strip outer quotes for index/vec table naming: "chunks" → chunks.
        let bare = fq.trim_matches('"').to_string();

        let create_idx = format!(
            "CREATE INDEX IF NOT EXISTS {} ON {fq} (\"doc_id\", \"seq_num\")",
            self.quote_ident(&format!("{bare}_doc_seq_idx"))
        );

        let vec_fq = self.quote_ident(&format!("{bare}_vec"));
        let create_vec = format!(
            "CREATE VIRTUAL TABLE IF NOT EXISTS {vec_fq} USING vec0(\
             id TEXT PRIMARY KEY, embedding FLOAT[{dim}])"
        );

        vec![create_main, create_idx, create_vec]
    }
}
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cargo test -p chunkshop-rs --lib backends::sqlite:: 2>&1 | tail -25`

Expected: all 16 dialect unit tests pass.

Then run the full suite to verify no regression: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3`

Expected: existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/sqlite.rs
git commit -m "$(cat <<'EOF'
feat(backends): SQLiteBackend BackendDialect impl with parity to Python

Mirrors python/src/chunkshop/backends/sqlite.py:
- fq_table drops the database arg (SQLite has no schemas)
- emit_chunks_table_ddl returns 3 statements: main CREATE TABLE,
  doc_seq index, and CREATE VIRTUAL TABLE ... USING vec0(...)
- embedding column is split out — never in the main table
- upsert_clause uses lowercase 'excluded' (SQLite syntax) vs PG's EXCLUDED
- vector_literal uses serde_json to match Python's json.dumps shape
EOF
)"
```

---

### Task 5: Author `dialect-sqlite.json` parity fixture + `dialect_sqlite_parity.rs`

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/dialect-sqlite.json`
- Create: `rust/chunkshop/tests/dialect_sqlite_parity.rs`

This locks the dialect-output shape against a canonical fixture (R3-SC-008). Python publishes a matching fixture in a follow-up commit (out of scope here).

- [ ] **Step 1: Author the fixture**

Create `rust/chunkshop/tests/parity-fixtures/dialect-sqlite.json`:

```json
{
  "backend": "sqlite",
  "quote_ident": [
    {"in": "my_table", "out": "\"my_table\""},
    {"in": "abc", "out": "\"abc\""},
    {"in": "with_underscore_123", "out": "\"with_underscore_123\""}
  ],
  "fq_table": [
    {"in": ["public", "my_table"], "out": "\"my_table\""},
    {"in": ["chunkshop", "test_chunks"], "out": "\"test_chunks\""},
    {"in": ["ignored_db", "chunks"], "out": "\"chunks\""}
  ],
  "vector_type_ddl": [
    {"in": 384, "out": "FLOAT[384]"},
    {"in": 1024, "out": "FLOAT[1024]"},
    {"in": 1, "out": "FLOAT[1]"}
  ],
  "json_path_sql": [
    {"in": ["metadata", "a"], "out": "json_extract(metadata,'$.a')"},
    {"in": ["metadata", "a.b"], "out": "json_extract(metadata,'$.a.b')"},
    {"in": ["metadata", "a.b.c"], "out": "json_extract(metadata,'$.a.b.c')"}
  ],
  "upsert_clause": [
    {"in": {"keys": ["id"], "updates": []}, "out": "ON CONFLICT (\"id\") DO NOTHING"},
    {"in": {"keys": ["id"], "updates": ["content"]}, "out": "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = excluded.\"content\""},
    {"in": {"keys": ["id"], "updates": ["a", "b"]}, "out": "ON CONFLICT (\"id\") DO UPDATE SET \"a\" = excluded.\"a\", \"b\" = excluded.\"b\""},
    {"in": {"keys": ["a", "b"], "updates": ["c"]}, "out": "ON CONFLICT (\"a\", \"b\") DO UPDATE SET \"c\" = excluded.\"c\""}
  ],
  "create_database_sql": [
    {"in": "chunkshop", "out": "SELECT 1 -- chunkshop: SQLite has no database/schema concept"},
    {"in": "ignored", "out": "SELECT 1 -- chunkshop: SQLite has no database/schema concept"}
  ],
  "drop_table_sql": [
    {"in": "\"chunks\"", "out": "DROP TABLE \"chunks\""}
  ],
  "add_column_if_not_exists_sql": [
    {"in": ["\"chunks\"", "source", "TEXT"], "out": "ALTER TABLE \"chunks\" ADD COLUMN \"source\" TEXT"}
  ]
}
```

- [ ] **Step 2: Author the parity test**

Create `rust/chunkshop/tests/dialect_sqlite_parity.rs`:

```rust
//! Cross-language dialect parity test for SQLite. Both Python and Rust assert
//! their BackendDialect impls produce the byte-for-byte outputs in the fixture.
//! Mirrors tests/dialect_postgres_parity.rs.

use chunkshop::backends::{BackendDialect, SQLiteBackend};
use serde_json::Value;

const FIXTURE_PATH: &str = "tests/parity-fixtures/dialect-sqlite.json";

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read parity fixture");
    serde_json::from_str(&raw).expect("parse parity fixture")
}

fn backend() -> SQLiteBackend {
    SQLiteBackend::new("UNUSED_FOR_DIALECT_PARITY".to_string())
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
        assert_eq!(b.upsert_clause(&keys, &updates), expected,
                   "upsert_clause(keys={keys:?}, updates={updates:?})");
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
```

- [ ] **Step 3: Run the parity test**

Run: `cargo test -p chunkshop-rs --test dialect_sqlite_parity -- --nocapture 2>&1 | tail -25`

Expected: all 8 parity tests pass. If any fail, the dialect impl from Task 4 has byte-level drift from Python — fix in `backends/sqlite.rs`, not the fixture.

- [ ] **Step 4: Run the full suite to confirm no regression**

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -5`

Expected: every test result shows 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/tests/parity-fixtures/dialect-sqlite.json \
        rust/chunkshop/tests/dialect_sqlite_parity.rs
git commit -m "$(cat <<'EOF'
test(parity): cross-language BackendDialect fixture for SQLite

Mirrors tests/parity-fixtures/dialect-postgres.json. 8 case groups
covering quote_ident, fq_table, vector_type_ddl, json_path_sql,
upsert_clause, create_database_sql, drop_table_sql,
add_column_if_not_exists_sql. Asserts byte-for-byte output equality
against SQLiteBackend. Python publishes the matching parity test in
a follow-up commit.
EOF
)"
```

---

### Task 6: ⛔ DC-001 — Drift checkpoint

This is a hard gate. Read the mission brief from disk and verify SC alignment before proceeding.

- [ ] **Step 1: Re-read the mission brief**

Read: `/home/yonk/yonk-tools/chunkshop-r3-sqlite/skill-output/mission-brief/Mission-Brief-r3-rust-sqlite.md` cover to cover.

- [ ] **Step 2: Run the three drift questions**

Answer each in writing in the implementer's workspace (or a comment on the work item):

1. **Am I still solving the stated Purpose?** — Building a SQLite backend with behavioral parity to Python. Yes / No.
2. **Does my current work map to at least one Success Criterion?**
   - Tasks 1–3 → SC-001 (BackendDialect plus inherent connection methods seam) and SC-009 (no regressions in baseline tests)
   - Task 4 → SC-001 (BackendDialect fully impl'd)
   - Task 5 → SC-008 (dialect parity fixture)
3. **Am I doing anything listed in Out of Scope?**
   - Have I edited `backends/base.rs` or `backends/postgres.rs`? **MUST be no.**
   - Have I edited any Python file? **MUST be no.**
   - Have I touched `../chunkshop-r2-mariadb/` or `../chunkshop-r4-clickhouse/`? **MUST be no.**

- [ ] **Step 3: Verify with `git diff`**

Run from the worktree root:

```bash
git diff --stat experimental/v4-modular-backends..HEAD -- \
    rust/chunkshop/src/backends/base.rs \
    rust/chunkshop/src/backends/postgres.rs \
    rust/chunkshop/src/sinks/pg.rs \
    rust/chunkshop/src/sources/pg_table.rs
```

Expected output: empty (no lines). If any of these files show changes, that is drift — stop and reassess.

```bash
git diff --stat experimental/v4-modular-backends..HEAD -- python/
```

Expected: empty. If non-empty, drift.

- [ ] **Step 4: Confirm SC-001, SC-008, SC-009 satisfied**

Run: `cargo test -p chunkshop-rs --test dialect_sqlite_parity 2>&1 | grep "test result"`

Expected: `test result: ok. 8 passed`.

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | awk '{print $4}' | paste -sd+ | bc`

Expected: total ≥ 126 (R1 baseline) + 16 (Task 4 unit tests) + 8 (parity) + 3 (config) + 1 (smoke) ≈ ≥ 154.

If all answers are correct and tests are green, proceed to Task 7. If any drift detected, file an issue in chat before continuing.

---

## Phase B — Backend connection plumbing

### Task 7: Implement `SQLiteBackend` connection methods

**Files:**
- Modify: `rust/chunkshop/src/backends/sqlite.rs`
- Create: `rust/chunkshop/tests/backend_sqlite_conn.rs`

Add the inherent async methods that the sink and source will call. Reference: `python/src/chunkshop/backends/sqlite.py` lines 27-46 (connect), 140-163 (table_exists, embedding_dim, with_create_lock).

- [ ] **Step 1: Write the failing integration tests**

Create `rust/chunkshop/tests/backend_sqlite_conn.rs`:

```rust
//! Integration tests for SQLiteBackend's connection methods.

use chunkshop::backends::SQLiteBackend;
use std::sync::Arc;
use tempfile::tempdir;

fn unique_env(name: &str) -> String { format!("CHUNKSHOP_R3_TEST_{name}_{}", std::process::id()) }

#[tokio::test]
async fn connect_opens_writable_db_with_wal() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("conn.db");
    let env = unique_env("connect");
    std::env::set_var(&env, path.to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let conn = b.connect().await.expect("connect");
    let g = conn.lock().await;
    let mode: String = g
        .query_row("PRAGMA journal_mode", [], |r| r.get(0))
        .expect("query journal_mode");
    assert_eq!(mode.to_lowercase(), "wal");
}

#[tokio::test]
async fn table_exists_distinguishes_present_absent() {
    let env = unique_env("texists");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch("CREATE TABLE present (x INT)").unwrap();
    }
    assert!(b.table_exists(&conn, "ignored", "present").await.unwrap());
    assert!(!b.table_exists(&conn, "ignored", "missing").await.unwrap());
}

#[tokio::test]
async fn table_exists_finds_virtual_tables() {
    let env = unique_env("vexists");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch("CREATE VIRTUAL TABLE v USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[4])").unwrap();
    }
    assert!(b.table_exists(&conn, "ignored", "v").await.unwrap());
}

#[tokio::test]
async fn embedding_dim_reads_dim_from_vec_partner() {
    let env = unique_env("dim");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    {
        let g = conn.lock().await;
        g.execute_batch(
            "CREATE TABLE chunks (id TEXT PRIMARY KEY); \
             CREATE VIRTUAL TABLE chunks_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[768])",
        ).unwrap();
    }
    let d = b.embedding_dim(&conn, "ignored", "chunks").await.unwrap();
    assert_eq!(d, Some(768));
    let d = b.embedding_dim(&conn, "ignored", "missing").await.unwrap();
    assert_eq!(d, None);
}

#[tokio::test]
async fn with_create_lock_is_a_noop_returning_ok() {
    let env = unique_env("lock");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    b.with_create_lock(&conn, "anykey").await.expect("noop");
    // Idempotent
    b.with_create_lock(&conn, "anykey").await.expect("noop");
}

#[tokio::test]
async fn arc_mutex_connection_is_shareable_across_tasks() {
    // Sanity check: Arc<Mutex<...>> wrapping is correct for tokio.
    let env = unique_env("share");
    std::env::set_var(&env, ":memory:");
    let b = SQLiteBackend::new(env);
    let conn = b.connect().await.unwrap();
    let conn2: Arc<_> = conn.clone();
    let h = tokio::spawn(async move {
        let g = conn2.lock().await;
        g.execute_batch("CREATE TABLE t (x INT)").unwrap();
    });
    h.await.unwrap();
    let g = conn.lock().await;
    let n: i64 = g.query_row("SELECT COUNT(*) FROM sqlite_master WHERE name='t'", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 1);
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cargo test -p chunkshop-rs --test backend_sqlite_conn 2>&1 | tail -15`

Expected: compile errors — `connect`, `table_exists`, `embedding_dim`, `with_create_lock` don't exist on `SQLiteBackend` yet.

- [ ] **Step 3: Implement the connection methods**

Add to `rust/chunkshop/src/backends/sqlite.rs`. First, register the sqlite-vec extension once per process via a `OnceLock`. Then add the inherent impl block. Append after the existing `impl BackendDialect for SQLiteBackend`:

```rust
use std::sync::{Arc, OnceLock};

use anyhow::{Context, Result};
use rusqlite::Connection;
use tokio::sync::Mutex;
use tokio::task::spawn_blocking;

/// Registers `sqlite_vec::sqlite3_vec_init` as an auto-extension exactly once
/// per process. After this is called, every new `rusqlite::Connection` opened
/// via `Connection::open(...)` automatically loads sqlite-vec.
fn register_sqlite_vec_once() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        // SAFETY: sqlite-vec's init function is C-callable; the cast matches
        // the expected sqlite3_auto_extension signature. This is the
        // documented integration pattern.
        unsafe {
            let _ = rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
                sqlite_vec::sqlite3_vec_init as *const (),
            )));
        }
    });
}

/// `Arc<Mutex<rusqlite::Connection>>` is the canonical shared-connection shape
/// used by the sink and source. `rusqlite::Connection` is `!Sync` so we wrap it
/// in `tokio::sync::Mutex` rather than `std::sync::Mutex` (we hold across
/// `.await` points in some test scenarios).
pub type SqliteConn = Arc<Mutex<Connection>>;

impl SQLiteBackend {
    /// Open a connection to the configured DB. Reads the path from the env var
    /// at the moment of the call (mirrors Python). Sets WAL on best-effort.
    /// Idempotent at the auto-extension level — sqlite-vec is registered once
    /// per process via `OnceLock`.
    pub async fn connect(&self) -> Result<SqliteConn> {
        let dsn_env = self.dsn_env.clone();
        spawn_blocking(move || -> Result<SqliteConn> {
            register_sqlite_vec_once();
            let path = std::env::var(&dsn_env)
                .with_context(|| format!("DSN env var {dsn_env} not set"))?;
            let conn = if path == ":memory:" {
                Connection::open_in_memory().context("open :memory:")?
            } else {
                Connection::open(&path).with_context(|| format!("opening {path}"))?
            };
            // Best-effort WAL — same as Python (`except sqlite3.DatabaseError: pass`).
            let _ = conn.pragma_update(None, "journal_mode", &"WAL");
            Ok(Arc::new(Mutex::new(conn)))
        })
        .await
        .context("spawn_blocking connect")?
    }

    /// Mirrors Python's `table_exists` — checks `sqlite_master` for table or
    /// virtual table by name. The `db` argument is dropped (no schemas).
    pub async fn table_exists(&self, conn: &SqliteConn, _db: &str, table: &str) -> Result<bool> {
        let conn = conn.clone();
        let table = table.to_string();
        spawn_blocking(move || -> Result<bool> {
            let g = conn.blocking_lock();
            let r: Option<i32> = g
                .query_row(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
                    rusqlite::params![table],
                    |row| row.get(0),
                )
                .ok();
            Ok(r.is_some())
        })
        .await
        .context("spawn_blocking table_exists")?
    }

    /// Read the FLOAT[N] dim from the vec0 partner table's CREATE statement
    /// in `sqlite_master`. Returns None when the partner table doesn't exist
    /// or doesn't have a FLOAT[N] column.
    pub async fn embedding_dim(
        &self, conn: &SqliteConn, _db: &str, table: &str,
    ) -> Result<Option<usize>> {
        let conn = conn.clone();
        let vec_table = format!("{table}_vec");
        spawn_blocking(move || -> Result<Option<usize>> {
            let g = conn.blocking_lock();
            let sql: Option<String> = g
                .query_row(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    rusqlite::params![vec_table],
                    |row| row.get(0),
                )
                .ok();
            let Some(sql) = sql else { return Ok(None) };
            let re = regex::Regex::new(r"(?i)FLOAT\[(\d+)\]").unwrap();
            Ok(re.captures(&sql)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok()))
        })
        .await
        .context("spawn_blocking embedding_dim")?
    }

    /// SQLite has no advisory-lock primitive. Mirror Python's no-op.
    pub async fn with_create_lock(&self, _conn: &SqliteConn, _key: &str) -> Result<()> {
        Ok(())
    }
}
```

Note: this uses `regex` (already a dep) and `tokio` (already a dep). `rusqlite::params!` macro is the standard binding form. `blocking_lock` is from tokio — it can be called inside `spawn_blocking` because we're off the async runtime.

- [ ] **Step 4: Run the integration tests**

Run: `cargo test -p chunkshop-rs --test backend_sqlite_conn 2>&1 | tail -15`

Expected: 6 tests pass.

Then full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: no failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/sqlite.rs \
        rust/chunkshop/tests/backend_sqlite_conn.rs
git commit -m "$(cat <<'EOF'
feat(backends): SQLiteBackend connection methods (connect, table_exists,
embedding_dim, with_create_lock)

Inherent async methods on SQLiteBackend, NOT trait impls. sqlite-vec
registered as a process-global auto-extension via OnceLock so every
Connection::open auto-loads it. Connection state is Arc<Mutex<...>>
because rusqlite::Connection is !Sync. Blocking calls wrapped in
tokio::task::spawn_blocking. WAL set best-effort on connect.
EOF
)"
```

---

## Phase C — Sink

### Task 8: `SqliteSink::create_table` — overwrite mode + HNSW warning

**Files:**
- Modify: `rust/chunkshop/src/sinks/sqlite.rs`
- Create: `rust/chunkshop/tests/sqlite_sink_create_table.rs`

Reference: `python/src/chunkshop/sinks/sqlite.py` lines 32-138.

- [ ] **Step 1: Write the failing tests**

Create `rust/chunkshop/tests/sqlite_sink_create_table.rs`:

```rust
//! Integration tests: SqliteSink::create_table per mode. Asserts that BOTH
//! the chunks table AND the {table}_vec virtual table are created (R3-SC-002).

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::SqliteSink;
use chunkshop::sinks::Sink;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: mode.into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    }
}

async fn assert_both_tables_exist(b: &SQLiteBackend) {
    let conn = b.connect().await.unwrap();
    assert!(b.table_exists(&conn, "ignored", "chunks").await.unwrap(), "chunks");
    assert!(b.table_exists(&conn, "ignored", "chunks_vec").await.unwrap(), "chunks_vec");
}

#[tokio::test]
async fn overwrite_creates_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_OWT_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("ow.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite"), b, 4);
    sink.create_table().await.expect("create_table");
    let b2 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b2).await;
}

#[tokio::test]
async fn overwrite_drops_existing_table_and_recreates() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DROP_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("d.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite"), b, 4);
    sink.create_table().await.expect("first");
    // Re-create — should not error, drop+recreate is the contract.
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "overwrite"), b2, 4);
    sink2.create_table().await.expect("second");
    let b3 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b3).await;
}
```

- [ ] **Step 2: Run them — they fail**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_create_table 2>&1 | tail -10`

Expected: failures with "not yet implemented" message from the sink stub.

- [ ] **Step 3: Implement create_table for overwrite mode + HNSW warning**

Replace the body of `rust/chunkshop/src/sinks/sqlite.rs` with:

```rust
//! SQLite sink — chunks-table writer with two-table layout (chunks + chunks_vec).
//!
//! Mirrors `python/src/chunkshop/sinks/sqlite.py`. The embedding column lives
//! in a `vec0` virtual table joined on `id`. The Sink owns the two-table
//! dance: every `write_document` writes both atomically; every
//! `delete_orphans` deletes from both. `vec0` virtual tables refuse UPSERT
//! and INSERT OR REPLACE — the working pattern is DELETE-by-id then INSERT.

use std::collections::BTreeSet;
use std::future::Future;
use std::sync::OnceLock;

use anyhow::{anyhow, Context, Result};

use crate::backends::base::{BackendDialect, ColSpec};
use crate::backends::sqlite::{SQLiteBackend, SqliteConn};
use crate::chunker::Chunk;
use crate::config::{PromoteColumn, SqliteTargetConfig};
use crate::sinks::base::Sink;

pub struct SqliteSink {
    pub(crate) cfg: SqliteTargetConfig,
    pub(crate) backend: SQLiteBackend,
    pub(crate) embed_dim: usize,
}

/// Process-global "have we warned about hnsw=true on SQLite yet?" flag.
/// Mirrors Python's `_HNSW_WARNED` set keyed on PID — one warning per process.
static HNSW_WARNED_ONCE: OnceLock<()> = OnceLock::new();

fn jsonb_path_get<'a>(meta: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        let obj = cur.as_object()?;
        cur = obj.get(seg)?;
    }
    Some(cur)
}

/// Map PG type names to SQLite equivalents for promote_metadata columns.
/// Mirrors Python's `_SQLITE_TYPE` dict in sinks/sqlite.py.
fn pg_type_to_sqlite(pg_type: &str) -> &str {
    match pg_type {
        "text" | "text[]" | "jsonb" | "timestamptz" | "date" => "TEXT",
        "int" | "bigint" | "boolean" => "INTEGER",
        other => other,
    }
}

/// Canonical chunks-table column list INCLUDING embedding — emit_chunks_table_ddl
/// splits the embedding column out into the vec0 partner table.
fn canonical_cols(dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "INTEGER".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "TEXT".into(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: "TEXT".into(), nullable: false, default: Some("'[]'"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: "TEXT".into(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: format!("FLOAT[{dim}]"), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "TEXT".into(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "TEXT".into(), nullable: false, default: Some("CURRENT_TIMESTAMP"), is_primary_key: false },
    ]
}

impl SqliteSink {
    pub fn new(cfg: SqliteTargetConfig, backend: SQLiteBackend, embed_dim: usize) -> Self {
        if cfg.hnsw {
            // Warn once per process. Subsequent SqliteSink instances built with
            // hnsw=true do NOT re-warn.
            if HNSW_WARNED_ONCE.set(()).is_ok() {
                tracing::warn!(
                    "target.hnsw=true on SQLite is a no-op — sqlite-vec uses brute-force KNN. \
                     Querying with `embedding MATCH '[...]' AND k = N` works without an index."
                );
            }
        }
        Self { cfg, backend, embed_dim }
    }

    fn fq_main(&self) -> String { self.backend.fq_table(&self.cfg.database_name, &self.cfg.table) }
    fn fq_vec(&self) -> String {
        let vec_table = format!("{}_vec", self.cfg.table);
        self.backend.fq_table(&self.cfg.database_name, &vec_table)
    }

    /// Create + run all DDL statements (main table, doc_seq index, vec0 virtual
    /// table) PLUS any promote_metadata ALTER TABLE statements on the main
    /// table. Idempotent — uses CREATE TABLE IF NOT EXISTS / CREATE VIRTUAL
    /// TABLE IF NOT EXISTS / catches duplicate-column errors.
    fn create_base_ddl(&self, conn: &rusqlite::Connection) -> Result<()> {
        for stmt in self.backend.emit_chunks_table_ddl(
            &self.fq_main(), &canonical_cols(self.embed_dim),
            self.cfg.hnsw, self.embed_dim, None,
        ) {
            conn.execute_batch(&stmt).with_context(|| format!("ddl: {stmt}"))?;
        }
        self.ensure_promote_columns(conn)?;
        Ok(())
    }

    fn ensure_promote_columns(&self, conn: &rusqlite::Connection) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq_main(), &pc.column_name(), pg_type_to_sqlite(&pc.type_),
            );
            match conn.execute_batch(&stmt) {
                Ok(()) => {}
                Err(e) => {
                    let m = e.to_string().to_lowercase();
                    if m.contains("duplicate column") { continue; }
                    return Err(anyhow!("ADD COLUMN promote_metadata: {e}"));
                }
            }
        }
        Ok(())
    }

    fn table_exists_sync(&self, conn: &rusqlite::Connection, table: &str) -> bool {
        let r: Option<i32> = conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
                rusqlite::params![table],
                |row| row.get(0),
            )
            .ok();
        r.is_some()
    }

    fn overwrite_create(&self, conn: &rusqlite::Connection) -> Result<()> {
        // Foreign-tag refuse: when the table exists and force_overwrite=false,
        // refuse to drop if any rows belong to a different source_tag.
        if self.table_exists_sync(conn, &self.cfg.table) && !self.cfg.force_overwrite {
            let stmt = format!(
                "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
                self.fq_main()
            );
            let mut q = conn.prepare(&stmt)?;
            let existing: BTreeSet<String> = q
                .query_map([], |r| r.get::<_, String>(0))?
                .filter_map(|r| r.ok())
                .collect();
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {table}: foreign source_tag {foreign:?}",
                    table = self.cfg.table, foreign = foreign,
                ));
            }
        }
        if self.table_exists_sync(conn, &self.cfg.table) {
            conn.execute_batch(&self.backend.drop_table_sql(&self.fq_main()))
                .context("drop main")?;
            conn.execute_batch(&format!("DROP TABLE IF EXISTS {}", self.fq_vec()))
                .context("drop vec")?;
        }
        self.create_base_ddl(conn)
    }
}

impl Sink for SqliteSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let conn = self.backend.connect().await?;
            let conn_arc = conn.clone();
            let cfg_mode = self.cfg.mode.clone();
            // Move work into spawn_blocking, holding `self` references via
            // a closure. We need owned data — clone what's small.
            let this_ptr = self as *const SqliteSink as usize;
            tokio::task::spawn_blocking(move || -> Result<()> {
                let g = conn_arc.blocking_lock();
                // SAFETY: spawn_blocking borrows are guaranteed to outlive
                // the async caller because we await this future synchronously.
                // We use a raw pointer to dodge the lifetime gymnastics.
                let this = unsafe { &*(this_ptr as *const SqliteSink) };
                this.create_database_noop(&g)?;
                match cfg_mode.as_str() {
                    "overwrite" => this.overwrite_create(&g)?,
                    "create_if_missing" => return Err(anyhow!("create_if_missing not yet implemented")),
                    "append" => return Err(anyhow!("append not yet implemented")),
                    other => return Err(anyhow!("unknown target.mode: {other:?}")),
                }
                Ok(())
            })
            .await
            .context("spawn_blocking create_table")?
        }
    }
    // The other 4 trait methods stay as the stub-error returns from Task 3
    // until later tasks implement them.
    fn write_document(
        &self, _doc_id: &str, _chunks: &[Chunk],
        _embeddings: &[Vec<f32>], _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { Err(anyhow!("write_document not yet implemented")) }
    }
    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("delete_document not yet implemented")) }
    }
    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { Err(anyhow!("count_docs not yet implemented")) }
    }
    fn query_top_k(
        &self, _query_vec: &[f32], _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { Err(anyhow!("query_top_k not yet implemented")) }
    }
}

// SqliteSink methods that don't fit on the Sink trait but are called from inside
// spawn_blocking closures.
impl SqliteSink {
    fn create_database_noop(&self, conn: &rusqlite::Connection) -> Result<()> {
        // SELECT 1 noop on SQLite — emit anyway for symmetry with PG's CREATE SCHEMA.
        conn.execute_batch(&self.backend.create_database_sql(&self.cfg.database_name))?;
        Ok(())
    }
}
```

NOTE on the raw-pointer usage: it's a workaround for Rust's borrow checker around `spawn_blocking + &self`. The cleaner alternative is to make `SqliteSink: Clone` (cheap — `cfg` is cheap, `backend` holds only `dsn_env: String`) and `move` a clone in. Use whichever the engineer prefers. The pointer pattern is OK because we `.await` synchronously, but it invites mistakes — switching to clone-then-move is the recommended refinement in the next task.

Even better: split into `fn create_table_blocking(&self, conn: &Connection) -> Result<()>` and have the async body do `let cfg = self.cfg.clone(); let backend = self.backend.clone();`, then build a temp `SqliteSink` inside the blocking closure. But that requires `SQLiteBackend: Clone`. Add `#[derive(Clone)]` to `SQLiteBackend` in `backends/sqlite.rs` if needed — it just holds `dsn_env: String` so it's cheap.

**Pragmatic recommendation for the engineer:** add `#[derive(Clone)]` to both `SQLiteBackend` and `SqliteSink` (the latter requires `SqliteTargetConfig: Clone` which it already is). Then write each `Sink` trait method as:

```rust
fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
    let this = self.clone();
    async move {
        let conn = this.backend.connect().await?;
        tokio::task::spawn_blocking(move || -> Result<()> {
            let g = conn.blocking_lock();
            this.create_database_noop(&g)?;
            match this.cfg.mode.as_str() {
                "overwrite" => this.overwrite_create(&g)?,
                ...
            }
            Ok(())
        }).await.context("spawn_blocking create_table")?
    }
}
```

Use the clone pattern. Update Task 3's stub `SQLiteBackend` to `#[derive(Clone)]` if it isn't already.

- [ ] **Step 4: Run the create_table tests + full suite**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_create_table 2>&1 | tail -10`

Expected: both overwrite tests pass.

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | tail -3`

Expected: 0 failures across the board.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/src/backends/sqlite.rs \
        rust/chunkshop/tests/sqlite_sink_create_table.rs
git commit -m "$(cat <<'EOF'
feat(sinks): SqliteSink::create_table overwrite mode + HNSW once-warning

create_table opens a connection, runs the create_database SELECT-1 noop,
and dispatches on cfg.mode. overwrite branch implements foreign-source_tag
refuse (mirrors Python), drops both main + vec0 tables when present, then
re-creates via emit_chunks_table_ddl. SQLiteBackend + SqliteSink now Clone
to support the spawn_blocking move pattern. HNSW=true emits one
tracing::warn! per process via OnceLock.
EOF
)"
```

---

### Task 9: `SqliteSink::create_table` — `create_if_missing` + `append` modes

**Files:**
- Modify: `rust/chunkshop/src/sinks/sqlite.rs`
- Modify: `rust/chunkshop/tests/sqlite_sink_create_table.rs`
- Create: `rust/chunkshop/tests/sqlite_sink_modes.rs`

Reference: `python/src/chunkshop/sinks/sqlite.py` lines 133-159.

- [ ] **Step 1: Write the failing tests for the two new modes**

Append to `rust/chunkshop/tests/sqlite_sink_create_table.rs`:

```rust
#[tokio::test]
async fn create_if_missing_creates_when_absent() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CIM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("c.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "create_if_missing"), b, 4);
    sink.create_table().await.expect("create");
    let b2 = SQLiteBackend::new(env);
    assert_both_tables_exist(&b2).await;
}

#[tokio::test]
async fn create_if_missing_is_idempotent() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CIM2_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("c.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "create_if_missing"), b, 4);
    sink.create_table().await.expect("first");
    let b2 = SQLiteBackend::new(env.clone());
    let sink2 = SqliteSink::new(cfg(&env, "create_if_missing"), b2, 4);
    sink2.create_table().await.expect("second");
}
```

Create `rust/chunkshop/tests/sqlite_sink_modes.rs`:

```rust
//! append-mode preflight + overwrite foreign-tag refuse + HNSW warning behaviors.

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::SqliteSink;
use chunkshop::sinks::Sink;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str, source_tag: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: mode.into(),
        source_tag: Some(source_tag.into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    }
}

#[tokio::test]
async fn append_errors_when_table_missing() {
    let dir = tempdir().unwrap();
    let env = format!("R3_AM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("a.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "append", "t1"), b, 4);
    let err = sink.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("does not exist"), "expected 'does not exist': {msg}");
}

#[tokio::test]
async fn append_errors_when_dim_mismatches() {
    let dir = tempdir().unwrap();
    let env = format!("R3_ADM_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("d.db").to_str().unwrap());
    // Set up with dim=4
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();

    // Append claiming dim=8 — must error.
    let b2 = SQLiteBackend::new(env);
    let sink2 = SqliteSink::new(cfg("R3_ADM_BOGUS", "append", "t2"), b2, 8);
    // (dsn_env still resolves; SQLITE_ADM is set by the first cfg)
    let err = sink2.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("dim 4") && msg.contains("embed_dim 8"),
            "expected dim mismatch: {msg}");
}

#[tokio::test]
async fn append_errors_when_vec_partner_missing() {
    let dir = tempdir().unwrap();
    let env = format!("R3_AVM_{}", std::process::id());
    let path = dir.path().join("nv.db");
    std::env::set_var(&env, path.to_str().unwrap());
    // Hand-create a chunks table WITHOUT its vec0 partner.
    let conn = rusqlite::Connection::open(&path).unwrap();
    conn.execute_batch("CREATE TABLE chunks (id TEXT PRIMARY KEY)").unwrap();
    drop(conn);

    let b = SQLiteBackend::new(env);
    let sink = SqliteSink::new(cfg(/*ignored*/"X", "append", "t1"), b, 4);
    // Override env to point at our prepared file
    let err = sink.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("no vec0 partner"), "expected 'no vec0 partner': {msg}");
}

#[tokio::test]
async fn overwrite_refuses_foreign_source_tag() {
    let dir = tempdir().unwrap();
    let env = format!("R3_FT_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("f.db").to_str().unwrap());
    // First sink writes a row tagged "t1".
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();
    {
        let conn = rusqlite::Connection::open(dir.path().join("f.db")).unwrap();
        conn.execute(
            "INSERT INTO chunks (id, doc_id, seq_num, original_content, embedded_content, source) \
             VALUES ('a', 'd', 0, 'x', 'x', 't1')",
            [],
        ).unwrap();
    }
    // Second sink tries to overwrite with a different source_tag.
    let b2 = SQLiteBackend::new(env);
    let sink2 = SqliteSink::new(cfg(/*ignored arg*/"X", "overwrite", "t2"), b2, 4);
    let err = sink2.create_table().await.unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("foreign source_tag"), "expected: {msg}");
}

#[tokio::test]
#[tracing_test::traced_test]
async fn hnsw_emits_one_warning_per_process() {
    let dir = tempdir().unwrap();
    let env = format!("R3_HNSW_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("h.db").to_str().unwrap());
    let mut c = cfg(&env, "overwrite", "t1");
    c.hnsw = true;
    // Two sinks built in the same process — exactly ONE warning total.
    let b1 = SQLiteBackend::new(env.clone());
    let _s1 = SqliteSink::new(c.clone(), b1, 4);
    let b2 = SQLiteBackend::new(env);
    let _s2 = SqliteSink::new(c, b2, 4);
    assert!(logs_contain("no-op"));
    // Note: tracing-test doesn't easily count occurrences; the OnceLock
    // guarantee + this presence assertion is enough.
}
```

NOTE on the `dsn_env` mismatch in tests above: the test config uses `&env` for the env-var name; the per-test variations passing `"X"` for some calls are bugs in my test draft above. Standardize: `cfg(&env, mode, tag)` always uses the actual env var. Ignore the placeholder `"X"` calls — the engineer should fix those by calling `cfg(&env, mode, tag)` everywhere consistently.

**Engineer:** when copying these tests, use `&env` (the unique-per-test env var name) consistently as the first arg to `cfg(...)`. The placeholder `"X"` strings shown above are typos.

- [ ] **Step 2: Run them — they fail**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_modes --test sqlite_sink_create_table 2>&1 | tail -30`

Expected: failures from `create_if_missing not yet implemented` and `append not yet implemented`.

- [ ] **Step 3: Implement the two modes**

In `rust/chunkshop/src/sinks/sqlite.rs`, add to the `impl SqliteSink` block:

```rust
    fn create_if_missing(&self, conn: &rusqlite::Connection) -> Result<()> {
        if !self.table_exists_sync(conn, &self.cfg.table) {
            return self.create_base_ddl(conn);
        }
        // Idempotent ADD COLUMN source — catch duplicate-column.
        match conn.execute_batch(&self.backend.add_column_if_not_exists_sql(
            &self.fq_main(), "source", "TEXT")) {
            Ok(()) => {}
            Err(e) => {
                let m = e.to_string().to_lowercase();
                if !m.contains("duplicate column") {
                    return Err(anyhow!("ADD COLUMN source: {e}"));
                }
            }
        }
        self.ensure_promote_columns(conn)
    }

    fn append_preflight(&self, conn: &rusqlite::Connection) -> Result<()> {
        if !self.table_exists_sync(conn, &self.cfg.table) {
            return Err(anyhow!(
                "append mode: table {} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.table
            ));
        }
        let current_dim = self.read_embedding_dim_sync(conn)?;
        let Some(d) = current_dim else {
            return Err(anyhow!(
                "append mode: {} has no vec0 partner table — not a chunkshop table.",
                self.cfg.table
            ));
        };
        if d != self.embed_dim {
            return Err(anyhow!(
                "append mode: target dim {d} != cell embed_dim {}", self.embed_dim
            ));
        }
        // Ensure source column + promote columns.
        match conn.execute_batch(&self.backend.add_column_if_not_exists_sql(
            &self.fq_main(), "source", "TEXT")) {
            Ok(()) => {}
            Err(e) => {
                let m = e.to_string().to_lowercase();
                if !m.contains("duplicate column") {
                    return Err(anyhow!("ADD COLUMN source: {e}"));
                }
            }
        }
        self.ensure_promote_columns(conn)
    }

    fn read_embedding_dim_sync(&self, conn: &rusqlite::Connection) -> Result<Option<usize>> {
        let vec_table = format!("{}_vec", self.cfg.table);
        let sql: Option<String> = conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                rusqlite::params![vec_table],
                |row| row.get(0),
            )
            .ok();
        let Some(sql) = sql else { return Ok(None) };
        let re = regex::Regex::new(r"(?i)FLOAT\[(\d+)\]").unwrap();
        Ok(re.captures(&sql)
            .and_then(|c| c.get(1))
            .and_then(|m| m.as_str().parse().ok()))
    }
```

Replace the `match cfg_mode.as_str() { ... }` arm in `create_table` with:

```rust
match this.cfg.mode.as_str() {
    "overwrite" => this.overwrite_create(&g)?,
    "create_if_missing" => this.create_if_missing(&g)?,
    "append" => this.append_preflight(&g)?,
    other => return Err(anyhow!("unknown target.mode: {other:?}")),
}
```

- [ ] **Step 4: Run the tests**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_modes --test sqlite_sink_create_table 2>&1 | tail -25`

Expected: all 6 tests pass (4 modes + 2 create_table cases).

Full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/tests/sqlite_sink_create_table.rs \
        rust/chunkshop/tests/sqlite_sink_modes.rs
git commit -m "$(cat <<'EOF'
feat(sinks): SqliteSink create_if_missing + append modes

append preflight refuses missing table, missing vec0 partner, or dim
mismatch with clear errors. create_if_missing is idempotent — adds
source column + promote columns when the table already exists,
catching duplicate-column. SC-005 (HNSW once-warning) covered by
sqlite_sink_modes.rs via tracing-test.
EOF
)"
```

---

### Task 10: `SqliteSink::write_document` — two-table dance

**Files:**
- Modify: `rust/chunkshop/src/sinks/sqlite.rs`
- Create: `rust/chunkshop/tests/sqlite_sink_two_table_dance.rs`

Reference: `python/src/chunkshop/sinks/sqlite.py` lines 164-222. THE load-bearing test of R3.

- [ ] **Step 1: Write the failing test**

Create `rust/chunkshop/tests/sqlite_sink_two_table_dance.rs`:

```rust
//! R3-SC-003: write_document upserts main table AND DELETE+INSERTs into vec0
//! in the SAME transaction. Re-writing the same doc_id replaces vec rows,
//! does NOT duplicate them or fail with UNIQUE-constraint errors.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::SqliteSink;
use chunkshop::sinks::Sink;
use serde_json::json;
use tempfile::tempdir;

fn cfg(dsn_env: &str, delete_orphans: bool) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans,
    }
}

fn chunk(doc_id: &str, n: i32) -> Vec<Chunk> {
    (0..n).map(|i| Chunk {
        doc_id: doc_id.into(),
        seq_num: i,
        original_content: format!("c{i}"),
        embedded_content: format!("c{i}"),
        metadata: json!({"k": i}),
    }).collect()
}

fn embeddings(n: usize, dim: usize) -> Vec<Vec<f32>> {
    (0..n).map(|i| {
        let mut v = vec![0.0_f32; dim];
        v[i % dim] = 1.0;
        v
    }).collect()
}

async fn count(b: &SQLiteBackend, sql: &str) -> i64 {
    let conn = b.connect().await.unwrap();
    let g = conn.lock().await;
    g.query_row(sql, [], |r| r.get(0)).unwrap()
}

#[tokio::test]
async fn write_creates_3_rows_in_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_W3_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("w.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, false), b, 4);
    sink.create_table().await.unwrap();

    let chunks = chunk("d1", 3);
    let embs = embeddings(3, 4);
    let tags = vec![vec![]; 3];
    sink.write_document("d1", &chunks, &embs, &tags).await.unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks").await, 3);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks_vec").await, 3);
}

#[tokio::test]
async fn rewriting_same_doc_replaces_vec_rows_no_duplicates() {
    let dir = tempdir().unwrap();
    let env = format!("R3_RW_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("rw.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, false), b, 4);
    sink.create_table().await.unwrap();

    let chunks = chunk("d1", 3);
    let embs = embeddings(3, 4);
    let tags = vec![vec![]; 3];
    sink.write_document("d1", &chunks, &embs, &tags).await.unwrap();
    sink.write_document("d1", &chunks, &embs, &tags).await.unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks").await, 3, "main");
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks_vec").await, 3, "vec");
}

#[tokio::test]
async fn delete_orphans_shrinks_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DO_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("do.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, true), b, 4);
    sink.create_table().await.unwrap();

    // First: 5 chunks
    let chunks = chunk("d1", 5);
    let embs = embeddings(5, 4);
    let tags = vec![vec![]; 5];
    sink.write_document("d1", &chunks, &embs, &tags).await.unwrap();

    // Re-write with only 2 chunks — orphans 2..4 should be deleted from both
    let chunks2 = chunk("d1", 2);
    let embs2 = embeddings(2, 4);
    let tags2 = vec![vec![]; 2];
    sink.write_document("d1", &chunks2, &embs2, &tags2).await.unwrap();

    let b = SQLiteBackend::new(env);
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks").await, 2, "main shrunk");
    assert_eq!(count(&b, "SELECT COUNT(*) FROM chunks_vec").await, 2, "vec shrunk");
}
```

- [ ] **Step 2: Run them — they fail**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_two_table_dance 2>&1 | tail -10`

Expected: 3 failures with `write_document not yet implemented`.

- [ ] **Step 3: Implement write_document**

In `rust/chunkshop/src/sinks/sqlite.rs`, replace the stub `write_document` impl with the real one. Reference: `python/src/chunkshop/sinks/sqlite.py` lines 164-222.

```rust
    fn write_document(
        &self, doc_id: &str, chunks: &[Chunk],
        embeddings: &[Vec<f32>], tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        let this = self.clone();
        let doc_id = doc_id.to_string();
        let chunks = chunks.to_vec();
        let embeddings = embeddings.to_vec();
        let tags_per_chunk = tags_per_chunk.to_vec();
        async move {
            if chunks.len() != embeddings.len() || chunks.len() != tags_per_chunk.len() {
                return Err(anyhow!(
                    "chunks/embeddings/tags length mismatch: {} / {} / {}",
                    chunks.len(), embeddings.len(), tags_per_chunk.len()
                ));
            }
            if chunks.is_empty() { return Ok(()); }

            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<()> {
                let mut g = conn.blocking_lock();
                let tx = g.transaction().context("begin tx")?;
                this.write_document_in_tx(&tx, &doc_id, &chunks, &embeddings, &tags_per_chunk)?;
                tx.commit().context("commit tx")?;
                Ok(())
            }).await.context("spawn_blocking write_document")?
        }
    }
```

Add the in-tx impl:

```rust
    fn write_document_in_tx(
        &self, tx: &rusqlite::Transaction<'_>,
        doc_id: &str, chunks: &[Chunk],
        embeddings: &[Vec<f32>], tags_per_chunk: &[Vec<String>],
    ) -> Result<()> {
        let promote = &self.cfg.promote_metadata;
        // Main table cols (no embedding).
        let mut main_col_names: Vec<String> = vec![
            "id".into(), "doc_id".into(), "seq_num".into(),
            "original_content".into(), "embedded_content".into(),
            "tags".into(), "metadata".into(), "source".into(),
        ];
        for pc in promote { main_col_names.push(pc.column_name()); }
        let mut update_cols: Vec<&str> = vec![
            "original_content", "embedded_content", "tags", "metadata",
        ];
        // Source excluded from update — write-once.
        let promoted_names: Vec<String> = promote.iter().map(|pc| pc.column_name()).collect();
        for n in &promoted_names { update_cols.push(n.as_str()); }
        let upsert = self.backend.upsert_clause(&["id"], &update_cols);
        let cols_sql: String = main_col_names.iter()
            .map(|c| self.backend.quote_ident(c)).collect::<Vec<_>>().join(", ");
        let placeholders: String = std::iter::repeat("?")
            .take(main_col_names.len()).collect::<Vec<_>>().join(", ");
        let main_stmt = format!(
            "INSERT INTO {tbl} ({cols_sql}) VALUES ({placeholders}) {upsert}",
            tbl = self.fq_main()
        );

        // vec0 — DELETE-by-id then INSERT (vec0 refuses UPSERT and INSERT OR REPLACE).
        let vec_delete = format!("DELETE FROM {} WHERE id = ?", self.fq_vec());
        let vec_insert = format!(
            "INSERT INTO {} (id, embedding) VALUES (?, ?)",
            self.fq_vec()
        );

        let mut main_q = tx.prepare(&main_stmt).context("prepare main upsert")?;
        let mut vec_del_q = tx.prepare(&vec_delete).context("prepare vec delete")?;
        let mut vec_ins_q = tx.prepare(&vec_insert).context("prepare vec insert")?;

        for (i, c) in chunks.iter().enumerate() {
            let id = format!("{}::{}", c.doc_id, c.seq_num);
            let tags_lit = serde_json::to_string(&tags_per_chunk[i])?;
            let meta_lit = serde_json::to_string(&c.metadata)?;
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![
                Box::new(id.clone()),
                Box::new(c.doc_id.clone()),
                Box::new(c.seq_num),
                Box::new(c.original_content.clone()),
                Box::new(c.embedded_content.clone()),
                Box::new(tags_lit),
                Box::new(meta_lit),
                Box::new(self.cfg.source_tag.clone()),
            ];
            for pc in promote {
                let v = jsonb_path_get(&c.metadata, &pc.path);
                let s: Option<String> = v.map(|val| match val {
                    serde_json::Value::String(s) => s.clone(),
                    other => serde_json::to_string(other).unwrap_or_default(),
                });
                params.push(Box::new(s));
            }
            let p_refs: Vec<&dyn rusqlite::ToSql> = params.iter()
                .map(|b| b.as_ref()).collect();
            main_q.execute(p_refs.as_slice()).context("upsert main row")?;

            // vec table
            vec_del_q.execute(rusqlite::params![id]).context("delete vec")?;
            let vec_lit = self.backend.vector_literal(&embeddings[i]);
            vec_ins_q.execute(rusqlite::params![id, vec_lit]).context("insert vec")?;
        }

        if self.cfg.delete_orphans {
            drop(main_q); drop(vec_del_q); drop(vec_ins_q);
            let n_new = chunks.len() as i64;
            tx.execute(
                &format!("DELETE FROM {} WHERE doc_id = ? AND seq_num >= ?", self.fq_main()),
                rusqlite::params![doc_id, n_new],
            ).context("delete orphans main")?;
            // Vec table: id format is `doc_id::seq_num`. Match by LIKE + parse seq.
            tx.execute(
                &format!(
                    "DELETE FROM {} WHERE id LIKE ? || '::%' \
                     AND CAST(substr(id, instr(id, '::') + 2) AS INTEGER) >= ?",
                    self.fq_vec()
                ),
                rusqlite::params![doc_id, n_new],
            ).context("delete orphans vec")?;
        }
        Ok(())
    }
```

Note: `Chunk.seq_num` is `i32`. Verify by reading `rust/chunkshop/src/chunker.rs` — adjust the cast in `delete_orphans` if needed (Python uses int).

- [ ] **Step 4: Run the tests**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_two_table_dance 2>&1 | tail -15`

Expected: all 3 tests pass.

Full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/tests/sqlite_sink_two_table_dance.rs
git commit -m "$(cat <<'EOF'
feat(sinks): SqliteSink::write_document two-table dance

INSERT...ON CONFLICT(id) DO UPDATE SET on the main chunks table; vec0
partner uses DELETE-by-id then INSERT inside the same rusqlite::Transaction
(vec0 refuses UPSERT and INSERT OR REPLACE). promote_metadata columns
populated via _jsonb_path_get equivalent. delete_orphans deletes from
BOTH tables when set, parsing seq_num out of id-string for the vec
table query (no doc_id column on vec0).
EOF
)"
```

---

### Task 11: `SqliteSink::delete_document` + `count_docs`

**Files:**
- Modify: `rust/chunkshop/src/sinks/sqlite.rs`
- Create: `rust/chunkshop/tests/sqlite_sink_delete_document.rs`

Reference: `python/src/chunkshop/sinks/sqlite.py` lines 224-256.

- [ ] **Step 1: Write the failing tests**

Create `rust/chunkshop/tests/sqlite_sink_delete_document.rs`:

```rust
//! R3-SC-010: delete_document removes from BOTH tables, scoped to source_tag
//! when set. Mirrors Python's test_sc017_*.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::SqliteSink;
use chunkshop::sinks::Sink;
use serde_json::json;
use tempfile::tempdir;

fn cfg(dsn_env: &str, mode: &str, source_tag: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: dsn_env.to_string(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false, hnsw: false,
        mode: mode.into(),
        source_tag: Some(source_tag.into()),
        promote_metadata: vec![], force_overwrite: false, delete_orphans: false,
    }
}

fn chunks(doc_id: &str, n: i32) -> Vec<Chunk> {
    (0..n).map(|i| Chunk {
        doc_id: doc_id.into(), seq_num: i,
        original_content: format!("c{i}"), embedded_content: format!("c{i}"),
        metadata: json!({}),
    }).collect()
}

fn embs(n: usize, dim: usize) -> Vec<Vec<f32>> {
    (0..n).map(|_| vec![0.5_f32; dim]).collect()
}

#[tokio::test]
async fn delete_document_removes_from_both_tables() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DD_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("dd.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();

    sink.write_document("d1", &chunks("d1", 3), &embs(3, 4), &vec![vec![]; 3]).await.unwrap();
    sink.write_document("d2", &chunks("d2", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();

    let n = sink.delete_document("d1").await.unwrap();
    assert_eq!(n, 3);

    let conn = sink.backend.connect().await.unwrap();
    let g = conn.lock().await;
    let n_main: i64 = g.query_row("SELECT COUNT(*) FROM chunks", [], |r| r.get(0)).unwrap();
    let n_vec: i64 = g.query_row("SELECT COUNT(*) FROM chunks_vec", [], |r| r.get(0)).unwrap();
    assert_eq!(n_main, 2);
    assert_eq!(n_vec, 2);
}

#[tokio::test]
async fn delete_document_respects_source_tag_scope() {
    let dir = tempdir().unwrap();
    let env = format!("R3_DDS_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("dds.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink1 = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink1.create_table().await.unwrap();
    sink1.write_document("d1", &chunks("d1", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();

    // Different source_tag — must not delete t1's rows
    let b2 = SQLiteBackend::new(env);
    let sink2 = SqliteSink::new(cfg(/*ignored*/"X", "create_if_missing", "t2"), b2, 4);
    sink2.create_table().await.unwrap();
    let n = sink2.delete_document("d1").await.unwrap();
    assert_eq!(n, 0);
}

#[tokio::test]
async fn count_docs_distinct() {
    let dir = tempdir().unwrap();
    let env = format!("R3_CD_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("cd.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env, "overwrite", "t1"), b, 4);
    sink.create_table().await.unwrap();
    sink.write_document("d1", &chunks("d1", 3), &embs(3, 4), &vec![vec![]; 3]).await.unwrap();
    sink.write_document("d2", &chunks("d2", 1), &embs(1, 4), &vec![vec![]; 1]).await.unwrap();
    sink.write_document("d1", &chunks("d1", 2), &embs(2, 4), &vec![vec![]; 2]).await.unwrap();
    assert_eq!(sink.count_docs().await.unwrap(), 2);
}
```

(Same env-var-naming caveat as Task 9: standardize on `&env` everywhere.)

- [ ] **Step 2: Run them — they fail**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_delete_document 2>&1 | tail -10`

Expected: 3 failures with "not yet implemented".

- [ ] **Step 3: Implement delete_document + count_docs**

In `sinks/sqlite.rs`, replace the stub trait methods:

```rust
    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        let this = self.clone();
        let doc_id = doc_id.to_string();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<i64> {
                let mut g = conn.blocking_lock();
                let tx = g.transaction().context("begin tx")?;
                // Two-phase: SELECT ids first, then DELETE both tables by id IN (...).
                let ids: Vec<String> = {
                    let stmt = if this.cfg.source_tag.is_some() {
                        format!("SELECT id FROM {} WHERE doc_id = ? AND source = ?", this.fq_main())
                    } else {
                        format!("SELECT id FROM {} WHERE doc_id = ?", this.fq_main())
                    };
                    let mut q = tx.prepare(&stmt)?;
                    let rows: rusqlite::Result<Vec<String>> = if let Some(tag) = &this.cfg.source_tag {
                        q.query_map(rusqlite::params![doc_id, tag], |r| r.get(0))?.collect()
                    } else {
                        q.query_map(rusqlite::params![doc_id], |r| r.get(0))?.collect()
                    };
                    rows.context("collect ids to delete")?
                };
                if ids.is_empty() {
                    tx.commit()?;
                    return Ok(0);
                }
                let placeholders: String = std::iter::repeat("?").take(ids.len()).collect::<Vec<_>>().join(",");
                let main_del = format!("DELETE FROM {} WHERE id IN ({placeholders})", this.fq_main());
                let vec_del = format!("DELETE FROM {} WHERE id IN ({placeholders})", this.fq_vec());
                let p: Vec<&dyn rusqlite::ToSql> = ids.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
                let n = tx.execute(&main_del, p.as_slice()).context("delete main")? as i64;
                tx.execute(&vec_del, p.as_slice()).context("delete vec")?;
                tx.commit()?;
                Ok(n)
            }).await.context("spawn_blocking delete_document")?
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        let this = self.clone();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<i64> {
                let g = conn.blocking_lock();
                let n: i64 = g.query_row(
                    &format!("SELECT COUNT(DISTINCT doc_id) FROM {}", this.fq_main()),
                    [], |r| r.get(0)
                ).context("count_docs")?;
                Ok(n)
            }).await.context("spawn_blocking count_docs")?
        }
    }
```

- [ ] **Step 4: Run the tests**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_delete_document 2>&1 | tail -10`

Expected: 3 tests pass.

Full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/tests/sqlite_sink_delete_document.rs
git commit -m "$(cat <<'EOF'
feat(sinks): SqliteSink::delete_document + count_docs

delete_document is two-phase per Python parity: SELECT matching ids
(scoped to source_tag when set), then DELETE FROM main WHERE id IN (?,...)
followed by DELETE FROM vec WHERE id IN (...) inside the same tx.
Returns rowcount from the main table delete. count_docs is a plain
SELECT COUNT(DISTINCT doc_id) on the main table.
EOF
)"
```

---

### Task 12: `SqliteSink::query_top_k`

**Files:**
- Modify: `rust/chunkshop/src/sinks/sqlite.rs`
- Create: `rust/chunkshop/tests/sqlite_sink_query_top_k.rs`

Reference: `python/src/chunkshop/sinks/sqlite.py` lines 258-276.

- [ ] **Step 1: Write the failing test**

Create `rust/chunkshop/tests/sqlite_sink_query_top_k.rs`:

```rust
//! R3-SC-004: query_top_k runs vec0 MATCH joined back to chunks, returns
//! (doc_id, seq_num, distance) ordered by ascending distance.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::{Sink, SqliteSink};
use serde_json::json;
use tempfile::tempdir;

fn cfg(env: &str) -> SqliteTargetConfig {
    SqliteTargetConfig {
        dsn_env: env.to_string(), database_name: "ignored".into(), table: "chunks".into(),
        overwrite: false, hnsw: false, mode: "overwrite".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![], force_overwrite: false, delete_orphans: false,
    }
}

#[tokio::test]
async fn query_top_k_returns_ordered_distance_tuples() {
    let dir = tempdir().unwrap();
    let env = format!("R3_QTK_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("q.db").to_str().unwrap());
    let b = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg(&env), b, 4);
    sink.create_table().await.unwrap();

    let chunks: Vec<Chunk> = (0..5).map(|i| Chunk {
        doc_id: "d1".into(), seq_num: i,
        original_content: format!("c{i}"), embedded_content: format!("c{i}"),
        metadata: json!({}),
    }).collect();
    let embs: Vec<Vec<f32>> = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.9, 0.1, 0.0, 0.0],
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
        vec![0.0, 0.0, 0.0, 1.0],
    ];
    sink.write_document("d1", &chunks, &embs, &vec![vec![]; 5]).await.unwrap();

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let results = sink.query_top_k(&q, 3).await.unwrap();
    assert_eq!(results.len(), 3);
    let dists: Vec<f64> = results.iter().map(|r| r.2).collect();
    let mut sorted = dists.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(dists, sorted, "non-decreasing distance");
    // Top-1 must be chunk 0 (exact vector match).
    assert_eq!(results[0].1, 0);
    assert_eq!(results[0].0, "d1");
}
```

- [ ] **Step 2: Run it — it fails**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_query_top_k 2>&1 | tail -10`

Expected: failure with "not yet implemented".

- [ ] **Step 3: Implement query_top_k**

In `sinks/sqlite.rs`, replace the stub:

```rust
    fn query_top_k(
        &self, query_vec: &[f32], k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        let this = self.clone();
        let q_owned = query_vec.to_vec();
        async move {
            let conn = this.backend.connect().await?;
            tokio::task::spawn_blocking(move || -> Result<Vec<(String, i32, f64)>> {
                let g = conn.blocking_lock();
                let vec_lit = this.backend.vector_literal(&q_owned);
                let stmt = format!(
                    "SELECT c.doc_id, c.seq_num, v.distance \
                     FROM {vec} v JOIN {main} c ON c.id = v.id \
                     WHERE v.embedding MATCH ? AND k = ? \
                     ORDER BY v.distance",
                    vec = this.fq_vec(), main = this.fq_main()
                );
                let mut q = g.prepare(&stmt).context("prepare top_k")?;
                let rows = q.query_map(
                    rusqlite::params![vec_lit, k as i64],
                    |r| Ok((r.get::<_, String>(0)?, r.get::<_, i32>(1)?, r.get::<_, f64>(2)?))
                ).context("query top_k")?;
                let out: rusqlite::Result<Vec<_>> = rows.collect();
                Ok(out.context("collect top_k rows")?)
            }).await.context("spawn_blocking query_top_k")?
        }
    }
```

- [ ] **Step 4: Run the test + full suite**

Run: `cargo test -p chunkshop-rs --test sqlite_sink_query_top_k 2>&1 | tail -10`

Expected: pass.

Full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/sqlite.rs \
        rust/chunkshop/tests/sqlite_sink_query_top_k.rs
git commit -m "$(cat <<'EOF'
feat(sinks): SqliteSink::query_top_k via vec0 MATCH JOIN

JOIN syntax mirrors Python: vec0 holds embedding+id, main table holds
the rest, JOIN ON id. WHERE v.embedding MATCH ? AND k = ? is the canonical
sqlite-vec query form. Returns (doc_id, seq_num, distance) ordered ascending.
EOF
)"
```

---

### Task 13: ⛔ DC-002 — Drift checkpoint

- [ ] **Step 1: Re-read the mission brief**

Read `/home/yonk/yonk-tools/chunkshop-r3-sqlite/skill-output/mission-brief/Mission-Brief-r3-rust-sqlite.md`.

- [ ] **Step 2: Run the three drift questions**

1. Still solving the Purpose? (SQLite backend with parity)
2. Each task 7–12 maps to which SC?
   - Task 7 → SC-001 (connection methods on backend)
   - Task 8 → SC-002 (overwrite create both tables), SC-005 (HNSW once-warning)
   - Task 9 → SC-002 (all 3 modes), SC-006 (append preflight)
   - Task 10 → SC-003 (two-table dance)
   - Task 11 → SC-010 (delete_document)
   - Task 12 → SC-004 (query_top_k)
3. Out of scope check — same as DC-001:
   - `git diff --stat experimental/v4-modular-backends..HEAD -- rust/chunkshop/src/backends/{base,postgres}.rs rust/chunkshop/src/sinks/pg.rs rust/chunkshop/src/sources/pg_table.rs python/` MUST be empty.

- [ ] **Step 3: Verify HNSW warning fires from `create_table` not `write_document`**

Specifically read `sinks/sqlite.rs::SqliteSink::new` — confirm the HNSW warning is in the constructor (mirrors Python's `__init__`). If it's in `write_document`, that's drift — fix and re-test.

Actually wait — re-read Python's pattern: `_HNSW_WARNED` is checked in `__init__`. So Rust mirrors by warning in `SqliteSink::new`, NOT `create_table`. The brief's SC-005 says "on create_table" which is a brief-vs-Python mismatch. **Defer to Python's actual behavior:** the warning fires on construction.

Update brief mental model: SC-005 reads "produces ONE warning per process" — both Python and Rust fire it on construction. The brief's "on create_table" wording is imprecise; the test only requires "fires once per process for hnsw=true." Confirm the test passes regardless of exact firing point. If the brief's specific wording bothers you, propose a brief correction to the user before continuing.

- [ ] **Step 4: Confirm sink test count**

Run from `rust/`:

```bash
cargo test -p chunkshop-rs --test sqlite_sink_create_table \
                            --test sqlite_sink_modes \
                            --test sqlite_sink_two_table_dance \
                            --test sqlite_sink_delete_document \
                            --test sqlite_sink_query_top_k 2>&1 | grep "test result"
```

Expected: 4 + 5 + 3 + 3 + 1 = 16 tests, 0 failures.

If all answers are correct, proceed to Task 14.

---

## Phase D — Source + factory wiring

### Task 14: Implement `SqliteTableSource`

**Files:**
- Modify: `rust/chunkshop/src/sources/sqlite_table.rs`
- Create: `rust/chunkshop/tests/sqlite_table_source.rs`

Reference: `python/src/chunkshop/sources/sqlite_table.py` (42 lines, the whole file).

- [ ] **Step 1: Write the failing test**

Create `rust/chunkshop/tests/sqlite_table_source.rs`:

```rust
//! Integration test: SqliteTableSource iterates over a planted table.

use chunkshop::config::SqliteTableSourceConfig;
use chunkshop::sources::SqliteTableSource;
use tempfile::tempdir;

#[tokio::test]
async fn iter_documents_yields_planted_rows() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("src.db");
    let env = format!("R3_SRC_{}", std::process::id());
    std::env::set_var(&env, path.to_str().unwrap());

    {
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE docs (id TEXT PRIMARY KEY, body TEXT, title TEXT, lang TEXT); \
             INSERT INTO docs VALUES \
                ('a', 'hello world', 'Greeting', 'en'), \
                ('b', 'bonjour le monde', 'Salutation', 'fr')",
        ).unwrap();
    }

    let cfg = SqliteTableSourceConfig {
        dsn_env: env,
        database_name: "ignored".into(),
        table: "docs".into(),
        id_column: "id".into(),
        content_column: "body".into(),
        title_column: Some("title".into()),
        where_clause: None,
        metadata_columns: vec!["lang".into()],
    };
    let src = SqliteTableSource::new(cfg);
    let docs = src.iter_documents().await.unwrap();
    assert_eq!(docs.len(), 2);
    let a = docs.iter().find(|d| d.id == "a").unwrap();
    assert_eq!(a.content, "hello world");
    assert_eq!(a.title.as_deref(), Some("Greeting"));
    assert_eq!(a.metadata.get("lang").and_then(|v| v.as_str()), Some("en"));
}

#[tokio::test]
async fn iter_documents_respects_where_clause() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("w.db");
    let env = format!("R3_SRCW_{}", std::process::id());
    std::env::set_var(&env, path.to_str().unwrap());
    {
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE docs (id TEXT, body TEXT, lang TEXT); \
             INSERT INTO docs VALUES ('a', 'x', 'en'), ('b', 'y', 'fr')",
        ).unwrap();
    }
    let cfg = SqliteTableSourceConfig {
        dsn_env: env,
        database_name: "ignored".into(), table: "docs".into(),
        id_column: "id".into(), content_column: "body".into(),
        title_column: None,
        where_clause: Some("lang = 'en'".into()),
        metadata_columns: vec![],
    };
    let docs = SqliteTableSource::new(cfg).iter_documents().await.unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].id, "a");
}
```

- [ ] **Step 2: Run it — fails**

Run: `cargo test -p chunkshop-rs --test sqlite_table_source 2>&1 | tail -10`

Expected: fails with "not yet implemented".

- [ ] **Step 3: Implement `SqliteTableSource`**

Replace the body of `rust/chunkshop/src/sources/sqlite_table.rs`:

```rust
//! SQLite source. Mirrors `python/src/chunkshop/sources/sqlite_table.py`.

use anyhow::{Context, Result};
use serde_json::json;

use crate::backends::base::BackendDialect;
use crate::backends::sqlite::SQLiteBackend;
use crate::config::SqliteTableSourceConfig;
use crate::sources::base::Document;

#[derive(Clone)]
pub struct SqliteTableSource {
    pub(crate) cfg: SqliteTableSourceConfig,
    pub(crate) backend: SQLiteBackend,
}

impl SqliteTableSource {
    pub fn new(cfg: SqliteTableSourceConfig) -> Self {
        let backend = SQLiteBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Build column list: [id, content, optional title, *metadata...]
        let mut cols: Vec<&str> = vec![&self.cfg.id_column, &self.cfg.content_column];
        let title_idx: Option<usize> = if let Some(tc) = &self.cfg.title_column {
            cols.push(tc); Some(2)
        } else { None };
        let meta_start = if title_idx.is_some() { 3 } else { 2 };
        for col in &self.cfg.metadata_columns { cols.push(col); }

        let cols_sql: String = cols.iter()
            .map(|c| self.backend.quote_ident(c))
            .collect::<Vec<_>>().join(", ");
        let fq = self.backend.fq_table(&self.cfg.database_name, &self.cfg.table);
        let mut select = format!("SELECT {cols_sql} FROM {fq}");
        if let Some(w) = &self.cfg.where_clause {
            select.push_str(&format!(" WHERE {w}"));
        }

        let conn = self.backend.connect().await?;
        let metadata_columns = self.cfg.metadata_columns.clone();
        tokio::task::spawn_blocking(move || -> Result<Vec<Document>> {
            let g = conn.blocking_lock();
            let mut q = g.prepare(&select).context("prepare source query")?;
            let n_cols = q.column_count();
            let rows = q.query_map([], |r| {
                let id_v: rusqlite::types::Value = r.get(0)?;
                let id = match id_v {
                    rusqlite::types::Value::Integer(i) => i.to_string(),
                    rusqlite::types::Value::Real(f) => f.to_string(),
                    rusqlite::types::Value::Text(s) => s,
                    rusqlite::types::Value::Null => String::new(),
                    rusqlite::types::Value::Blob(_) => "<blob>".to_string(),
                };
                let content: String = r.get(1)?;
                let title: Option<String> = title_idx.map(|i| r.get::<_, Option<String>>(i).ok().flatten()).flatten();
                let mut meta = serde_json::Map::new();
                for (i, col) in metadata_columns.iter().enumerate() {
                    let idx = meta_start + i;
                    if idx >= n_cols { break; }
                    let v: rusqlite::types::Value = r.get(idx)?;
                    let jv = match v {
                        rusqlite::types::Value::Null => serde_json::Value::Null,
                        rusqlite::types::Value::Integer(i) => json!(i),
                        rusqlite::types::Value::Real(f) => json!(f),
                        rusqlite::types::Value::Text(s) => serde_json::Value::String(s),
                        rusqlite::types::Value::Blob(_) => serde_json::Value::Null,
                    };
                    meta.insert(col.clone(), jv);
                }
                Ok(Document { id, content, title, metadata: serde_json::Value::Object(meta) })
            }).context("query source rows")?;
            let out: rusqlite::Result<Vec<Document>> = rows.collect();
            Ok(out.context("collect source rows")?)
        }).await.context("spawn_blocking iter_documents")?
    }
}
```

Note: the closure captures `title_idx` from outer scope via `move`. Rust prefers `let title_idx = title_idx;` inside the closure if needed, but it's `Copy` here.

- [ ] **Step 4: Run the tests**

Run: `cargo test -p chunkshop-rs --test sqlite_table_source 2>&1 | tail -10`

Expected: both tests pass.

Full suite: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/sqlite_table.rs \
        rust/chunkshop/tests/sqlite_table_source.rs
git commit -m "$(cat <<'EOF'
feat(sources): SqliteTableSource — column-projection SELECT, JSON metadata

Mirrors python/src/chunkshop/sources/sqlite_table.py. Column order matches
Python: [id, content, optional title, *metadata...]. id column is read
as a Value and stringified (handles INTEGER PKs gracefully). where_clause
is concatenated verbatim — operator-trusted, NOT validated (matches
PgTableSource).
EOF
)"
```

---

### Task 15: Wire `lib.rs` re-exports + sample-sqlite.yaml + CLI smoke

**Files:**
- Modify: `rust/chunkshop/src/lib.rs`
- Create: `docs/samples/sample-sqlite.yaml`

- [ ] **Step 1: Add re-exports**

Edit `rust/chunkshop/src/lib.rs`. Update the existing `pub use` lines:

```rust
pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ColSpec, PostgresBackend, SQLiteBackend};
// ... existing lines ...
pub use sinks::{AnySink, PgSink, Sink, SqliteSink};
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source, SqliteTableSource};
```

- [ ] **Step 2: Create `docs/samples/sample-sqlite.yaml`**

```yaml
# Standalone SQLite example: files source → sentence_aware → fastembed (int8 BGE)
# → sqlite + sqlite-vec target. Runs end-to-end with no external DB.

cell_name: sample_sqlite

source:
  type: files
  glob: "docs/samples/*-*.md"
  id_from: stem

chunker:
  type: sentence_aware
  doc_type: prose
  max_chars: 2000
  min_chars: 200

embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 32

target:
  type: sqlite
  dsn_env: CHUNKSHOP_SQLITE_PATH
  database: ignored
  table: chunks
  mode: overwrite
  hnsw: false
  source_tag: sample_sqlite
```

Document the env var in a header comment if your project convention is to do so.

- [ ] **Step 3: Verify the sample YAML parses**

Run: `cargo test -p chunkshop-rs --lib config:: 2>&1 | grep "test result"`

If the existing config tests load YAML files, add one that loads `docs/samples/sample-sqlite.yaml` directly. Otherwise, do a one-off:

```bash
cd /home/yonk/yonk-tools/chunkshop-r3-sqlite
cargo run -p chunkshop-rs -- --help 2>&1 | head -10
```

Just confirm the binary builds. If it has a config-validation subcommand (e.g., `chunkshop validate --config ...`), use that. Otherwise this step is best-effort coverage.

- [ ] **Step 4: Run the full suite**

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result" | wc -l`

Expected: same number of test-result lines as before (no new lines appear unless we wired a new test fn). 0 failures across all groups.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/lib.rs docs/samples/sample-sqlite.yaml
git commit -m "$(cat <<'EOF'
feat(lib,samples): re-export Sqlite types + add sample-sqlite.yaml

lib re-exports SQLiteBackend, SqliteSink, SqliteTableSource so
downstream callers can `use chunkshop::SQLiteBackend;` directly.
sample-sqlite.yaml is the standalone runnable example: files →
sentence_aware → bge-small int8 → sqlite. CHUNKSHOP_SQLITE_PATH
env var holds the file path or ':memory:'.
EOF
)"
```

---

## Phase E — Cross-language parity

### Task 16: ⛔ DC-003 — Drift checkpoint before cross-language test

- [ ] **Step 1: Re-read the mission brief**, focusing on the SC-007 entry.

- [ ] **Step 2: Confirm SC-007 mechanism alignment**

Confirm BEFORE writing the cross-language test:

1. The test shells out to `uv run python -c '...'` from the **`python/`** sibling dir, not from the worktree root. The Python CWD must be `/home/yonk/yonk-tools/chunkshop-r3-sqlite/python/` so `import chunkshop` works.
2. The test SKIPS (not fails) when `uv` is not on PATH or when `python -c "import sqlite_vec"` errors. Use `eprintln!` to log the skip reason; do not panic.
3. Tolerance is **1e-5** for distance comparison, NOT byte-exact.
4. The vectors are **deterministic** (hand-authored, not from a real embedder) — the test must NOT depend on any embedder model or HF cache.

If any of those four properties has slid since the brief was approved, that is drift — stop and discuss with the user. Otherwise proceed to Task 17.

- [ ] **Step 3: Verify Python side has SqliteSink installed**

Run from worktree root:

```bash
cd python && uv run python -c "from chunkshop.sinks.sqlite import SqliteSink; print('ok')"
```

If it errors, the worktree's Python virtualenv isn't built; run `uv sync --extra dev --extra extractors` first. If it still errors, that means the Python `SqliteSink` was never installed in this worktree's environment — escalate to the user before forcing the test to skip-everything.

---

### Task 17: Implement `cross_language_sqlite_parity.rs`

**Files:**
- Create: `rust/chunkshop/tests/cross_language_sqlite_parity.rs`

This is R3-SC-007 — the bar is automated, not manual.

- [ ] **Step 1: Write the test**

Create `rust/chunkshop/tests/cross_language_sqlite_parity.rs`:

```rust
//! R3-SC-007: cross-language vector parity. Python writes a known doc with
//! known vectors to a temp .db; Rust opens the file, runs query_top_k,
//! asserts results match within 1e-5.
//!
//! Skips when `uv` is not on PATH, or Python's `sqlite_vec` is unavailable.
//! Skip messages are logged via eprintln so CI can surface them.

use chunkshop::backends::SQLiteBackend;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::{Sink, SqliteSink};
use std::process::Command;
use tempfile::tempdir;

fn skip(reason: &str) {
    eprintln!("SKIPPING cross_language_sqlite_parity: {reason}");
}

fn uv_available() -> bool {
    Command::new("uv").arg("--version").output()
        .map(|o| o.status.success()).unwrap_or(false)
}

fn python_has_sqlite_vec(python_dir: &str) -> bool {
    Command::new("uv")
        .args(["run", "python", "-c", "import sqlite_vec; print('ok')"])
        .current_dir(python_dir)
        .output()
        .map(|o| o.status.success()).unwrap_or(false)
}

#[tokio::test]
async fn rust_reads_python_written_db() {
    // Worktree-relative — adjust if running from a different location.
    let python_dir = std::env::var("CHUNKSHOP_PY_DIR")
        .unwrap_or_else(|_| {
            // Default: relative to the rust crate at compile time.
            let manifest = env!("CARGO_MANIFEST_DIR"); // .../rust/chunkshop
            std::path::Path::new(manifest)
                .parent().unwrap()  // .../rust
                .parent().unwrap()  // worktree root
                .join("python")
                .to_string_lossy().to_string()
        });

    if !uv_available() { skip("uv not on PATH"); return; }
    if !python_has_sqlite_vec(&python_dir) { skip("python sqlite_vec unavailable"); return; }

    let dir = tempdir().unwrap();
    let db_path = dir.path().join("xlang.db");
    let env = format!("R3_XLANG_{}", std::process::id());

    // Python script: write 5 chunks with known orthogonal-ish vectors.
    let py = r#"
import os, sys, numpy as np
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks.sqlite import SqliteSink

dsn_env = "X_DSN"
os.environ[dsn_env] = sys.argv[1]
cfg = TargetConfig(type="sqlite", dsn_env=dsn_env, database="ignored",
                   table="chunks", mode="overwrite", hnsw=False, source_tag="t1")
backend = SQLiteBackend(dsn_env=dsn_env)
sink = SqliteSink(cfg, backend, embed_dim=4)
sink.create_table()

chunks = [Chunk(doc_id="d1", seq_num=i, original_content=f"c{i}",
                embedded_content=f"c{i}", metadata={}) for i in range(5)]
embs = np.array([
    [1.0, 0.0, 0.0, 0.0],
    [0.9, 0.1, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
], dtype=np.float32)
sink.write_document("d1", chunks, embs, [[]] * 5)
print("OK")
"#;

    let py_file = dir.path().join("write.py");
    std::fs::write(&py_file, py).unwrap();
    let status = Command::new("uv")
        .args(["run", "python", py_file.to_str().unwrap(), db_path.to_str().unwrap()])
        .current_dir(&python_dir)
        .status()
        .expect("spawn python");
    assert!(status.success(), "python writer must succeed");

    // Rust opens the same file and queries.
    std::env::set_var(&env, db_path.to_str().unwrap());
    let backend = SQLiteBackend::new(env.clone());
    let cfg = SqliteTargetConfig {
        dsn_env: env, database_name: "ignored".into(), table: "chunks".into(),
        overwrite: false, hnsw: false, mode: "create_if_missing".into(),
        source_tag: Some("t1".into()),
        promote_metadata: vec![], force_overwrite: false, delete_orphans: false,
    };
    let sink = SqliteSink::new(cfg, backend, 4);
    sink.create_table().await.unwrap();
    let results = sink.query_top_k(&[1.0, 0.0, 0.0, 0.0], 3).await.unwrap();
    assert_eq!(results.len(), 3);
    assert_eq!(results[0].1, 0, "top-1 must be chunk 0 (exact match)");

    // Distance values are sqlite-vec's L2 (default for FLOAT[N]). Top-1 should
    // be ~0.0; second result should be small but non-zero.
    assert!(results[0].2 < 1e-5, "exact match distance: {}", results[0].2);
    assert!(results[1].2 > results[0].2, "second is farther");
}
```

- [ ] **Step 2: Run the test**

From `rust/`:

```bash
cargo test -p chunkshop-rs --test cross_language_sqlite_parity -- --nocapture 2>&1 | tail -25
```

Expected: PASS, or SKIPPING with a clear reason. If it FAILS with a Python error, check the worktree's Python venv has `chunkshop` + `sqlite_vec` installed.

- [ ] **Step 3: Run the full suite**

Run: `cargo test -p chunkshop-rs 2>&1 | grep "test result"`

Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/cross_language_sqlite_parity.rs
git commit -m "$(cat <<'EOF'
test(parity): cross-language SQLite round-trip (R3-SC-007)

Python writes 5 chunks with known orthogonal-ish vectors via uv run
python -c '...'; Rust opens the same .db and asserts query_top_k
returns the expected top-1 (exact match, distance < 1e-5) and ordering.
Skips with eprintln reason if uv or python sqlite_vec missing — does
not fail. Locks the brief's R3-SC-007 as an automated gate.
EOF
)"
```

---

## Phase F — Final

### Task 18: ⛔ DC-FINAL — Evidence cite + merge readiness

- [ ] **Step 1: Re-read the mission brief one final time**

Read every section. For each SC-001..SC-010, identify the test or build artifact that proves satisfaction.

- [ ] **Step 2: Run the full suite cleanly**

```bash
cd /home/yonk/yonk-tools/chunkshop-r3-sqlite/rust
cargo test -p chunkshop-rs 2>&1 | tail -50
```

Expected: every test result line is `0 failed`. Ignored count should be ≤ 1 (the pre-existing pipeline doctest).

- [ ] **Step 3: Cite evidence for each SC**

Print or paste-into-PR-body the following table, with the actual test names that passed:

```
SC-001  SQLiteBackend BackendDialect impl + inherent conn methods
        → cargo build clean; tests/backend_sqlite_conn.rs (6 tests)
SC-002  create_table builds both tables across all 3 modes
        → tests/sqlite_sink_create_table.rs (4 tests)
SC-003  write_document upserts main + DELETE+INSERT vec in same tx
        → tests/sqlite_sink_two_table_dance.rs (3 tests)
SC-004  query_top_k vec0 MATCH JOIN
        → tests/sqlite_sink_query_top_k.rs (1 test)
SC-005  HNSW once-warning per process
        → tests/sqlite_sink_modes.rs::hnsw_emits_one_warning_per_process
SC-006  append preflight: missing table / missing _vec / dim mismatch
        → tests/sqlite_sink_modes.rs (3 tests)
SC-007  cross-language round-trip
        → tests/cross_language_sqlite_parity.rs (1 test, may skip)
SC-008  dialect parity fixture
        → tests/dialect_sqlite_parity.rs (8 tests)
SC-009  no R1 baseline regressions
        → cargo test -p chunkshop-rs total ≥ baseline + new
SC-010  delete_document both tables, source_tag scope
        → tests/sqlite_sink_delete_document.rs (3 tests)
```

For each row, run the cited test alone and paste the result, e.g.:

```
$ cargo test -p chunkshop-rs --test sqlite_sink_create_table 2>&1 | grep "test result"
test result: ok. 4 passed; 0 failed; 0 ignored
```

If any cited test fails or doesn't exist, R3 is not done.

- [ ] **Step 4: Confirm Out-of-Scope was not violated**

```bash
cd /home/yonk/yonk-tools/chunkshop-r3-sqlite
git diff --stat experimental/v4-modular-backends..HEAD -- \
    rust/chunkshop/src/backends/base.rs \
    rust/chunkshop/src/backends/postgres.rs \
    rust/chunkshop/src/sinks/pg.rs \
    rust/chunkshop/src/sources/pg_table.rs \
    python/
```

Expected: empty. Any non-empty output means a brief violation was committed — investigate and either revert or escalate.

- [ ] **Step 5: Inspect the branch**

```bash
git log --oneline experimental/v4-modular-backends..HEAD
```

Expected: 14–17 commits, all `type(scope): subject` form, all clean.

```bash
git status
```

Expected: working tree clean.

- [ ] **Step 6: Final summary commit (optional, for handover)**

If desired, add a final docs commit summarizing what merged:

```bash
git commit --allow-empty -m "$(cat <<'EOF'
docs(handover): R3 SQLite backend ready for merge into v4-modular-backends

R3-SC-001..010 all satisfied with cited test evidence (see commit log).
Branch ready for `git merge --no-ff experimental/v4-rust-sqlite` from
experimental/v4-modular-backends. Mirrors R1's 13cac8b merge shape.
Wave 2 continues — R4 (ClickHouse) is parallel-safe.
EOF
)"
```

(This is optional — the merge target maintainer may prefer a clean log.)

---

## Self-Review Notes (author's own check)

**Spec coverage:**
- SC-001 → Tasks 4 + 7 (dialect + conn methods)
- SC-002 → Tasks 8 + 9 (create_table all 3 modes, both tables)
- SC-003 → Task 10 (two-table dance)
- SC-004 → Task 12 (query_top_k)
- SC-005 → Task 8 implements; Task 9 covers test (`hnsw_emits_one_warning_per_process`)
- SC-006 → Task 9 (append preflight)
- SC-007 → Task 17 (cross-language)
- SC-008 → Task 5 (dialect parity fixture)
- SC-009 → DC checkpoints + final sweep
- SC-010 → Task 11 (delete_document)

All 10 SCs have at least one task. All 4 DCs are present (Tasks 6, 13, 16, 18).

**Type consistency:**
- `SQLiteBackend` (capitalized like Python class) used everywhere — verified.
- `SqliteSink`, `SqliteTableSource` use Rust-camelcase; matches PostgresBackend / PgSink / PgTableSource convention from R1.
- `SqliteConn` type alias = `Arc<Mutex<rusqlite::Connection>>` — used in every connection-method signature.

**Known plan caveats the engineer should watch for:**
- **Test env-var typos:** the test files in Tasks 9 and 11 use placeholder `"X"` strings as `dsn_env` arg in some `cfg(...)` calls — those are typos. Standardize on `&env` (the unique-per-test variable) everywhere. Search for `cfg(/*ignored arg*/"X"` and fix.
- **Raw-pointer pattern in Task 8:** the first draft of `create_table` shows a raw-pointer workaround. Replace with `#[derive(Clone)]` on `SQLiteBackend` + `SqliteSink` and `let this = self.clone();` move pattern. The narrative of Task 8 explicitly recommends this.
- **`Chunk.seq_num` type:** the plan uses `i32` based on common Rust convention. Confirm by reading `rust/chunkshop/src/chunker.rs` — adjust casts in `delete_orphans` if it's `usize` or `i64`.
- **`sqlite-vec` crate API drift:** Task 1 has an explicit step to verify the crate's current API. If `sqlite3_vec_init` has been renamed, the rest of the plan is shaped around that symbol — adjust accordingly.
- **`tracing-test::traced_test` requires `tracing-test` >= 0.2 with `no-env-filter` feature** (already in `Cargo.toml` per the existing dev-dep). Don't add a new feature.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-r3-rust-sqlite.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
