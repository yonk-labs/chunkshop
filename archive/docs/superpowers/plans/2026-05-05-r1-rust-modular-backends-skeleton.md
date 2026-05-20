# R1 — Rust Modular Backends Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Rust crate's PG-only sink/source code into the modular `backends/` + `sinks/` + `sources/` shape that mirrors the v4 Python layout. **No behavior change.** Postgres-only at the end of R1; R2/R3/R4 plug new backend impls into the trait surface this sub-project produces.

**Architecture:** Two-trait split per backend (sync `BackendDialect` for pure helpers + async `BackendConn` for I/O), AFIT (Rust ≥1.75) with generic dispatch (no `async-trait`, no `dyn`). `Sink` trait is 5 methods mirroring Python's Protocol. Runtime polymorphism uses sum-type enums (`AnyBackend`, `AnySink`, `AnySource`); only `AnySink` needs trait-impl boilerplate (Pipeline holds it). Identifier safety = regex allowlist (config-load) + `quote_ident` doubling embedded `"`.

**Tech Stack:** Rust 1.93 (workspace edition), `sqlx` 0.8 (PG only in R1), `serde` + `serde_yml`, `anyhow`, `tokio`. Existing crate at `rust/chunkshop/`. Worktree: `/home/yonk/yonk-tools/chunkshop-rust-skeleton` on branch `experimental/v4-rust-backends-skeleton`.

**Spec:** [`docs/superpowers/specs/2026-05-05-r1-rust-modular-backends-skeleton-design.md`](../specs/2026-05-05-r1-rust-modular-backends-skeleton-design.md)

**Working directory for all commands:** `/home/yonk/yonk-tools/chunkshop-rust-skeleton`. Use `cargo` from the project root; the workspace at `rust/Cargo.toml` resolves the right crate. To run a specific test: `cargo test -p chunkshop <test_name>`.

**Commit style:** `type(scope): subject` — match existing repo convention. Types: `feat`, `chore`, `refactor`, `docs`, `test`. Common scopes: `backends`, `sinks`, `sources`, `config`, `samples`, `runner`, `pipeline`, `lib`, `parity`.

---

## Phase A — Trait skeleton (compile-clean placeholders, no impls yet)

### Task 1: Create `backends/base.rs` + `backends/mod.rs` with trait declarations

**Files:**
- Create: `rust/chunkshop/src/backends/base.rs`
- Create: `rust/chunkshop/src/backends/mod.rs`
- Modify: `rust/chunkshop/src/lib.rs` (add `pub mod backends;`)

- [ ] **Step 1: Create `rust/chunkshop/src/backends/base.rs` with the trait declarations**

```rust
//! Backend traits + ColSpec.
//!
//! Backends own everything that MUST be different per backend, including DDL
//! sequencing. Sinks own chunkshop-specific data-model semantics (modes,
//! metadata promotion, delete_orphans, source-tag write-once).
//!
//! Two traits:
//! - `BackendDialect` — pure helpers, no I/O, no async. Returns String / Vec<String>.
//!   Trivially unit-testable without a tokio runtime.
//! - `BackendConn` — I/O surface. AFIT (Rust ≥1.75 stable). No `async-trait` macro,
//!   no `dyn`. Generic dispatch via `<B: Backend>`.
//!
//! R1 caveat (deliberate seam): `BackendConn` methods take a PG-concrete
//! `&mut sqlx::Transaction<'_, sqlx::Postgres>`. R2 (MariaDB) introduces the GAT
//! or executor abstraction that makes this truly cross-backend, because the
//! right shape can only be designed with a second concrete impl in hand.

use std::future::Future;

#[derive(Debug, Clone)]
pub struct ColSpec {
    pub name: &'static str,
    pub type_ddl: String,
    pub nullable: bool,
    pub default: Option<&'static str>,
    pub is_primary_key: bool,
}

/// Pure dialect helpers. No I/O, no async.
pub trait BackendDialect {
    const NAME: &'static str;
    const SUPPORTS_UPSERT: bool;

    fn quote_ident(&self, name: &str) -> String;
    fn fq_table(&self, db: &str, table: &str) -> String;

    fn vector_type_ddl(&self, dim: usize) -> String;
    fn json_type_ddl(&self) -> String;
    fn tags_array_type_ddl(&self) -> String;
    fn text_pk_type_ddl(&self) -> String;
    fn timestamp_now_default_ddl(&self) -> String;

    fn vector_literal(&self, arr: &[f32]) -> String;
    fn json_literal(&self, obj: &serde_json::Value) -> String;

    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String;
    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String;

    fn create_database_sql(&self, name: &str) -> String;
    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String;
    fn drop_table_sql(&self, fq: &str) -> String;

    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        dim: usize,
        engine: Option<&str>,
    ) -> Vec<String>;
}

/// I/O surface. R1 PG-concrete; R2 introduces the GAT/executor abstraction.
pub trait BackendConn {
    fn connect(&self) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn acquire_create_lock(
        &self,
        tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
        key: &str,
    ) -> impl Future<Output = anyhow::Result<()>> + Send;

    fn table_exists(
        &self,
        tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<bool>> + Send;

    fn embedding_dim(
        &self,
        tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = anyhow::Result<Option<usize>>> + Send;
}

/// Convenience super-trait: `<B: Backend>` for ergonomic generic bounds.
pub trait Backend: BackendDialect + BackendConn {}
impl<T: BackendDialect + BackendConn> Backend for T {}
```

- [ ] **Step 2: Create `rust/chunkshop/src/backends/mod.rs`**

```rust
//! Backend module — connection management + dialect helpers per DB engine.
//!
//! AnyBackend is a TRANSPORT sum type — used by the loader to hand a backend
//! to load_sink, where it's pattern-matched back to a concrete type. Sinks
//! store concrete backends (PgSink holds PostgresBackend), not AnyBackend.
//! So this enum does NOT impl Backend / BackendDialect / BackendConn — no
//! match-delegate boilerplate.

pub mod base;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};

// load_backend factory + AnyBackend enum land in Phase F (Task 22) once
// PostgresBackend is implemented.
```

- [ ] **Step 3: Add `pub mod backends;` to `rust/chunkshop/src/lib.rs`**

In `rust/chunkshop/src/lib.rs`, add the line `pub mod backends;` to the module declarations (alongside `pub mod chunker;`, `pub mod config;`, etc.). Place it after `pub mod bakeoff;` to keep alphabetical order.

- [ ] **Step 4: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build, no errors, no warnings introduced.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/ rust/chunkshop/src/lib.rs
git commit -m "feat(backends): scaffold BackendDialect + BackendConn trait declarations"
```

---

### Task 2: Create `sinks/base.rs` + `sinks/mod.rs` with `Sink` trait

**Files:**
- Create: `rust/chunkshop/src/sinks/base.rs`
- Create: `rust/chunkshop/src/sinks/mod.rs`
- Modify: `rust/chunkshop/src/lib.rs` (add `pub mod sinks;`)

- [ ] **Step 1: Create `rust/chunkshop/src/sinks/base.rs`**

```rust
//! Sink trait — chunkshop's data-model semantics on a backend.
//!
//! Mirrors `python/src/chunkshop/sinks/base.py` Sink Protocol: 5 methods,
//! one per concern. Per-backend impls (PgSink, MariadbSink, etc.) own mode
//! dispatch (overwrite/append/create_if_missing), foreign-tag safety,
//! append preflight, source write-once on UPDATE, delete_orphans behavior,
//! and the canonical chunks-table column list.

use std::future::Future;

use anyhow::Result;

use crate::chunker::Chunk;

pub trait Sink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send;

    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send;

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send;

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send;

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send;
}
```

- [ ] **Step 2: Create `rust/chunkshop/src/sinks/mod.rs`**

```rust
//! Sinks — chunkshop's per-backend data-model semantics layer.

pub mod base;

pub use base::Sink;

// AnySink + load_sink factory land in Phase F (Task 23) once PgSink is implemented.
```

- [ ] **Step 3: Add `pub mod sinks;` to `rust/chunkshop/src/lib.rs`**

In `rust/chunkshop/src/lib.rs`, add `pub mod sinks;` alongside the other module declarations (after `pub mod backends;`).

- [ ] **Step 4: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build, no errors.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sinks/ rust/chunkshop/src/lib.rs
git commit -m "feat(sinks): scaffold Sink trait declaration"
```

---

### Task 3: Create `sources/base.rs` + `sources/mod.rs` with `Document` struct

**Files:**
- Create: `rust/chunkshop/src/sources/base.rs`
- Create: `rust/chunkshop/src/sources/mod.rs`
- Modify: `rust/chunkshop/src/source.rs` (re-export `Document` from new location for transition)
- Modify: `rust/chunkshop/src/lib.rs` (add `pub mod sources;`)

- [ ] **Step 1: Create `rust/chunkshop/src/sources/base.rs`**

```rust
//! Source-side shared types. `Document` is the unit yielded by every source.
//!
//! Mirrors `python/src/chunkshop/sources/base.py`. Per-source impls live in
//! sibling files (files.rs, json_corpus.rs, pg_table.rs, http.rs, s3.rs).

#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub content: String,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
}
```

- [ ] **Step 2: Create `rust/chunkshop/src/sources/mod.rs`**

```rust
//! Sources — input document iterators per backing store.

pub mod base;

pub use base::Document;

// Per-source modules + AnySource + load_source factory land in Phase E/F.
```

- [ ] **Step 3: Update `rust/chunkshop/src/source.rs` to re-export `Document` from the new location**

Replace the existing `Document` struct definition (lines 14-21 of `source.rs`) with a re-export so existing source code keeps working during transition:

Find this block in `source.rs`:
```rust
/// Analogue of Python's `sources.base.Document`.
#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub content: String,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
}
```

Replace with:
```rust
// `Document` lives in `sources::base::Document` as of v4.0. Re-exported here
// during the R1 transition; this re-export is removed when source.rs is
// deleted (Phase G).
pub use crate::sources::base::Document;
```

- [ ] **Step 4: Add `pub mod sources;` to `rust/chunkshop/src/lib.rs`**

In `rust/chunkshop/src/lib.rs`, add `pub mod sources;` alongside other module declarations.

- [ ] **Step 5: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build. The re-export means any caller of `crate::source::Document` still resolves correctly.

- [ ] **Step 6: Run the existing source tests to verify nothing broke**

Run: `cargo test -p chunkshop --test json_corpus_source --test http_source --test s3_source -- --nocapture`
Expected: all pass (or skip-if-no-DSN for those that need it).

- [ ] **Step 7: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs rust/chunkshop/src/lib.rs
git commit -m "feat(sources): scaffold Document in sources/base.rs; re-export from source.rs"
```

---

## Phase B — Implement `PostgresBackend` (TDD on each method group)

### Task 4: `PostgresBackend` struct + identifier-safety methods (TDD)

**Files:**
- Create: `rust/chunkshop/src/backends/postgres.rs`
- Modify: `rust/chunkshop/src/backends/mod.rs` (add `pub mod postgres;` + re-export)

- [ ] **Step 1: Write failing unit tests for `quote_ident` and `fq_table` in a new file**

Create `rust/chunkshop/src/backends/postgres.rs` with this initial content (struct skeleton + tests, no method bodies yet):

```rust
//! Postgres backend — sqlx-based connection pool + dialect helpers.
//!
//! Mirrors `python/src/chunkshop/backends/postgres.py`. Identifier safety
//! is two-layer: regex allowlist enforced at config-load (in config.rs)
//! plus quote-doubling here (defense-in-depth — even if the regex were
//! widened, embedded `"` characters can't break out).

use std::future::Future;

use anyhow::{anyhow, Context, Result};
use sqlx::{postgres::PgPoolOptions, PgPool, Postgres, Row, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};

pub struct PostgresBackend {
    dsn_env: String,
    pool: tokio::sync::OnceCell<PgPool>,
}

impl PostgresBackend {
    pub fn new(dsn_env: String) -> Self {
        Self {
            dsn_env,
            pool: tokio::sync::OnceCell::new(),
        }
    }

    /// Lazily-initialized pool. Idempotent.
    pub async fn pool(&self) -> Result<&PgPool> {
        self.pool
            .get_or_try_init(|| async {
                let dsn = std::env::var(&self.dsn_env).with_context(|| {
                    format!("DSN env var {} not set", self.dsn_env)
                })?;
                PgPoolOptions::new()
                    .max_connections(1)
                    .connect(&dsn)
                    .await
                    .with_context(|| format!("connecting to {}", self.dsn_env))
            })
            .await
    }
}

impl BackendDialect for PostgresBackend {
    const NAME: &'static str = "postgres";
    const SUPPORTS_UPSERT: bool = true;

    fn quote_ident(&self, name: &str) -> String {
        // Defense-in-depth: even with the regex allowlist at config-load
        // refusing characters outside [a-z0-9_], we still double-quote any
        // embedded `"` in case the regex is ever widened.
        format!("\"{}\"", name.replace('"', "\"\""))
    }

    fn fq_table(&self, db: &str, table: &str) -> String {
        format!("{}.{}", self.quote_ident(db), self.quote_ident(table))
    }

    // --- remaining methods land in Tasks 5–9. Stubs below to keep crate compiling. ---

    fn vector_type_ddl(&self, _dim: usize) -> String { unimplemented!("Task 5") }
    fn json_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn tags_array_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn text_pk_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn timestamp_now_default_ddl(&self) -> String { unimplemented!("Task 5") }
    fn vector_literal(&self, _arr: &[f32]) -> String { unimplemented!("Task 6") }
    fn json_literal(&self, _obj: &serde_json::Value) -> String { unimplemented!("Task 6") }
    fn json_path_sql(&self, _col_expr: &str, _dotted_path: &str) -> String { unimplemented!("Task 7") }
    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String { unimplemented!("Task 7") }
    fn create_database_sql(&self, _name: &str) -> String { unimplemented!("Task 8") }
    fn add_column_if_not_exists_sql(&self, _fq: &str, _col: &str, _type_ddl: &str) -> String { unimplemented!("Task 8") }
    fn drop_table_sql(&self, _fq: &str) -> String { unimplemented!("Task 8") }
    fn emit_chunks_table_ddl(
        &self, _fq: &str, _cols: &[ColSpec], _hnsw: bool, _dim: usize, _engine: Option<&str>,
    ) -> Vec<String> { unimplemented!("Task 9") }
}

impl BackendConn for PostgresBackend {
    fn connect(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let _ = self.pool().await?;
            Ok(())
        }
    }

    fn acquire_create_lock(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _key: &str,
    ) -> impl Future<Output = Result<()>> + Send {
        async move { unimplemented!("Task 10") }
    }

    fn table_exists(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _db: &str,
        _table: &str,
    ) -> impl Future<Output = Result<bool>> + Send {
        async move { unimplemented!("Task 10") }
    }

    fn embedding_dim(
        &self,
        _tx: &mut Transaction<'_, Postgres>,
        _db: &str,
        _table: &str,
    ) -> impl Future<Output = Result<Option<usize>>> + Send {
        async move { unimplemented!("Task 10") }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn backend() -> PostgresBackend {
        PostgresBackend::new("UNUSED_FOR_DIALECT_TESTS".to_string())
    }

    #[test]
    fn quote_ident_wraps_in_double_quotes() {
        let b = backend();
        assert_eq!(b.quote_ident("my_table"), "\"my_table\"");
    }

    #[test]
    fn quote_ident_doubles_embedded_double_quote() {
        let b = backend();
        // Defense-in-depth: even though the config-load regex disallows `"`,
        // we still escape it here.
        assert_eq!(b.quote_ident("a\"b"), "\"a\"\"b\"");
    }

    #[test]
    fn fq_table_quotes_both_segments() {
        let b = backend();
        assert_eq!(b.fq_table("my_db", "my_table"), "\"my_db\".\"my_table\"");
    }
}
```

- [ ] **Step 2: Add `pub mod postgres;` and re-export to `rust/chunkshop/src/backends/mod.rs`**

Replace `rust/chunkshop/src/backends/mod.rs` with:

```rust
//! Backend module — connection management + dialect helpers per DB engine.

pub mod base;
pub mod postgres;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;

// AnyBackend + load_backend factory land in Phase F (Task 22).
```

- [ ] **Step 3: Run the new tests and verify they pass**

Run: `cargo test -p chunkshop --lib backends::postgres -- --nocapture`
Expected: 3 tests pass (`quote_ident_wraps_in_double_quotes`, `quote_ident_doubles_embedded_double_quote`, `fq_table_quotes_both_segments`).

- [ ] **Step 4: Verify the rest of the crate still compiles (the `unimplemented!()` stubs are fine until called)**

Run: `cargo build -p chunkshop`
Expected: clean build. (Existing tests that don't touch PostgresBackend still pass; the stubs panic only if called, which they aren't yet.)

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/
git commit -m "feat(backends): PostgresBackend skeleton + identifier-safety methods"
```

---

### Task 5: Type DDL fragment methods on `PostgresBackend` (TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Add failing tests for the 5 type-DDL methods to the `tests` mod**

Inside the `mod tests` block in `rust/chunkshop/src/backends/postgres.rs`, append:

```rust
    #[test]
    fn vector_type_ddl() {
        let b = backend();
        assert_eq!(b.vector_type_ddl(384), "vector(384)");
        assert_eq!(b.vector_type_ddl(1024), "vector(1024)");
    }

    #[test]
    fn json_type_ddl_is_jsonb() {
        let b = backend();
        assert_eq!(b.json_type_ddl(), "jsonb");
    }

    #[test]
    fn tags_array_type_ddl_is_text_array() {
        let b = backend();
        assert_eq!(b.tags_array_type_ddl(), "text[]");
    }

    #[test]
    fn text_pk_type_ddl_is_text() {
        let b = backend();
        assert_eq!(b.text_pk_type_ddl(), "text");
    }

    #[test]
    fn timestamp_now_default_ddl() {
        let b = backend();
        assert_eq!(
            b.timestamp_now_default_ddl(),
            "timestamptz NOT NULL DEFAULT now()"
        );
    }
```

- [ ] **Step 2: Run the new tests to verify they fail (panic on `unimplemented!`)**

Run: `cargo test -p chunkshop --lib backends::postgres::tests::vector_type_ddl 2>&1 | head -20`
Expected: FAIL — panic message `not implemented: Task 5`.

- [ ] **Step 3: Replace the type-DDL `unimplemented!()` stubs with real implementations**

In the `impl BackendDialect for PostgresBackend` block, replace these stub methods:

```rust
    fn vector_type_ddl(&self, _dim: usize) -> String { unimplemented!("Task 5") }
    fn json_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn tags_array_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn text_pk_type_ddl(&self) -> String { unimplemented!("Task 5") }
    fn timestamp_now_default_ddl(&self) -> String { unimplemented!("Task 5") }
```

with:

```rust
    fn vector_type_ddl(&self, dim: usize) -> String {
        format!("vector({dim})")
    }
    fn json_type_ddl(&self) -> String {
        "jsonb".to_string()
    }
    fn tags_array_type_ddl(&self) -> String {
        "text[]".to_string()
    }
    fn text_pk_type_ddl(&self) -> String {
        "text".to_string()
    }
    fn timestamp_now_default_ddl(&self) -> String {
        "timestamptz NOT NULL DEFAULT now()".to_string()
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cargo test -p chunkshop --lib backends::postgres::tests`
Expected: 8 tests pass (3 from Task 4 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs
git commit -m "feat(backends): PostgresBackend type DDL fragments"
```

---

### Task 6: Value-literal methods on `PostgresBackend` (TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Add failing tests for `vector_literal` and `json_literal`**

Append inside `mod tests`:

```rust
    #[test]
    fn vector_literal_format_matches_python() {
        let b = backend();
        // Mirrors Python's PostgresBackend.vector_literal:
        //   "[" + ",".join(f"{x:.6f}" for x in arr) + "]"
        let v = vec![0.1_f32, 0.2_f32, -0.3_f32];
        let lit = b.vector_literal(&v);
        assert_eq!(lit, "[0.100000,0.200000,-0.300000]");
    }

    #[test]
    fn vector_literal_empty() {
        let b = backend();
        assert_eq!(b.vector_literal(&[]), "[]");
    }

    #[test]
    fn json_literal_canonical_form() {
        let b = backend();
        let v = serde_json::json!({"k": "v", "n": 1});
        let lit = b.json_literal(&v);
        // Order is implementation-defined; assert structure via re-parse.
        let reparsed: serde_json::Value = serde_json::from_str(&lit).unwrap();
        assert_eq!(reparsed["k"], "v");
        assert_eq!(reparsed["n"], 1);
    }
```

- [ ] **Step 2: Run tests and verify they fail with `unimplemented!`**

Run: `cargo test -p chunkshop --lib backends::postgres::tests::vector_literal_format_matches_python 2>&1 | head -10`
Expected: FAIL with `not implemented: Task 6`.

- [ ] **Step 3: Replace the value-literal stubs with real implementations**

Replace:

```rust
    fn vector_literal(&self, _arr: &[f32]) -> String { unimplemented!("Task 6") }
    fn json_literal(&self, _obj: &serde_json::Value) -> String { unimplemented!("Task 6") }
```

with:

```rust
    fn vector_literal(&self, arr: &[f32]) -> String {
        let parts: Vec<String> = arr.iter().map(|x| format!("{x:.6}")).collect();
        format!("[{}]", parts.join(","))
    }

    fn json_literal(&self, obj: &serde_json::Value) -> String {
        serde_json::to_string(obj).unwrap_or_else(|_| "null".to_string())
    }
```

- [ ] **Step 4: Run tests and verify all pass**

Run: `cargo test -p chunkshop --lib backends::postgres::tests`
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs
git commit -m "feat(backends): PostgresBackend value literals (vector + json)"
```

---

### Task 7: SQL composition methods (`json_path_sql`, `upsert_clause`) on `PostgresBackend` (TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Add failing tests**

Append inside `mod tests`:

```rust
    #[test]
    fn json_path_sql_single_segment() {
        let b = backend();
        // Mirrors Python's PostgresBackend.json_path_sql for path "a"
        assert_eq!(b.json_path_sql("metadata", "a"), "metadata->>'a'");
    }

    #[test]
    fn json_path_sql_two_segments() {
        let b = backend();
        // Path "a.b" → metadata->'a'->>'b'
        assert_eq!(b.json_path_sql("metadata", "a.b"), "metadata->'a'->>'b'");
    }

    #[test]
    fn json_path_sql_three_segments() {
        let b = backend();
        // Path "a.b.c" → metadata->'a'->'b'->>'c'
        assert_eq!(
            b.json_path_sql("metadata", "a.b.c"),
            "metadata->'a'->'b'->>'c'"
        );
    }

    #[test]
    fn upsert_clause_do_nothing_when_no_update_cols() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &[]);
        assert_eq!(sql, "ON CONFLICT (\"id\") DO NOTHING");
    }

    #[test]
    fn upsert_clause_do_update_set() {
        let b = backend();
        let sql = b.upsert_clause(&["id"], &["content", "metadata"]);
        assert_eq!(
            sql,
            "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = EXCLUDED.\"content\", \
             \"metadata\" = EXCLUDED.\"metadata\""
        );
    }

    #[test]
    fn upsert_clause_composite_key() {
        let b = backend();
        let sql = b.upsert_clause(&["a", "b"], &["c"]);
        assert_eq!(
            sql,
            "ON CONFLICT (\"a\", \"b\") DO UPDATE SET \"c\" = EXCLUDED.\"c\""
        );
    }
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cargo test -p chunkshop --lib backends::postgres::tests::json_path_sql_single_segment 2>&1 | head -10`
Expected: FAIL with `not implemented: Task 7`.

- [ ] **Step 3: Replace the stubs with implementations**

Replace:

```rust
    fn json_path_sql(&self, _col_expr: &str, _dotted_path: &str) -> String { unimplemented!("Task 7") }
    fn upsert_clause(&self, _key_cols: &[&str], _update_cols: &[&str]) -> String { unimplemented!("Task 7") }
```

with:

```rust
    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String {
        let segs: Vec<&str> = dotted_path.split('.').collect();
        if segs.len() == 1 {
            return format!("{col_expr}->>'{}'", segs[0]);
        }
        // Build the chain: col->'a'->'b'->>'c' (last hop uses ->>)
        let mut s = String::from(col_expr);
        for seg in &segs[..segs.len() - 1] {
            s.push_str(&format!("->'{seg}'"));
        }
        s.push_str(&format!("->>'{}'", segs[segs.len() - 1]));
        s
    }

    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String {
        let keys: Vec<String> = key_cols.iter().map(|c| self.quote_ident(c)).collect();
        let keys_sql = keys.join(", ");
        if update_cols.is_empty() {
            return format!("ON CONFLICT ({keys_sql}) DO NOTHING");
        }
        let sets: Vec<String> = update_cols
            .iter()
            .map(|c| format!("{q} = EXCLUDED.{q}", q = self.quote_ident(c)))
            .collect();
        format!("ON CONFLICT ({keys_sql}) DO UPDATE SET {}", sets.join(", "))
    }
```

- [ ] **Step 4: Run tests and verify all pass**

Run: `cargo test -p chunkshop --lib backends::postgres::tests`
Expected: 17 tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs
git commit -m "feat(backends): PostgresBackend SQL composition (json_path_sql + upsert_clause)"
```

---

### Task 8: DDL primitives (`create_database_sql`, `add_column_if_not_exists_sql`, `drop_table_sql`) (TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Add failing tests**

Append inside `mod tests`:

```rust
    #[test]
    fn create_database_sql_uses_schema_for_postgres() {
        let b = backend();
        // PG implements `database` via CREATE SCHEMA (per spec §5).
        assert_eq!(
            b.create_database_sql("chunkshop"),
            "CREATE SCHEMA IF NOT EXISTS \"chunkshop\""
        );
    }

    #[test]
    fn add_column_if_not_exists_sql_format() {
        let b = backend();
        let sql = b.add_column_if_not_exists_sql("\"db\".\"t\"", "source", "text");
        assert_eq!(
            sql,
            "ALTER TABLE \"db\".\"t\" ADD COLUMN IF NOT EXISTS \"source\" text"
        );
    }

    #[test]
    fn drop_table_sql_format() {
        let b = backend();
        assert_eq!(b.drop_table_sql("\"db\".\"t\""), "DROP TABLE \"db\".\"t\"");
    }
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cargo test -p chunkshop --lib backends::postgres::tests::drop_table_sql_format 2>&1 | head -10`
Expected: FAIL with `not implemented: Task 8`.

- [ ] **Step 3: Replace stubs with implementations**

Replace:

```rust
    fn create_database_sql(&self, _name: &str) -> String { unimplemented!("Task 8") }
    fn add_column_if_not_exists_sql(&self, _fq: &str, _col: &str, _type_ddl: &str) -> String { unimplemented!("Task 8") }
    fn drop_table_sql(&self, _fq: &str) -> String { unimplemented!("Task 8") }
```

with:

```rust
    fn create_database_sql(&self, name: &str) -> String {
        format!("CREATE SCHEMA IF NOT EXISTS {}", self.quote_ident(name))
    }

    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String {
        format!(
            "ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {} {type_ddl}",
            self.quote_ident(col)
        )
    }

    fn drop_table_sql(&self, fq: &str) -> String {
        format!("DROP TABLE {fq}")
    }
```

- [ ] **Step 4: Run tests and verify all pass**

Run: `cargo test -p chunkshop --lib backends::postgres::tests`
Expected: 20 tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs
git commit -m "feat(backends): PostgresBackend DDL primitives"
```

---

### Task 9: `emit_chunks_table_ddl` composite DDL (TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Add failing tests**

Append inside `mod tests`:

```rust
    fn canonical_cols(dim: usize) -> Vec<ColSpec> {
        vec![
            ColSpec { name: "id", type_ddl: "text".into(), nullable: false, default: None, is_primary_key: true },
            ColSpec { name: "doc_id", type_ddl: "text".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "seq_num", type_ddl: "int".into(), nullable: false, default: None, is_primary_key: false },
            ColSpec { name: "embedding", type_ddl: format!("vector({dim})"), nullable: false, default: None, is_primary_key: false },
        ]
    }

    #[test]
    fn emit_chunks_table_ddl_no_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("\"db\".\"t\"", &cols, false, 384, None);
        // Expected: CREATE TABLE + 1 doc_seq index. No HNSW index.
        assert_eq!(stmts.len(), 2);
        assert!(stmts[0].starts_with("CREATE TABLE IF NOT EXISTS \"db\".\"t\""));
        assert!(stmts[0].contains("\"id\" text NOT NULL"));
        assert!(stmts[0].contains("PRIMARY KEY (\"id\")"));
        assert!(stmts[1].contains("CREATE INDEX IF NOT EXISTS \"t_doc_seq_idx\""));
        assert!(stmts[1].contains("ON \"db\".\"t\" (\"doc_id\", \"seq_num\")"));
    }

    #[test]
    fn emit_chunks_table_ddl_with_hnsw() {
        let b = backend();
        let cols = canonical_cols(384);
        let stmts = b.emit_chunks_table_ddl("\"db\".\"t\"", &cols, true, 384, None);
        // Expected: CREATE TABLE + doc_seq index + HNSW index.
        assert_eq!(stmts.len(), 3);
        assert!(stmts[2].contains("USING hnsw (\"embedding\" vector_cosine_ops)"));
        assert!(stmts[2].contains("\"t_emb_hnsw_idx\""));
    }
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cargo test -p chunkshop --lib backends::postgres::tests::emit_chunks_table_ddl_no_hnsw 2>&1 | head -10`
Expected: FAIL with `not implemented: Task 9`.

- [ ] **Step 3: Replace the stub with implementation**

Replace:

```rust
    fn emit_chunks_table_ddl(
        &self, _fq: &str, _cols: &[ColSpec], _hnsw: bool, _dim: usize, _engine: Option<&str>,
    ) -> Vec<String> { unimplemented!("Task 9") }
```

with:

```rust
    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        _dim: usize, // dim is encoded in the embedding column's type_ddl
        _engine: Option<&str>, // engine clause is not applicable on PG
    ) -> Vec<String> {
        let mut col_lines: Vec<String> = Vec::with_capacity(cols.len());
        let mut pk_cols: Vec<&str> = Vec::new();
        for c in cols {
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
        let create = format!("CREATE TABLE IF NOT EXISTS {fq} (\n{body}\n)");

        // Strip schema prefix from fq for index naming: "db"."t" → t
        let bare = fq
            .rsplit('.')
            .next()
            .unwrap_or(fq)
            .trim_matches('"')
            .to_string();

        let mut stmts = vec![create];
        stmts.push(format!(
            "CREATE INDEX IF NOT EXISTS {} ON {fq} (\"doc_id\", \"seq_num\")",
            self.quote_ident(&format!("{bare}_doc_seq_idx"))
        ));
        if hnsw {
            stmts.push(format!(
                "CREATE INDEX IF NOT EXISTS {} ON {fq} USING hnsw (\"embedding\" vector_cosine_ops)",
                self.quote_ident(&format!("{bare}_emb_hnsw_idx"))
            ));
        }
        stmts
    }
```

- [ ] **Step 4: Run tests and verify all pass**

Run: `cargo test -p chunkshop --lib backends::postgres::tests`
Expected: 22 tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs
git commit -m "feat(backends): PostgresBackend emit_chunks_table_ddl"
```

---

### Task 10: `BackendConn` I/O methods on `PostgresBackend` (integration TDD)

**Files:**
- Modify: `rust/chunkshop/src/backends/postgres.rs`

- [ ] **Step 1: Replace the `BackendConn` stubs with real implementations**

Replace the body of `impl BackendConn for PostgresBackend` (currently containing the `unimplemented!("Task 10")` stubs) with:

```rust
impl BackendConn for PostgresBackend {
    fn connect(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let _ = self.pool().await?;
            Ok(())
        }
    }

    fn acquire_create_lock(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        key: &str,
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
            // Deterministic 64-bit signed int from BLAKE2b-8 of the schema name.
            // Mirrors Python's PostgresBackend._advisory_lock_key.
            use blake2::{digest::consts::U8, Blake2b, Digest};
            let mut hasher = Blake2b::<U8>::new();
            hasher.update(key.as_bytes());
            let digest = hasher.finalize();
            let lock_key = i64::from_be_bytes(digest.into());
            sqlx::query("SELECT pg_advisory_xact_lock($1)")
                .bind(lock_key)
                .execute(&mut **tx)
                .await
                .with_context(|| format!("acquire advisory lock for {key}"))?;
            Ok(())
        }
    }

    fn table_exists(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<bool>> + Send {
        async move {
            let row = sqlx::query(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename=$2)",
            )
            .bind(db)
            .bind(table)
            .fetch_one(&mut **tx)
            .await?;
            Ok(row.get::<bool, _>(0))
        }
    }

    fn embedding_dim(
        &self,
        tx: &mut Transaction<'_, Postgres>,
        db: &str,
        table: &str,
    ) -> impl Future<Output = Result<Option<usize>>> + Send {
        async move {
            let row = sqlx::query(
                r#"
                SELECT format_type(atttypid, atttypmod) AS t
                FROM pg_attribute
                WHERE attrelid = (
                    SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = $1 AND n.nspname = $2
                ) AND attname = 'embedding'
                "#,
            )
            .bind(table)
            .bind(db)
            .fetch_optional(&mut **tx)
            .await?;
            let Some(r) = row else { return Ok(None) };
            let s: String = r.get("t");
            let re = regex::Regex::new(r"^vector\((\d+)\)$").unwrap();
            Ok(re
                .captures(&s)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok()))
        }
    }
}
```

- [ ] **Step 2: Add an integration test that exercises connect + acquire_create_lock + table_exists + embedding_dim**

Create `rust/chunkshop/tests/backend_postgres_conn.rs`:

```rust
//! BackendConn integration tests for PostgresBackend.
//!
//! Skips if `CHUNKSHOP_TEST_DSN` is unset (matches the rest of the integration
//! test suite's skip-if-no-DSN pattern).

use chunkshop::backends::{BackendConn, PostgresBackend};
use sqlx::postgres::PgPoolOptions;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn connect_lazy_pool_init() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    // Calling connect a second time is idempotent (pool already initialized).
    backend.connect().await?;
    Ok(())
}

#[tokio::test]
async fn acquire_create_lock_and_introspection() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    backend.connect().await?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(&std::env::var(DSN_ENV).unwrap())
        .await?;

    let mut tx = pool.begin().await?;
    backend.acquire_create_lock(&mut tx, "chunkshop_r1_test").await?;

    // Set up + tear down a synthetic schema/table to exercise table_exists +
    // embedding_dim.
    sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
        .execute(&mut *tx)
        .await?;
    sqlx::query(r#"CREATE SCHEMA IF NOT EXISTS "chunkshop_r1_test""#)
        .execute(&mut *tx)
        .await?;

    // table_exists = false initially
    let exists = backend
        .table_exists(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert!(!exists);

    sqlx::query(
        r#"CREATE TABLE "chunkshop_r1_test"."synthetic" (id text PRIMARY KEY, embedding vector(8))"#,
    )
    .execute(&mut *tx)
    .await?;

    let exists = backend
        .table_exists(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert!(exists);

    let dim = backend
        .embedding_dim(&mut tx, "chunkshop_r1_test", "synthetic")
        .await?;
    assert_eq!(dim, Some(8));

    // Cleanup
    sqlx::query(r#"DROP SCHEMA "chunkshop_r1_test" CASCADE"#)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
```

- [ ] **Step 3: Run the new integration test (skipped if DSN unset; pass if DSN set)**

Run: `cargo test -p chunkshop --test backend_postgres_conn -- --nocapture`
Expected: PASS if `CHUNKSHOP_TEST_DSN` is set; the test prints "skipping: CHUNKSHOP_TEST_DSN not set" otherwise.

- [ ] **Step 4: Verify the crate still compiles cleanly**

Run: `cargo build -p chunkshop`
Expected: clean build, no warnings.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/backends/postgres.rs rust/chunkshop/tests/backend_postgres_conn.rs
git commit -m "feat(backends): PostgresBackend BackendConn impl + integration test"
```

---

## Phase C — Config migration (legacy support FIRST so PgSink builds against final shape)

### Task 11: Migrate `TargetConfig` to discriminated enum + `database_name` rename

**Files:**
- Modify: `rust/chunkshop/src/config.rs` (replace `TargetConfig` struct with enum + extract `PostgresTargetConfig`)
- Modify: `rust/chunkshop/src/sink.rs` (callers of `cfg.schema_name` → `cfg.database_name`)
- Modify: `rust/chunkshop/src/runner.rs` (sink construction)
- Modify: `rust/chunkshop/src/pipeline.rs` (callers of `cfg.target.schema_name`)

This task touches existing callsites. Key observation: the existing `PgVectorSink` keeps working — it just gets handed a `PostgresTargetConfig` instead of the old flat `TargetConfig`.

- [ ] **Step 1: Replace `TargetConfig` struct with discriminated enum in `config.rs`**

In `rust/chunkshop/src/config.rs`, find this block (around lines 702-743):

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct TargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "schema")]
    pub schema_name: String,
    pub table: String,
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`. All three are
    /// implemented in Rust as of MB-3 (sink full-mode parity).
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// When true, after upserting chunks for a document, delete any rows for
    /// that document with `seq_num >= len(new_chunks)`. ...
    #[serde(default)]
    pub delete_orphans: bool,
}

impl TargetConfig {
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

Replace with:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    // R2/R3/R4 add: Mariadb, Sqlite, Clickhouse
}

impl TargetConfig {
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct PostgresTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. New configs
    /// should use `mode`. The legacy-form rejection in `load_config` (Task 13)
    /// flags top-level `target.overwrite: true` with a friendly error, but here
    /// we keep the field for internal compatibility within the v4.0 enum body.
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`. All three are
    /// implemented in Rust as of MB-3 (sink full-mode parity).
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// When true, after upserting chunks for a document, delete any rows for
    /// that document with `seq_num >= len(new_chunks)`. Default false to
    /// preserve historical behavior. See `docs/incremental.md`.
    #[serde(default)]
    pub delete_orphans: bool,
}

impl PostgresTargetConfig {
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

- [ ] **Step 2: Update `validate_ident` callsites in `load_config`**

In `rust/chunkshop/src/config.rs`, find the existing `load_config` function (around line 779). The current target-validation block looks like this:

```rust
    validate_ident(&cfg.target.schema_name, "target.schema")?;
    validate_ident(&cfg.target.table, "target.table")?;
    if let Some(tag) = &cfg.target.source_tag {
        validate_ident(tag, "target.source_tag")?;
    }
```

Replace those four lines with a match on the new enum:

```rust
    match &cfg.target {
        TargetConfig::Postgres(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
    }
```

The line `cfg.target.validate()?;` further down stays as-is — it now delegates through the new `impl TargetConfig::validate()` (which dispatches to the variant's `validate()`).

`PromoteColumn` validation runs inside its existing `serde::Deserialize` impl (no public `validate()` method); leave that untouched. No new loop needed.

- [ ] **Step 3: Update `sink.rs` (the existing `PgVectorSink`) to take `PostgresTargetConfig` instead of `TargetConfig`**

In `rust/chunkshop/src/sink.rs`:

Find:
```rust
use crate::config::{PromoteColumn, TargetConfig};
```
Replace with:
```rust
use crate::config::{PostgresTargetConfig, PromoteColumn};
```

Find:
```rust
pub struct PgVectorSink {
    cfg: TargetConfig,
    ...
}
```
Replace with:
```rust
pub struct PgVectorSink {
    cfg: PostgresTargetConfig,
    ...
}
```

Find:
```rust
    pub async fn connect(cfg: TargetConfig, embed_dim: usize) -> Result<Self> {
```
Replace with:
```rust
    pub async fn connect(cfg: PostgresTargetConfig, embed_dim: usize) -> Result<Self> {
```

Search for `self.cfg.schema_name` throughout `sink.rs` and replace each occurrence with `self.cfg.database_name`.

- [ ] **Step 4: Update `runner.rs` sink construction**

In `rust/chunkshop/src/runner.rs`, find:
```rust
    let sink = PgVectorSink::connect(cfg.target, embedder.dim()).await?;
```

Replace with:
```rust
    let TargetConfig::Postgres(target_cfg) = cfg.target else {
        unreachable!("R1 only ships TargetConfig::Postgres; R2/R3/R4 add variants");
    };
    let sink = PgVectorSink::connect(target_cfg, embedder.dim()).await?;
```

Add to the `use` block at the top of `runner.rs`: `use crate::config::TargetConfig;` (if not already imported).

- [ ] **Step 5: Update `pipeline.rs` sink construction and `cfg.target` accesses**

In `rust/chunkshop/src/pipeline.rs`:

At the top, add `TargetConfig` to the imports from `crate::config`:
```rust
use crate::config::{CellConfig, EmbedderConfig, SourceConfig, TargetConfig};
```

Find:
```rust
        let sink = PgVectorSink::connect(cfg.target.clone(), embedder.dim()).await?;
```
Replace with:
```rust
        let TargetConfig::Postgres(target_cfg) = cfg.target.clone() else {
            unreachable!("R1 only ships TargetConfig::Postgres; R2/R3/R4 add variants");
        };
        let sink = PgVectorSink::connect(target_cfg, embedder.dim()).await?;
```

Find `delete_document` method body. Replace:
```rust
    pub async fn delete_document(&self, doc_id: &str) -> Result<u64> {
        let pool = self.sink.pool();
        let target = &self.cfg.target;
        let fq = format!("\"{}\".\"{}\"", target.schema_name, target.table);
        let result = if let Some(tag) = &target.source_tag {
            ...
        };
        Ok(result.rows_affected())
    }
```

with (using a `match` on `TargetConfig::Postgres`):

```rust
    pub async fn delete_document(&self, doc_id: &str) -> Result<u64> {
        let pool = self.sink.pool();
        let TargetConfig::Postgres(target) = &self.cfg.target else {
            unreachable!("R1 only ships TargetConfig::Postgres");
        };
        let fq = format!("\"{}\".\"{}\"", target.database_name, target.table);
        let result = if let Some(tag) = &target.source_tag {
            let stmt = format!("DELETE FROM {tbl} WHERE doc_id = $1 AND source = $2", tbl = fq);
            sqlx::query(&stmt).bind(doc_id).bind(tag).execute(pool).await?
        } else {
            let stmt = format!("DELETE FROM {tbl} WHERE doc_id = $1", tbl = fq);
            sqlx::query(&stmt).bind(doc_id).execute(pool).await?
        };
        Ok(result.rows_affected())
    }
```

Find `sample_row` method body, apply the same `match` treatment to the `cfg.target` access:

```rust
    pub async fn sample_row(&self, doc_id: &str) -> Result<Option<(i32, String)>> {
        let TargetConfig::Postgres(target) = &self.cfg.target else {
            unreachable!("R1 only ships TargetConfig::Postgres");
        };
        let fq = format!("\"{}\".\"{}\"", target.database_name, target.table);
        let stmt = format!(
            "SELECT seq_num, left(original_content, 80) FROM {tbl} \
             WHERE doc_id = $1 ORDER BY seq_num LIMIT 1",
            tbl = fq
        );
        let row = sqlx::query(&stmt)
            .bind(doc_id)
            .fetch_optional(self.sink.pool())
            .await?;
        Ok(row.map(|r| (r.get::<i32, _>(0), r.get::<String, _>(1))))
    }
```

- [ ] **Step 6: Update any test files in `rust/chunkshop/tests/` that read `cfg.target.schema_name` directly**

Run: `grep -rn "schema_name\|cfg\.target\." rust/chunkshop/tests/ rust/chunkshop/src/`
For each match outside `sink.rs`, `runner.rs`, `pipeline.rs` (which we already updated), apply the same `match` pattern to extract `target` from `TargetConfig::Postgres(t)`.

- [ ] **Step 7: Run `cargo check` to find any remaining callsites**

Run: `cargo check -p chunkshop 2>&1 | head -60`
Expected: clean (or one or two more callsites to fix following the same pattern).

- [ ] **Step 8: Commit**

```bash
git add rust/chunkshop/src/config.rs rust/chunkshop/src/sink.rs rust/chunkshop/src/runner.rs rust/chunkshop/src/pipeline.rs rust/chunkshop/tests/
git commit -m "refactor(config): TargetConfig becomes discriminated enum; rename schema → database"
```

---

### Task 12: Update existing test YAML fixtures + sample YAMLs to v0.4.0 shape

**Files:**
- Modify: `docs/samples/sample.yaml`
- Modify: `docs/samples/sample-sentence-aware.yaml`
- Modify: `docs/samples/sample-neighbor-expand.yaml`
- Modify: `docs/samples/sample-multi-source.yaml`
- Modify: any test files that contain inline YAML literals with `target:` blocks

The change: every `target:` block needs `type: postgres` added, and `schema:` renamed to `database:`.

- [ ] **Step 1: Find all YAML files (sample + test fixtures) that need updating**

Run: `grep -rln "schema:" docs/samples/ rust/chunkshop/tests/ python/ 2>/dev/null | xargs grep -l "target:" 2>/dev/null`
(Note: also run `grep -rln "target:" rust/chunkshop/src/configs/ rust/chunkshop/src/**/configs*` if any internal config dirs exist — for the rust crate the bakeoff configs may live inside the package.)

Also check inline test YAMLs:
Run: `grep -rln 'r#"' rust/chunkshop/tests/ rust/chunkshop/src/ | xargs grep -l 'target:' 2>/dev/null`

Build a list of files to update.

- [ ] **Step 2: Update each `target:` block to v0.4.0 shape**

For each YAML file found, find the `target:` block. Apply this transform:

**Before:**
```yaml
target:
  dsn_env: PG_DSN
  schema: chunkshop
  table: my_chunks
  mode: overwrite
  ...
```

**After:**
```yaml
target:
  type: postgres
  dsn_env: PG_DSN
  database: chunkshop
  table: my_chunks
  mode: overwrite
  ...
```

Two changes per block:
1. Add `type: postgres` as the first field under `target:`.
2. Rename `schema:` to `database:`.

For inline YAML in Rust test files (typically inside `r#"..."#` raw string literals), apply the same edits.

- [ ] **Step 3: Run the full test suite to verify all migrations are clean**

Run: `cargo test -p chunkshop 2>&1 | tail -40`
Expected: all tests pass (or skip-if-no-DSN). Any failure here is most likely a missed YAML fixture — find and update.

- [ ] **Step 4: Commit**

```bash
git add docs/samples/ rust/chunkshop/tests/ rust/chunkshop/src/
git commit -m "samples+tests: migrate target: blocks to v0.4.0 (type: postgres, database:)"
```

---

### Task 13: Add legacy-form rejection in `load_config` (TDD)

**Files:**
- Modify: `rust/chunkshop/src/config.rs`
- Create: `rust/chunkshop/tests/config_legacy_rejection.rs`

- [ ] **Step 1: Write failing tests for the three legacy-form rejections**

Create `rust/chunkshop/tests/config_legacy_rejection.rs`:

```rust
//! Tests for v0.4.0 legacy-form YAML rejection. Each must produce a friendly
//! error message that names the new field/value (V4-SC-006 in the roadmap).

use std::io::Write;
use tempfile::NamedTempFile;

use chunkshop::load_config;

fn write_yaml(yaml: &str) -> NamedTempFile {
    let mut f = NamedTempFile::new().expect("create temp file");
    f.write_all(yaml.as_bytes()).expect("write yaml");
    f
}

const VALID_REST: &str = r#"
source:
  type: inline
chunker:
  type: fixed_overlap
  chunk_size: 200
  overlap: 50
embedder:
  type: fastembed
  model: BAAI/bge-small-en-v1.5
extractor:
  type: none
framer:
  type: none
"#;

#[test]
fn rejects_legacy_pgvector_type() {
    let yaml = format!(
        r#"target:
  type: pgvector
  dsn_env: X
  database: y
  table: z
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("pgvector") && msg.contains("postgres") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}

#[test]
fn rejects_legacy_schema_field() {
    let yaml = format!(
        r#"target:
  type: postgres
  dsn_env: X
  schema: y
  table: z
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("schema") && msg.contains("database") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}

#[test]
fn rejects_legacy_overwrite_bool() {
    let yaml = format!(
        r#"target:
  type: postgres
  dsn_env: X
  database: y
  table: z
  overwrite: true
{VALID_REST}"#
    );
    let f = write_yaml(&yaml);
    let err = load_config(f.path()).unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("overwrite") && msg.contains("mode") && msg.contains("v0.4.0"),
        "expected friendly migration message, got: {msg}"
    );
}
```

- [ ] **Step 2: Add `tempfile` to dev-dependencies if missing**

In `rust/chunkshop/Cargo.toml`, check if `tempfile` is already in `[dev-dependencies]`. If not, add:

```toml
tempfile = "3"
```

- [ ] **Step 3: Run the tests and verify they FAIL (current load_config doesn't reject these)**

Run: `cargo test -p chunkshop --test config_legacy_rejection 2>&1 | tail -30`
Expected: 3 tests fail. The exact failure mode varies — `pgvector` triggers a serde "unknown variant" error which doesn't include "v0.4.0"; `schema` triggers "unknown field" error; `overwrite: true` may pass through silently and not error at all.

- [ ] **Step 4: Implement the pre-deserialize legacy-form check in `load_config`**

In `rust/chunkshop/src/config.rs`, find the `pub fn load_config(path: &Path) -> Result<CellConfig>` function. Add a helper function and call it at the top of `load_config` before the typed deserialization happens.

Add this helper (place it just above `pub fn load_config`):

```rust
/// Pre-deserialize legacy-form rejection (V4-SC-006).
///
/// Walks the raw YAML for known 0.3.x field/value patterns and emits a
/// migration-friendly error when found. Without this pass, serde's default
/// errors are cryptic ("unknown variant `pgvector`") or absent (silently
/// accepted legacy fields).
fn reject_legacy_forms(yaml: &serde_yml::Value) -> Result<()> {
    let target = yaml.get("target").and_then(|v| v.as_mapping());
    let Some(target) = target else {
        return Ok(()); // No target block; nothing to validate.
    };

    if let Some(t) = target.get("type").and_then(|v| v.as_str()) {
        if t == "pgvector" {
            return Err(anyhow!(
                "target.type 'pgvector' was renamed to 'postgres' in v0.4.0. Update your YAML."
            ));
        }
    }
    if target.get("schema").is_some() {
        return Err(anyhow!(
            "target.schema was renamed to target.database in v0.4.0. Update your YAML."
        ));
    }
    if let Some(o) = target.get("overwrite") {
        if matches!(o.as_bool(), Some(true)) {
            return Err(anyhow!(
                "target.overwrite: true was replaced by target.mode: 'overwrite' in v0.4.0. \
                 Update your YAML."
            ));
        }
    }
    Ok(())
}
```

Then in `pub fn load_config(path: &Path) -> Result<CellConfig>`, after the YAML file is read into a string but before deserialization into `CellConfig`, parse it as `serde_yml::Value` and run the rejection check. The current function shape is roughly:

```rust
pub fn load_config(path: &Path) -> Result<CellConfig> {
    let raw = std::fs::read_to_string(path).with_context(...)?;
    let cfg: CellConfig = serde_yml::from_str(&raw).with_context(...)?;
    // ... validate identifiers, etc.
    Ok(cfg)
}
```

Insert the legacy-rejection step:

```rust
pub fn load_config(path: &Path) -> Result<CellConfig> {
    let raw = std::fs::read_to_string(path).with_context(...)?;

    // V4-SC-006: reject 0.3.x legacy YAML shapes with friendly errors before
    // typed deserialization (which would emit cryptic "unknown variant" errors).
    let raw_value: serde_yml::Value = serde_yml::from_str(&raw)
        .with_context(|| format!("parsing YAML at {}", path.display()))?;
    reject_legacy_forms(&raw_value)?;

    let cfg: CellConfig = serde_yml::from_str(&raw).with_context(...)?;
    // ... validate identifiers, etc.
    Ok(cfg)
}
```

(The exact existing structure may differ — preserve all existing logic and only add the new pre-pass.)

- [ ] **Step 5: Run the legacy-rejection tests and verify they pass**

Run: `cargo test -p chunkshop --test config_legacy_rejection`
Expected: 3 tests pass.

- [ ] **Step 6: Run the full test suite to verify no regression**

Run: `cargo test -p chunkshop 2>&1 | tail -20`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add rust/chunkshop/src/config.rs rust/chunkshop/tests/config_legacy_rejection.rs rust/chunkshop/Cargo.toml
git commit -m "feat(config): legacy-form YAML rejection with v0.4.0 migration messages"
```

---

## Phase D — Implement `PgSink` (against final config shape)

### Task 14: Implement `PgSink::new` + `PgSink::create_table` (port mode dispatch)

**Files:**
- Create: `rust/chunkshop/src/sinks/pg.rs`
- Modify: `rust/chunkshop/src/sinks/mod.rs` (add `pub mod pg;` + re-export)

This task ports the bulk of `PgVectorSink::create_table` logic into `PgSink`, but expressed via the new `PostgresBackend` interface. The existing `PgVectorSink` continues to work in parallel until Task 25 swaps callsites.

- [ ] **Step 1: Create `rust/chunkshop/src/sinks/pg.rs` with `PgSink::new`, helpers, and `create_table`**

```rust
//! Postgres sink — chunkshop data-model writer using `PostgresBackend` for dialect.
//!
//! Mirrors `python/src/chunkshop/sinks/pg.py`. Owns mode dispatch
//! (overwrite/append/create_if_missing), foreign-tag safety, append preflight,
//! `_ensure_promote_columns`, source write-once on UPDATE, delete_orphans, and
//! the canonical chunks-table column list.

use std::future::Future;

use anyhow::{anyhow, Context, Result};
use pgvector::Vector;
use sqlx::{PgPool, Postgres, Row, Transaction};

use crate::backends::base::{BackendConn, BackendDialect, ColSpec};
use crate::backends::postgres::PostgresBackend;
use crate::chunker::Chunk;
use crate::config::{PostgresTargetConfig, PromoteColumn};
use crate::sinks::base::Sink;

pub struct PgSink {
    cfg: PostgresTargetConfig,
    backend: PostgresBackend,
    embed_dim: usize,
}

/// Traverse a dotted path through a JSON value. Returns `None` if any segment
/// is missing or an intermediate is not an object. Mirrors Python's
/// `_jsonb_path_get`. Chunkshop-specific dict navigation, not SQL — lives here
/// rather than on Backend.
fn jsonb_path_get<'a>(
    meta: &'a serde_json::Value,
    path: &str,
) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        let obj = cur.as_object()?;
        cur = obj.get(seg)?;
    }
    Some(cur)
}

/// The chunkshop-canonical chunks-table column list, PG-typed via the backend.
fn canonical_cols<B: BackendDialect>(b: &B, dim: usize) -> Vec<ColSpec> {
    vec![
        ColSpec { name: "id", type_ddl: b.text_pk_type_ddl(), nullable: false, default: None, is_primary_key: true },
        ColSpec { name: "doc_id", type_ddl: b.text_pk_type_ddl(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "seq_num", type_ddl: "int".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "original_content", type_ddl: "text".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "embedded_content", type_ddl: "text".to_string(), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "tags", type_ddl: b.tags_array_type_ddl(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "metadata", type_ddl: b.json_type_ddl(), nullable: false, default: Some("'{}'"), is_primary_key: false },
        ColSpec { name: "embedding", type_ddl: b.vector_type_ddl(dim), nullable: false, default: None, is_primary_key: false },
        ColSpec { name: "source", type_ddl: "text".to_string(), nullable: true, default: None, is_primary_key: false },
        ColSpec { name: "created_at", type_ddl: "timestamptz".to_string(), nullable: false, default: Some("now()"), is_primary_key: false },
    ]
}

impl PgSink {
    pub fn new(cfg: PostgresTargetConfig, backend: PostgresBackend, embed_dim: usize) -> Self {
        Self { cfg, backend, embed_dim }
    }

    fn fq(&self) -> String {
        self.backend.fq_table(&self.cfg.database_name, &self.cfg.table)
    }

    /// Inherent accessor — used by `Pipeline::sample_row` (demo helper).
    /// NOT on the Sink trait; for v0.4.0 PG-only usage. Removed when Pipeline
    /// stops using raw pool access (separate cleanup task, post-R1).
    pub async fn pool(&self) -> Result<&PgPool> {
        self.backend.pool().await
    }
}

impl Sink for PgSink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let mut tx = pool.begin().await.context("begin schema-setup tx")?;

            self.backend
                .acquire_create_lock(&mut tx, &self.cfg.database_name)
                .await?;

            sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
                .execute(&mut *tx)
                .await
                .context("CREATE EXTENSION vector")?;

            sqlx::query(&self.backend.create_database_sql(&self.cfg.database_name))
                .execute(&mut *tx)
                .await
                .context("CREATE SCHEMA")?;

            match self.cfg.mode.as_str() {
                "overwrite" => self.overwrite_create_in_tx(&mut tx).await?,
                "create_if_missing" => self.create_if_missing_in_tx(&mut tx).await?,
                "append" => self.append_preflight_in_tx(&mut tx).await?,
                other => return Err(anyhow!("unknown target.mode: {other:?}")),
            }
            tx.commit().await.context("commit schema-setup tx")?;
            Ok(())
        }
    }

    fn write_document(
        &self,
        _doc_id: &str,
        _chunks: &[Chunk],
        _embeddings: &[Vec<f32>],
        _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { unimplemented!("Task 15") }
    }

    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn query_top_k(
        &self,
        _query_vec: &[f32],
        _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { unimplemented!("Task 16") }
    }
}

// --- Mode dispatch helpers (private). Ported from PgVectorSink. ---

impl PgSink {
    async fn overwrite_create_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await?
            && !self.cfg.force_overwrite
        {
            let stmt = format!(
                "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
                self.fq()
            );
            let rows = sqlx::query(&stmt).fetch_all(&mut **tx).await?;
            let existing: std::collections::BTreeSet<String> = rows
                .into_iter()
                .filter_map(|r| r.try_get::<String, _>("source").ok())
                .collect();
            let my_tag = self.cfg.source_tag.clone();
            let foreign: Vec<&String> = existing
                .iter()
                .filter(|t| my_tag.as_deref() != Some(t.as_str()))
                .collect();
            if !foreign.is_empty() {
                return Err(anyhow!(
                    "overwrite refuses to drop {schema}.{table}: table holds rows with \
                     source_tag values {foreign:?} that differ from this cell's source_tag \
                     {my_tag:?}. Set target.force_overwrite: true in YAML to bypass.",
                    schema = self.cfg.database_name,
                    table = self.cfg.table,
                    foreign = foreign,
                    my_tag = my_tag,
                ));
            }
        }
        if self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            sqlx::query(&self.backend.drop_table_sql(&self.fq()))
                .execute(&mut **tx)
                .await
                .context("DROP TABLE")?;
        }
        self.create_base_ddl_in_tx(tx).await
    }

    async fn create_if_missing_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            return self.create_base_ddl_in_tx(tx).await;
        }
        sqlx::query(&self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "text"))
            .execute(&mut **tx)
            .await
            .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn append_preflight_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        if !self.backend.table_exists(tx, &self.cfg.database_name, &self.cfg.table).await? {
            return Err(anyhow!(
                "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
                self.cfg.database_name,
                self.cfg.table
            ));
        }
        let current_dim = self.backend.embedding_dim(tx, &self.cfg.database_name, &self.cfg.table).await?;
        let Some(d) = current_dim else {
            return Err(anyhow!(
                "append mode: table {}.{} has no 'embedding' vector column. Not a chunkshop \
                 table — pick a different target or use mode='overwrite'.",
                self.cfg.database_name,
                self.cfg.table
            ));
        };
        if d != self.embed_dim {
            return Err(anyhow!(
                "append mode: target embedding dim is {d}, cell embedder dim is {own}. \
                 Vectors are not comparable. Use a different target or re-ingest into overwrite.",
                d = d,
                own = self.embed_dim,
            ));
        }
        sqlx::query(&self.backend.add_column_if_not_exists_sql(&self.fq(), "source", "text"))
            .execute(&mut **tx)
            .await
            .context("ADD COLUMN source")?;
        self.ensure_promote_columns_in_tx(tx).await
    }

    async fn ensure_promote_columns_in_tx(
        &self,
        tx: &mut Transaction<'_, Postgres>,
    ) -> Result<()> {
        for pc in &self.cfg.promote_metadata {
            // pc.type_ is allowlisted in PromoteColumn::validate_type.
            let stmt = self.backend.add_column_if_not_exists_sql(
                &self.fq(),
                &pc.column_name(),
                &pc.type_,
            );
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("ADD COLUMN promote_metadata")?;
        }
        Ok(())
    }

    async fn create_base_ddl_in_tx(&self, tx: &mut Transaction<'_, Postgres>) -> Result<()> {
        let cols = canonical_cols(&self.backend, self.embed_dim);
        for stmt in self.backend.emit_chunks_table_ddl(&self.fq(), &cols, self.cfg.hnsw, self.embed_dim, None) {
            sqlx::query(&stmt)
                .execute(&mut **tx)
                .await
                .context("emit_chunks_table_ddl statement")?;
        }
        self.ensure_promote_columns_in_tx(tx).await
    }
}
```

- [ ] **Step 2: Update `rust/chunkshop/src/sinks/mod.rs` to declare and re-export `PgSink`**

Replace contents of `rust/chunkshop/src/sinks/mod.rs` with:

```rust
//! Sinks — chunkshop's per-backend data-model semantics layer.

pub mod base;
pub mod pg;

pub use base::Sink;
pub use pg::PgSink;

// AnySink + load_sink factory land in Phase F (Task 23).
```

- [ ] **Step 3: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build (the unimplemented! stubs in `write_document`, `delete_document`, `count_docs`, `query_top_k` panic only when called — which they aren't yet from the existing test path that goes through PgVectorSink).

- [ ] **Step 4: Add a sanity integration test for `PgSink::create_table` (skip-if-no-DSN)**

Create `rust/chunkshop/tests/pg_sink_create_table.rs`:

```rust
//! Sanity integration test for PgSink::create_table.

use chunkshop::backends::PostgresBackend;
use chunkshop::config::PostgresTargetConfig;
use chunkshop::sinks::{PgSink, Sink};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

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

    let cfg = PostgresTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_r1_pgsink".to_string(),
        table: "ct".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("r1-test".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    };
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = PgSink::new(cfg, backend, 8);
    sink.create_table().await?;

    // Cleanup
    let pool = sink.pool().await?;
    sqlx::query(r#"DROP SCHEMA "chunkshop_r1_pgsink" CASCADE"#)
        .execute(pool)
        .await?;
    Ok(())
}
```

- [ ] **Step 5: Run the new test**

Run: `cargo test -p chunkshop --test pg_sink_create_table -- --nocapture`
Expected: PASS if DSN set; otherwise prints "skipping" and exits clean.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/sinks/ rust/chunkshop/tests/pg_sink_create_table.rs
git commit -m "feat(sinks): PgSink::create_table — mode dispatch + foreign-tag safety + append preflight"
```

---

### Task 15: Implement `PgSink::write_document`

**Files:**
- Modify: `rust/chunkshop/src/sinks/pg.rs`

- [ ] **Step 1: Replace the `write_document` stub with the full implementation**

In `rust/chunkshop/src/sinks/pg.rs`, replace:

```rust
    fn write_document(
        &self,
        _doc_id: &str,
        _chunks: &[Chunk],
        _embeddings: &[Vec<f32>],
        _tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move { unimplemented!("Task 15") }
    }
```

with:

```rust
    fn write_document(
        &self,
        doc_id: &str,
        chunks: &[Chunk],
        embeddings: &[Vec<f32>],
        tags_per_chunk: &[Vec<String>],
    ) -> impl Future<Output = Result<()>> + Send {
        async move {
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
            let n_base = 9; // id, doc_id, seq_num, original_content, embedded_content, tags, metadata, embedding, source

            let base_col_names: Vec<&str> = vec![
                "id", "doc_id", "seq_num", "original_content", "embedded_content",
                "tags", "metadata", "embedding", "source",
            ];
            let mut all_cols: Vec<String> = base_col_names.iter().map(|c| c.to_string()).collect();
            for pc in promote {
                all_cols.push(pc.column_name());
            }
            let cols_sql: String = all_cols
                .iter()
                .map(|c| format!("\"{c}\""))
                .collect::<Vec<_>>()
                .join(", ");

            let mut placeholders: Vec<String> = (1..=n_base)
                .map(|i| match i {
                    7 => format!("${i}::jsonb"),
                    _ => format!("${i}"),
                })
                .collect();
            for (i, pc) in promote.iter().enumerate() {
                // pc.type_ is allowlisted; safe to interpolate as ::cast.
                placeholders.push(format!("${}::{}", n_base + 1 + i, pc.type_));
            }
            let vals_sql = placeholders.join(", ");

            // Update cols: skip id, doc_id, seq_num AND source (write-once).
            let mut update_cols: Vec<&str> = vec![
                "original_content", "embedded_content", "tags", "metadata", "embedding",
            ];
            let mut update_cols_owned: Vec<String> = update_cols.iter().map(|s| s.to_string()).collect();
            for pc in promote {
                update_cols_owned.push(pc.column_name());
            }
            let update_refs: Vec<&str> = update_cols_owned.iter().map(|s| s.as_str()).collect();

            let upsert = self.backend.upsert_clause(&["id"], &update_refs);

            let insert_sql = format!(
                "INSERT INTO {tbl} ({cols}) VALUES ({vals}) {upsert}",
                tbl = self.fq(),
                cols = cols_sql,
                vals = vals_sql,
                upsert = upsert,
            );

            let pool = self.backend.pool().await?;
            let mut tx = pool.begin().await?;
            for ((c, emb), tags) in chunks
                .iter()
                .zip(embeddings.iter())
                .zip(tags_per_chunk.iter())
            {
                let id = format!("{}::{}", c.doc_id, c.seq_num);
                let vec = Vector::from(emb.clone());
                let meta_str = serde_json::to_string(&c.metadata)?;

                let mut q = sqlx::query(&insert_sql)
                    .bind(id)
                    .bind(&c.doc_id)
                    .bind(c.seq_num as i32)
                    .bind(&c.original_content)
                    .bind(&c.embedded_content)
                    .bind(tags)
                    .bind(&meta_str)
                    .bind(&vec)
                    .bind(self.cfg.source_tag.as_deref());

                for pc in promote {
                    q = q.bind(promote_value_for(&c.metadata, pc));
                }

                q.execute(&mut *tx).await.context("INSERT chunk row")?;
            }

            // delete_orphans: same-tx cleanup of stale chunks at higher seq_nums
            // when a doc shrinks. doc_id parameter is the canonical key.
            if self.cfg.delete_orphans {
                let new_count = chunks.len() as i32;
                let delete_sql = format!(
                    "DELETE FROM {tbl} WHERE doc_id = $1 AND seq_num >= $2",
                    tbl = self.fq(),
                );
                sqlx::query(&delete_sql)
                    .bind(doc_id)
                    .bind(new_count)
                    .execute(&mut *tx)
                    .await
                    .context("DELETE orphan chunks")?;
            }
            tx.commit().await?;
            Ok(())
        }
    }
```

- [ ] **Step 2: Add the `promote_value_for` helper at the bottom of `rust/chunkshop/src/sinks/pg.rs`**

```rust
/// Project a chunk's metadata down to the right text representation for the
/// promoted column's typed cast. Postgres handles the actual cast via the
/// `::<type>` placeholder. Mirrors the helper of the same name in the legacy
/// `sink.rs` (see write_document).
fn promote_value_for(metadata: &serde_json::Value, pc: &PromoteColumn) -> Option<String> {
    let v = jsonb_path_get(metadata, &pc.path)?;
    Some(match v {
        serde_json::Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    })
}
```

- [ ] **Step 3: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sinks/pg.rs
git commit -m "feat(sinks): PgSink::write_document — upsert with source write-once + delete_orphans"
```

---

### Task 16: Implement `PgSink::count_docs`, `query_top_k`, `delete_document`

**Files:**
- Modify: `rust/chunkshop/src/sinks/pg.rs`

- [ ] **Step 1: Replace the three remaining `unimplemented!()` stubs in `impl Sink for PgSink`**

Replace:

```rust
    fn delete_document(&self, _doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move { unimplemented!("Task 16") }
    }

    fn query_top_k(
        &self,
        _query_vec: &[f32],
        _k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move { unimplemented!("Task 16") }
    }
```

with:

```rust
    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let result = if let Some(tag) = &self.cfg.source_tag {
                let stmt = format!(
                    "DELETE FROM {tbl} WHERE doc_id = $1 AND source = $2",
                    tbl = self.fq()
                );
                sqlx::query(&stmt).bind(doc_id).bind(tag).execute(pool).await?
            } else {
                let stmt = format!("DELETE FROM {tbl} WHERE doc_id = $1", tbl = self.fq());
                sqlx::query(&stmt).bind(doc_id).execute(pool).await?
            };
            Ok(result.rows_affected() as i64)
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let stmt = format!("SELECT COUNT(DISTINCT doc_id) FROM {}", self.fq());
            let row = sqlx::query(&stmt).fetch_one(pool).await?;
            Ok(row.get::<i64, _>(0))
        }
    }

    fn query_top_k(
        &self,
        query_vec: &[f32],
        k: usize,
    ) -> impl Future<Output = Result<Vec<(String, i32, f64)>>> + Send {
        async move {
            let pool = self.backend.pool().await?;
            let vec_lit = self.backend.vector_literal(query_vec);
            let stmt = format!(
                "SELECT doc_id, seq_num, embedding <=> $1::vector AS distance \
                 FROM {tbl} ORDER BY embedding <=> $1::vector LIMIT $2",
                tbl = self.fq()
            );
            let rows = sqlx::query(&stmt)
                .bind(&vec_lit)
                .bind(k as i64)
                .fetch_all(pool)
                .await?;
            Ok(rows
                .into_iter()
                .map(|r| {
                    (
                        r.get::<String, _>(0),
                        r.get::<i32, _>(1),
                        r.get::<f64, _>(2),
                    )
                })
                .collect())
        }
    }
```

- [ ] **Step 2: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean.

- [ ] **Step 3: Run all sink tests + parity tests**

Run: `cargo test -p chunkshop --test pg_sink_create_table --test sink_modes_parity --test embedding_parity`
Expected: all pass (or skip-if-no-DSN). PgSink is constructed in `pg_sink_create_table` only; the other parity tests still go through PgVectorSink.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sinks/pg.rs
git commit -m "feat(sinks): PgSink complete — delete_document + count_docs + query_top_k"
```

---

## Phase E — Source migration (file moves + pg_table uses Backend)

### Task 17: Move `FilesSource` to `sources/files.rs`

**Files:**
- Create: `rust/chunkshop/src/sources/files.rs`
- Modify: `rust/chunkshop/src/source.rs` (remove FilesSource; re-export from new location)
- Modify: `rust/chunkshop/src/sources/mod.rs` (declare module)
- Modify: `rust/chunkshop/src/lib.rs` (already exposes via `pub use source::FilesSource`; add `pub use sources::files::FilesSource`)

This is a pure file move. No semantic change.

- [ ] **Step 1: Create `rust/chunkshop/src/sources/files.rs` with the FilesSource definition**

Cut the `FilesSource` struct + `impl FilesSource` block from the current `rust/chunkshop/src/source.rs` (lines ~23-78) into a new file:

```rust
//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde_json::json;
use sha1::{Digest, Sha1};

use crate::config::FilesSourceConfig;
use crate::sources::base::Document;

pub struct FilesSource {
    cfg: FilesSourceConfig,
}

impl FilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        Self { cfg }
    }

    /// Enumerate files matching the glob, in sorted order, reading each as text.
    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut paths: Vec<PathBuf> = glob::glob(&self.cfg.glob)
            .with_context(|| format!("invalid glob {:?}", self.cfg.glob))?
            .filter_map(std::result::Result::ok)
            .collect();
        if paths.is_empty() {
            return Err(anyhow!("no files matched glob: {}", self.cfg.glob));
        }
        paths.sort();

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let text = std::fs::read_to_string(&p)
                .with_context(|| format!("reading {}", p.display()))?;
            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            out.push(Document {
                id: doc_id,
                content: text,
                title,
                metadata: json!({ "source_path": p.display().to_string() }),
            });
        }
        Ok(out)
    }

    fn id_for(&self, path: &Path) -> Result<String> {
        match self.cfg.id_from.as_str() {
            "path" => Ok(path.display().to_string()),
            "stem" => path
                .file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
                .ok_or_else(|| anyhow!("file has no stem: {}", path.display())),
            "sha1" => {
                let mut hasher = Sha1::new();
                hasher.update(path.display().to_string().as_bytes());
                Ok(format!("{:x}", hasher.finalize()))
            }
            other => Err(anyhow!("unknown id_from: {other}")),
        }
    }
}
```

- [ ] **Step 2: Update `sources/mod.rs` to declare and re-export**

Update `rust/chunkshop/src/sources/mod.rs` to:

```rust
//! Sources — input document iterators per backing store.

pub mod base;
pub mod files;

pub use base::Document;
pub use files::FilesSource;

// Other source modules + AnySource land in subsequent tasks.
```

- [ ] **Step 3: Remove the FilesSource block from `rust/chunkshop/src/source.rs` and re-export from new location**

In `rust/chunkshop/src/source.rs`, delete lines 23-78 (the entire `pub struct FilesSource { ... }` and `impl FilesSource { ... }` block). At the top of `source.rs` (alongside the existing `pub use crate::sources::base::Document;` line from Task 3), add:

```rust
pub use crate::sources::files::FilesSource;
```

- [ ] **Step 4: Verify the crate compiles + run source tests**

Run: `cargo build -p chunkshop && cargo test -p chunkshop --lib --tests`
Expected: clean build, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs
git commit -m "refactor(sources): move FilesSource to sources/files.rs"
```

---

### Task 18: Move `JsonCorpusSource` to `sources/json_corpus.rs`

Same pattern as Task 17, applied to `JsonCorpusSource` (lines ~84-169 in current `source.rs`).

**Files:**
- Create: `rust/chunkshop/src/sources/json_corpus.rs`
- Modify: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`

- [ ] **Step 1: Create `rust/chunkshop/src/sources/json_corpus.rs`**

Copy the `JsonCorpusSource` struct and impl from `source.rs` into a new file:

```rust
//! JSON-corpus source. Mirrors `python/src/chunkshop/sources/json_corpus.py`.
//! Reads a JSON file, extracts the array under `documents_key`, and yields
//! one `Document` per row.

use anyhow::{anyhow, Context, Result};

use crate::config::JsonCorpusSourceConfig;
use crate::sources::base::Document;

pub struct JsonCorpusSource {
    cfg: JsonCorpusSourceConfig,
}

impl JsonCorpusSource {
    pub fn new(cfg: JsonCorpusSourceConfig) -> Self {
        Self { cfg }
    }

    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let bytes = std::fs::read(&self.cfg.path)
            .with_context(|| format!("reading {}", self.cfg.path))?;
        let parsed: serde_json::Value = serde_json::from_slice(&bytes)
            .with_context(|| format!("parsing JSON from {}", self.cfg.path))?;
        let arr = parsed
            .get(&self.cfg.documents_key)
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                anyhow!(
                    "no array at key {:?} in {}",
                    self.cfg.documents_key,
                    self.cfg.path
                )
            })?;

        let mut out = Vec::with_capacity(arr.len());
        for (i, row_value) in arr.iter().enumerate() {
            let row = row_value.as_object().ok_or_else(|| {
                anyhow!("row {i} in {} is not a JSON object", self.cfg.path)
            })?;
            let id = row
                .get(&self.cfg.id_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!("row {i} missing string field {:?} in {}", self.cfg.id_field, self.cfg.path)
                })?
                .to_string();
            let content = row
                .get(&self.cfg.content_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!("row {i} missing string field {:?} in {}", self.cfg.content_field, self.cfg.path)
                })?
                .to_string();
            let title = self
                .cfg
                .title_field
                .as_ref()
                .and_then(|tf| row.get(tf).and_then(|v| v.as_str()).map(String::from));

            let mut meta = serde_json::Map::new();
            for (k, v) in row.iter() {
                if k == &self.cfg.id_field { continue; }
                if k == &self.cfg.content_field { continue; }
                if let Some(tf) = &self.cfg.title_field {
                    if k == tf { continue; }
                }
                meta.insert(k.clone(), v.clone());
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
```

- [ ] **Step 2: Update `sources/mod.rs`**

Add `pub mod json_corpus;` and `pub use json_corpus::JsonCorpusSource;` to `rust/chunkshop/src/sources/mod.rs`.

- [ ] **Step 3: Remove `JsonCorpusSource` from `source.rs` and re-export**

Delete the `JsonCorpusSource` block from `rust/chunkshop/src/source.rs`. Add to the re-export block at the top:
```rust
pub use crate::sources::json_corpus::JsonCorpusSource;
```

- [ ] **Step 4: Verify**

Run: `cargo build -p chunkshop && cargo test -p chunkshop --test json_corpus_source`
Expected: clean build; tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs
git commit -m "refactor(sources): move JsonCorpusSource to sources/json_corpus.rs"
```

---

### Task 19: Move `HttpSource` to `sources/http.rs`

Same pattern. Cut `HttpSource` (lines ~290-394 in current `source.rs`) into a new file.

**Files:**
- Create: `rust/chunkshop/src/sources/http.rs`
- Modify: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`

- [ ] **Step 1: Create `rust/chunkshop/src/sources/http.rs`** — copy the entire `HttpSource` struct and impl block from `source.rs` (with leading docstring), adjusting imports:

```rust
//! HTTP source. Mirrors `python/src/chunkshop/sources/http.py`.

use anyhow::{anyhow, Context, Result};

use crate::config::HttpSourceConfig;
use crate::sources::base::Document;

pub struct HttpSource {
    cfg: HttpSourceConfig,
}

impl HttpSource {
    pub fn new(cfg: HttpSourceConfig) -> Self {
        Self { cfg }
    }

    async fn fetch(client: &reqwest::Client, url: &str) -> Result<(String, u16, String)> {
        let resp = client
            .get(url)
            .header("User-Agent", "chunkshop-http/1.0")
            .send()
            .await
            .with_context(|| format!("GET {url}"))?;
        let status = resp.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(anyhow!("GET {url}: status {status}"));
        }
        let ctype = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = resp
            .text()
            .await
            .with_context(|| format!("reading body of {url}"))?;
        Ok((body, status, ctype))
    }

    fn extract_title(body: &str) -> Option<String> {
        let re = regex::Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
        let captures = re.captures(body)?;
        let raw = captures.get(1)?.as_str().trim();
        if raw.is_empty() {
            None
        } else {
            Some(raw.to_string())
        }
    }

    fn parse_sitemap(body: &str) -> Vec<String> {
        let re = match regex::Regex::new(r"(?is)<loc>(.*?)</loc>") {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        re.captures_iter(body)
            .filter_map(|c| c.get(1).map(|m| m.as_str().trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect()
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut fetch_list: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for u in &self.cfg.urls {
            if seen.insert(u.clone()) {
                fetch_list.push(u.clone());
            }
        }
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .context("build reqwest client")?;
        if let Some(sm) = &self.cfg.sitemap {
            let (sm_body, _, _) = Self::fetch(&client, sm).await?;
            for u in Self::parse_sitemap(&sm_body) {
                if seen.insert(u.clone()) {
                    fetch_list.push(u);
                }
            }
        }

        let mut out: Vec<Document> = Vec::with_capacity(fetch_list.len());
        for url in fetch_list {
            let (body, status, ctype) = Self::fetch(&client, &url).await?;
            let title = Self::extract_title(&body);
            out.push(Document {
                id: url.clone(),
                content: body,
                title,
                metadata: serde_json::json!({
                    "url": url,
                    "status_code": status,
                    "content_type": ctype,
                }),
            });
        }
        Ok(out)
    }
}
```

- [ ] **Step 2: Update `sources/mod.rs`** — add `pub mod http;` and `pub use http::HttpSource;`.

- [ ] **Step 3: Remove `HttpSource` block from `source.rs`**, add `pub use crate::sources::http::HttpSource;` to the re-export block.

- [ ] **Step 4: Verify**

Run: `cargo build -p chunkshop && cargo test -p chunkshop --test http_source`
Expected: clean build, tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs
git commit -m "refactor(sources): move HttpSource to sources/http.rs"
```

---

### Task 20: Move `S3Source` to `sources/s3.rs`

Same pattern. Cut `S3Source` (lines ~406-475 in current `source.rs`) into a new file.

**Files:**
- Create: `rust/chunkshop/src/sources/s3.rs`
- Modify: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`

- [ ] **Step 1: Create `rust/chunkshop/src/sources/s3.rs`**

```rust
//! S3 source. Mirrors `python/src/chunkshop/sources/s3.py`.

use anyhow::{Context, Result};

use crate::config::S3SourceConfig;
use crate::sources::base::Document;

pub struct S3Source {
    cfg: S3SourceConfig,
}

impl S3Source {
    pub fn new(cfg: S3SourceConfig) -> Self {
        Self { cfg }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        use futures::StreamExt;
        use object_store::aws::AmazonS3Builder;
        use object_store::{path::Path as ObjPath, ObjectStore};

        let mut builder = AmazonS3Builder::from_env().with_bucket_name(&self.cfg.bucket);
        if let Some(endpoint) = &self.cfg.endpoint_url {
            builder = builder.with_endpoint(endpoint);
            builder = builder.with_allow_http(endpoint.starts_with("http://"));
        }
        let store = builder
            .build()
            .with_context(|| format!("building S3 client for bucket {}", self.cfg.bucket))?;

        let prefix = if self.cfg.prefix.is_empty() {
            None
        } else {
            Some(ObjPath::from(self.cfg.prefix.clone()))
        };
        let mut listing = store.list(prefix.as_ref());
        let mut metas: Vec<object_store::ObjectMeta> = Vec::new();
        while let Some(item) = listing.next().await {
            metas.push(item.with_context(|| format!("list under {}", self.cfg.prefix))?);
        }

        let mut out: Vec<Document> = Vec::with_capacity(metas.len());
        for meta in metas {
            let key = meta.location.to_string();
            let result = store
                .get(&meta.location)
                .await
                .with_context(|| format!("GET s3://{}/{key}", self.cfg.bucket))?;
            let bytes = result
                .bytes()
                .await
                .with_context(|| format!("read body of s3://{}/{key}", self.cfg.bucket))?;
            let content = String::from_utf8_lossy(&bytes).to_string();
            let etag = meta.e_tag.clone().unwrap_or_default();
            out.push(Document {
                id: format!("s3://{}/{}", self.cfg.bucket, key),
                content,
                title: None,
                metadata: serde_json::json!({
                    "bucket": self.cfg.bucket,
                    "key": key,
                    "size": meta.size,
                    "etag": etag,
                }),
            });
        }
        Ok(out)
    }
}
```

- [ ] **Step 2: Update `sources/mod.rs`** — add `pub mod s3;` and `pub use s3::S3Source;`.

- [ ] **Step 3: Remove `S3Source` block from `source.rs`** and add `pub use crate::sources::s3::S3Source;`.

- [ ] **Step 4: Verify**

Run: `cargo build -p chunkshop && cargo test -p chunkshop --test s3_source`
Expected: clean build, tests pass.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs
git commit -m "refactor(sources): move S3Source to sources/s3.rs"
```

---

### Task 21: Move + migrate `PgTableSource` to `sources/pg_table.rs` (using `PostgresBackend`)

This is the only source that has a real semantic change in R1: it switches from raw sqlx pool construction to using `PostgresBackend` for connection + identifier quoting.

**Files:**
- Create: `rust/chunkshop/src/sources/pg_table.rs`
- Modify: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs`

- [ ] **Step 1: Create `rust/chunkshop/src/sources/pg_table.rs` using PostgresBackend**

```rust
//! Postgres source. Mirrors `python/src/chunkshop/sources/pg_table.py`.
//! Uses PostgresBackend for connection + identifier quoting (v0.4.0 modular shape).

use anyhow::{Context, Result};
use serde_json::json;

use crate::backends::base::{BackendConn, BackendDialect};
use crate::backends::postgres::PostgresBackend;
use crate::config::PgTableSourceConfig;
use crate::sources::base::Document;

pub struct PgTableSource {
    cfg: PgTableSourceConfig,
    backend: PostgresBackend,
}

impl PgTableSource {
    pub fn new(cfg: PgTableSourceConfig) -> Self {
        let backend = PostgresBackend::new(cfg.dsn_env.clone());
        Self { cfg, backend }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        // Column order matches Python's pg_table.py:
        //   [id, content, optional title, *metadata_columns...]
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
            fq = self.backend.fq_table(&self.cfg.schema_name, &self.cfg.table)
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

fn read_meta_value(row: &sqlx::postgres::PgRow, idx: usize) -> serde_json::Value {
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
    if let Ok(v) = row.try_get::<Option<Vec<String>>, _>(idx) {
        return v.map(|a| json!(a)).unwrap_or(serde_json::Value::Null);
    }
    serde_json::Value::Null
}
```

- [ ] **Step 2: Update `sources/mod.rs`** — add `pub mod pg_table;` and `pub use pg_table::PgTableSource;`.

- [ ] **Step 3: Remove `PgTableSource` block from `source.rs`** and add `pub use crate::sources::pg_table::PgTableSource;`.

- [ ] **Step 4: Verify**

Run: `cargo build -p chunkshop && cargo test -p chunkshop --test pg_table_source`
Expected: clean build; tests pass (or skip-if-no-DSN).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/sources/ rust/chunkshop/src/source.rs
git commit -m "refactor(sources): move PgTableSource and migrate to PostgresBackend"
```

---

## Phase F — Sum types + loaders + wire-up

### Task 22: Add `AnyBackend` enum + `load_backend` factory

**Files:**
- Modify: `rust/chunkshop/src/backends/mod.rs`

- [ ] **Step 1: Update `backends/mod.rs` with `AnyBackend` and `load_backend`**

Replace `rust/chunkshop/src/backends/mod.rs` with:

```rust
//! Backend module — connection management + dialect helpers per DB engine.

use anyhow::Result;

use crate::config::TargetConfig;

pub mod base;
pub mod postgres;

pub use base::{Backend, BackendConn, BackendDialect, ColSpec};
pub use postgres::PostgresBackend;

/// Transport sum type — used by the loader to hand a backend to load_sink,
/// where it's pattern-matched back to a concrete type. Sinks store concrete
/// backends (PgSink holds PostgresBackend), not AnyBackend. So this enum does
/// NOT impl Backend / BackendDialect / BackendConn — no match-delegate
/// boilerplate. R2/R3/R4 add new variants.
pub enum AnyBackend {
    Postgres(PostgresBackend),
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
    }
}
```

- [ ] **Step 2: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/backends/mod.rs
git commit -m "feat(backends): AnyBackend transport enum + load_backend factory"
```

---

### Task 23: Add `AnySink` enum + `impl Sink for AnySink` + `load_sink`

**Files:**
- Modify: `rust/chunkshop/src/sinks/mod.rs`

- [ ] **Step 1: Update `sinks/mod.rs`**

Replace `rust/chunkshop/src/sinks/mod.rs` with:

```rust
//! Sinks — chunkshop's per-backend data-model semantics layer.

use std::future::Future;

use anyhow::{anyhow, Result};

use crate::backends::AnyBackend;
use crate::chunker::Chunk;
use crate::config::TargetConfig;

pub mod base;
pub mod pg;

pub use base::Sink;
pub use pg::PgSink;

/// Sum type for runtime polymorphism. Pipeline holds `AnySink` and calls
/// trait methods through the match-delegate impl below.
pub enum AnySink {
    Pg(PgSink),
}

impl Sink for AnySink {
    fn create_table(&self) -> impl Future<Output = Result<()>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.create_table().await,
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
            }
        }
    }

    fn delete_document(&self, doc_id: &str) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.delete_document(doc_id).await,
            }
        }
    }

    fn count_docs(&self) -> impl Future<Output = Result<i64>> + Send {
        async move {
            match self {
                AnySink::Pg(s) => s.count_docs().await,
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
            }
        }
    }
}

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) => {
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim)))
        }
        // R2/R3/R4 add matched (Variant, Variant) arms. Cross-variant mismatches
        // are programming errors (load_backend + load_sink are always called
        // paired with the same TargetConfig).
        #[allow(unreachable_patterns)]
        _ => Err(anyhow!("backend / target type mismatch — programming error in load_sink dispatch")),
    }
}
```

- [ ] **Step 2: Verify**

Run: `cargo build -p chunkshop`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/sinks/mod.rs
git commit -m "feat(sinks): AnySink enum + Sink impl + load_sink factory"
```

---

### Task 24: Add `AnySource` enum + `iter_documents` impl + `load_source`

**Files:**
- Modify: `rust/chunkshop/src/sources/mod.rs`

- [ ] **Step 1: Update `sources/mod.rs`**

Replace `rust/chunkshop/src/sources/mod.rs` with:

```rust
//! Sources — input document iterators per backing store.

use anyhow::{anyhow, Result};

use crate::config::SourceConfig;

pub mod base;
pub mod files;
pub mod http;
pub mod json_corpus;
pub mod pg_table;
pub mod s3;

pub use base::Document;
pub use files::FilesSource;
pub use http::HttpSource;
pub use json_corpus::JsonCorpusSource;
pub use pg_table::PgTableSource;
pub use s3::S3Source;

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

impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
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
        SourceConfig::Http(c) => Ok(AnySource::Http(HttpSource::new(c.clone()))),
        SourceConfig::S3(c) => Ok(AnySource::S3(S3Source::new(c.clone()))),
        SourceConfig::Inline(_) => Err(anyhow!(
            "inline source is not used via load_source — Pipeline::new handles it directly"
        )),
    }
}
```

- [ ] **Step 2: Verify**

Run: `cargo build -p chunkshop`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/sources/mod.rs
git commit -m "feat(sources): AnySource enum + iter_documents impl + load_source factory"
```

---

### Task 25: Switch `runner.rs` to use `load_backend` / `load_sink` / `load_source`

**Files:**
- Modify: `rust/chunkshop/src/runner.rs`

**Important context:** `runner.rs` currently has its OWN local `AnySource` enum + `iter_documents` impl (around lines 105-127). The R1 work makes the one in `sources/mod.rs` (Task 24) canonical. This task deletes runner.rs's local `AnySource` and switches to the public one.

- [ ] **Step 1: Delete the local `AnySource` enum + `impl AnySource` block from runner.rs**

In `rust/chunkshop/src/runner.rs`, find and DELETE this entire block (the local enum currently lives there, around lines 105-127):

```rust
enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    Http(HttpSource),
    S3(S3Source),
}

impl AnySource {
    /// Async because PgTable + Http + S3 do network I/O; the file/JSON
    /// variants run sync work inside the async fn (no actual await). Caller
    /// is already in an async context (`run_cell` is async).
    async fn iter_documents(&self) -> Result<Vec<Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
            AnySource::PgTable(s) => s.iter_documents().await,
            AnySource::Http(s) => s.iter_documents().await,
            AnySource::S3(s) => s.iter_documents().await,
        }
    }
}
```

(The exact wording may differ slightly. Delete the `enum AnySource { ... }` block and its `impl AnySource { ... }` block in their entirety. The canonical one now lives in `crate::sources::AnySource`.)

- [ ] **Step 2: Update `runner.rs` imports**

Replace the existing source/sink imports:

```rust
use crate::sink::PgVectorSink;
use crate::source::{
    Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source,
};
```

with:

```rust
use crate::sources::{AnySource, Document};
```

(`FilesSource`, `JsonCorpusSource`, etc. are no longer needed in runner.rs since `load_source` constructs them internally.)

Also add `Context` to the anyhow import line if not already present:

Find:
```rust
use anyhow::{anyhow, Result};
```
Replace with:
```rust
use anyhow::{anyhow, Context, Result};
```

- [ ] **Step 3: Replace the inline source-construction match with `load_source`**

Find this block (around lines 145-160):

```rust
    let source: AnySource = match cfg.source {
        SourceConfig::Files(fc) => AnySource::Files(FilesSource::new(fc)),
        SourceConfig::JsonCorpus(jc) => AnySource::JsonCorpus(JsonCorpusSource::new(jc)),
        SourceConfig::PgTable(pc) => AnySource::PgTable(PgTableSource::new(pc)),
        SourceConfig::Http(hc) => AnySource::Http(HttpSource::new(hc)),
        SourceConfig::S3(sc) => AnySource::S3(S3Source::new(sc)),
        SourceConfig::Inline(_) => {
            return Err(anyhow!(
                "inline source has no auto-iterator: drive ingest from your app \
                 with chunkshop::Pipeline::from_yaml(...).ingest_text(doc_id, text, metadata). \
                 See docs/incremental.md (Pattern F) and docs/samples/inline-mode/."
            ));
        }
    };
```

Replace with:

```rust
    // Inline source still gets a special error path — load_source returns an
    // error for SourceConfig::Inline, but the message we want here is more
    // informative than the generic one.
    if matches!(cfg.source, SourceConfig::Inline(_)) {
        return Err(anyhow!(
            "inline source has no auto-iterator: drive ingest from your app \
             with chunkshop::Pipeline::from_yaml(...).ingest_text(doc_id, text, metadata). \
             See docs/incremental.md (Pattern F) and docs/samples/inline-mode/."
        ));
    }
    let source: AnySource = crate::sources::load_source(&cfg.source).context("load source")?;
```

- [ ] **Step 4: Replace the sink-construction block**

Find:

```rust
    let sink = PgVectorSink::connect(cfg.target, embedder.dim()).await?;

    info!("creating target table");
    sink.create_table().await?;
```

Replace with:

```rust
    let backend = crate::backends::load_backend(&cfg.target).context("load backend")?;
    let sink = crate::sinks::load_sink(&cfg.target, backend, embedder.dim())
        .context("load sink")?;

    info!("creating target table");
    use crate::sinks::Sink;
    sink.create_table().await?;
```

(The `use crate::sinks::Sink;` brings the trait into scope so `sink.create_table()` etc. resolve via the trait — required because `AnySink::create_table` is on the trait, not an inherent method.)

- [ ] **Step 5: Update the `write_document` call to pass `doc_id`**

The new `Sink::write_document` signature includes `doc_id: &str` as first param. Find the call inside the per-document loop (after the `for raw in docs.into_iter().take(limit)` line):

```rust
            sink.write_document(&chunks_with_meta, &embeddings, &tags_per_chunk)
                .await
                .context("write_document")?;
```

Replace with:

```rust
            sink.write_document(&raw.id, &chunks_with_meta, &embeddings, &tags_per_chunk)
                .await
                .context("write_document")?;
```

(`raw` is the active `Document` for that iteration — the variable name comes from the `for raw in docs.into_iter()` loop. If your runner.rs uses a different variable name, use that.)

- [ ] **Step 6: Verify the crate compiles**

Run: `cargo build -p chunkshop`
Expected: clean build.

- [ ] **Step 7: Run the parity + sink-modes tests**

Run: `cargo test -p chunkshop --test sink_modes_parity --test embedding_parity --test parity 2>&1 | tail -20`
Expected: all pass (or skip-if-no-DSN).

- [ ] **Step 8: Commit**

```bash
git add rust/chunkshop/src/runner.rs
git commit -m "refactor(runner): use load_backend / load_sink / load_source factories; drop local AnySource"
```

- [ ] **Step 6: Verify**

Run: `cargo build -p chunkshop`
Expected: clean build.

Run: `cargo test -p chunkshop --test sink_modes_parity --test embedding_parity --test parity 2>&1 | tail -20`
Expected: all pass (or skip-if-no-DSN).

- [ ] **Step 7: Commit**

```bash
git add rust/chunkshop/src/runner.rs
git commit -m "refactor(runner): use load_backend / load_sink / load_source factories"
```

---

### Task 26: Switch `pipeline.rs` to hold `AnySink`; migrate `delete_document` + `sample_row`

**Files:**
- Modify: `rust/chunkshop/src/pipeline.rs`

- [ ] **Step 1: Update Pipeline struct and `new()` to use AnySink**

In `rust/chunkshop/src/pipeline.rs`, change:

```rust
use crate::sink::PgVectorSink;
use crate::source::Document;
```
to:
```rust
use crate::sinks::{AnySink, Sink};
use crate::sources::Document;
```

Change the field type:
```rust
    sink: PgVectorSink,
```
to:
```rust
    sink: AnySink,
```

In `Pipeline::new()`, replace the sink construction:
```rust
        let TargetConfig::Postgres(target_cfg) = cfg.target.clone() else {
            unreachable!("R1 only ships TargetConfig::Postgres; R2/R3/R4 add variants");
        };
        let sink = PgVectorSink::connect(target_cfg, embedder.dim()).await?;
        sink.create_table().await?;
```
with:
```rust
        let backend = crate::backends::load_backend(&cfg.target).context("load backend")?;
        let sink = crate::sinks::load_sink(&cfg.target, backend, embedder.dim())
            .context("load sink")?;
        sink.create_table().await?;
```

- [ ] **Step 2: Update `ingest_document` to pass `doc_id` to `write_document`**

Find:
```rust
            self.sink
                .write_document(&chunks_with_meta, &embeddings, &tags_per_chunk)
                .await
                .context("write_document")?;
```

Replace with:
```rust
            self.sink
                .write_document(&fdoc.id, &chunks_with_meta, &embeddings, &tags_per_chunk)
                .await
                .context("write_document")?;
```

- [ ] **Step 3: Migrate `Pipeline::delete_document` to call `Sink::delete_document` trait method**

Replace the existing `Pipeline::delete_document` body (which uses raw pool access) with:

```rust
    /// Remove every chunk for a doc_id, scoped to this pipeline's source_tag.
    /// Returns the number of rows deleted. Mirrors Python's
    /// `Pipeline.delete_document`.
    pub async fn delete_document(&self, doc_id: &str) -> Result<u64> {
        Ok(self.sink.delete_document(doc_id).await? as u64)
    }
```

- [ ] **Step 4: Migrate `Pipeline::sample_row` to use `match` on `AnySink::Pg`**

Replace the existing `Pipeline::sample_row` body with:

```rust
    /// Used by the demo — return one row's text preview for stdout. PG-only
    /// (uses raw SQL via the underlying pool); other backends will need their
    /// own paths once R2/R3/R4 add variants.
    pub async fn sample_row(&self, doc_id: &str) -> Result<Option<(i32, String)>> {
        // Both let-bindings are irrefutable in R1 (single-variant enums). R2
        // introduces additional AnySink + TargetConfig variants — these
        // become refutable then and the compiler tells us where to add
        // match arms. Compile-fail is the right signal vs runtime panic.
        let AnySink::Pg(pg_sink) = &self.sink;
        let TargetConfig::Postgres(target) = &self.cfg.target;
        let fq = format!("\"{}\".\"{}\"", target.database_name, target.table);
        let stmt = format!(
            "SELECT seq_num, left(original_content, 80) FROM {tbl} \
             WHERE doc_id = $1 ORDER BY seq_num LIMIT 1",
            tbl = fq
        );
        let pool = pg_sink.pool().await?;
        let row = sqlx::query(&stmt)
            .bind(doc_id)
            .fetch_optional(pool)
            .await?;
        Ok(row.map(|r| (r.get::<i32, _>(0), r.get::<String, _>(1))))
    }
```

- [ ] **Step 5: Update `count_docs` to use trait method**

The current body is:
```rust
    pub async fn count_docs(&self) -> Result<i64> {
        self.sink.count_docs().await
    }
```
This still works — `Sink::count_docs` is on the trait. No change needed.

- [ ] **Step 6: Verify the crate compiles + run the full test suite**

Run: `cargo build -p chunkshop && cargo test -p chunkshop 2>&1 | tail -40`
Expected: clean build; all tests pass (or skip-if-no-DSN).

- [ ] **Step 7: Commit**

```bash
git add rust/chunkshop/src/pipeline.rs
git commit -m "refactor(pipeline): hold AnySink; migrate delete_document to Sink trait; sample_row matches AnySink::Pg"
```

---

## Phase G — Cleanup + parity

### Task 27: Delete old `sink.rs` + `source.rs`; update `lib.rs` re-exports

**Files:**
- Delete: `rust/chunkshop/src/sink.rs`
- Delete: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/lib.rs`

- [ ] **Step 1: Verify nothing in the codebase still references `crate::sink::` or `crate::source::`**

Run: `grep -rn "crate::sink::\|crate::source::\|use crate::sink\|use crate::source" rust/chunkshop/src/ rust/chunkshop/tests/`
Expected: empty output. If anything is found, replace `crate::sink::PgVectorSink` with `crate::sinks::PgSink` (or `AnySink`) and `crate::source::FilesSource` etc. with `crate::sources::FilesSource`.

- [ ] **Step 2: Delete the old files**

Run:
```bash
rm rust/chunkshop/src/sink.rs rust/chunkshop/src/source.rs
```

- [ ] **Step 3: Update `lib.rs`**

In `rust/chunkshop/src/lib.rs`:

Remove the now-stale module declarations:
```rust
pub mod sink;
pub mod source;
```

Remove the now-stale re-exports:
```rust
pub use sink::PgVectorSink;
pub use source::{Document, FilesSource};
```

Replace with:
```rust
pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ColSpec, PostgresBackend};
pub use sinks::{AnySink, PgSink, Sink};
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source};
```

The final `lib.rs` should look approximately like:

```rust
//! chunkshop-rs — Rust port of chunkshop.

pub mod backends;
pub mod bakeoff;
pub mod chunker;
pub mod config;
pub mod embedder;
pub mod extractor;
pub mod framer;
pub(crate) mod hf_cache;
pub mod pipeline;
pub mod runner;
pub mod sentence_split;
pub mod sinks;
pub mod sources;
pub mod summarizer;

pub use backends::{AnyBackend, Backend, BackendConn, BackendDialect, ColSpec, PostgresBackend};
pub use bakeoff::{run_bakeoff, run_bakeoff_with_base, BakeoffConfig, BakeoffResults};
pub use chunker::{Chunk, SentenceAwareChunker};
pub use config::{load_config, CellConfig};
pub use embedder::FastembedEmbedder;
pub use pipeline::Pipeline;
pub use runner::{run_cell, CellResult};
pub use sinks::{AnySink, PgSink, Sink};
pub use sources::{AnySource, Document, FilesSource, HttpSource, JsonCorpusSource, PgTableSource, S3Source};
```

- [ ] **Step 4: Verify the crate compiles + all tests pass**

Run: `cargo build -p chunkshop && cargo test -p chunkshop 2>&1 | tail -20`
Expected: clean build; all tests pass.

Run: `cargo doc -p chunkshop --no-deps 2>&1 | tail -10`
Expected: doc build clean. Verify `PgVectorSink` no longer appears in the rendered docs (look for `target/doc/chunkshop/struct.PgVectorSink.html` — should not exist).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/
git commit -m "refactor(lib): delete sink.rs + source.rs; update lib.rs re-exports for v4.0 module layout"
```

---

### Task 28: Cross-language dialect parity fixture

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/dialect-postgres.json`
- Create: `rust/chunkshop/tests/dialect_postgres_parity.rs`

The fixture is byte-for-byte expected outputs of `BackendDialect` methods. Both Python and Rust assert against it. (R1 ships the fixture and the Rust test; the Python-side parity test is added in a paired commit on the v4 worktree — out of scope for this plan but flagged as a follow-up.)

- [ ] **Step 1: Create `rust/chunkshop/tests/parity-fixtures/dialect-postgres.json`**

```json
{
  "backend": "postgres",
  "quote_ident": [
    {"in": "my_table", "out": "\"my_table\""},
    {"in": "abc", "out": "\"abc\""},
    {"in": "with_underscore_123", "out": "\"with_underscore_123\""}
  ],
  "fq_table": [
    {"in": ["public", "my_table"], "out": "\"public\".\"my_table\""},
    {"in": ["chunkshop", "test_chunks"], "out": "\"chunkshop\".\"test_chunks\""}
  ],
  "vector_type_ddl": [
    {"in": 384, "out": "vector(384)"},
    {"in": 1024, "out": "vector(1024)"},
    {"in": 1, "out": "vector(1)"}
  ],
  "json_path_sql": [
    {"in": ["metadata", "a"], "out": "metadata->>'a'"},
    {"in": ["metadata", "a.b"], "out": "metadata->'a'->>'b'"},
    {"in": ["metadata", "a.b.c"], "out": "metadata->'a'->'b'->>'c'"}
  ],
  "upsert_clause": [
    {"in": {"keys": ["id"], "updates": []}, "out": "ON CONFLICT (\"id\") DO NOTHING"},
    {"in": {"keys": ["id"], "updates": ["content"]}, "out": "ON CONFLICT (\"id\") DO UPDATE SET \"content\" = EXCLUDED.\"content\""},
    {"in": {"keys": ["id"], "updates": ["a", "b"]}, "out": "ON CONFLICT (\"id\") DO UPDATE SET \"a\" = EXCLUDED.\"a\", \"b\" = EXCLUDED.\"b\""},
    {"in": {"keys": ["a", "b"], "updates": ["c"]}, "out": "ON CONFLICT (\"a\", \"b\") DO UPDATE SET \"c\" = EXCLUDED.\"c\""}
  ],
  "create_database_sql": [
    {"in": "chunkshop", "out": "CREATE SCHEMA IF NOT EXISTS \"chunkshop\""},
    {"in": "my_db", "out": "CREATE SCHEMA IF NOT EXISTS \"my_db\""}
  ],
  "drop_table_sql": [
    {"in": "\"db\".\"t\"", "out": "DROP TABLE \"db\".\"t\""}
  ],
  "add_column_if_not_exists_sql": [
    {"in": ["\"db\".\"t\"", "source", "text"], "out": "ALTER TABLE \"db\".\"t\" ADD COLUMN IF NOT EXISTS \"source\" text"}
  ]
}
```

- [ ] **Step 2: Create `rust/chunkshop/tests/dialect_postgres_parity.rs`**

```rust
//! Cross-language dialect parity test. Both Python and Rust assert their
//! BackendDialect impls produce the byte-for-byte outputs in the fixture.

use chunkshop::backends::{BackendDialect, PostgresBackend};
use serde_json::Value;

const FIXTURE_PATH: &str = "tests/parity-fixtures/dialect-postgres.json";

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read parity fixture");
    serde_json::from_str(&raw).expect("parse parity fixture")
}

fn backend() -> PostgresBackend {
    PostgresBackend::new("UNUSED_FOR_DIALECT_PARITY".to_string())
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
        let keys: Vec<&str> = inp["keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        let updates: Vec<&str> = inp["updates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
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
```

- [ ] **Step 3: Run the parity tests**

Run: `cargo test -p chunkshop --test dialect_postgres_parity`
Expected: 8 tests pass.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/parity-fixtures/ rust/chunkshop/tests/dialect_postgres_parity.rs
git commit -m "test(parity): cross-language BackendDialect fixture + Rust assertions"
```

---

### Task 29: Final verification + summary

**Files:**
- (Verify only — no edits)

- [ ] **Step 1: Run the full test suite**

Run: `cargo test -p chunkshop 2>&1 | tail -30`
Expected: all tests pass (or skip-if-no-DSN where appropriate). No new test failures vs. main branch.

- [ ] **Step 2: Run `cargo clippy` to catch lint regressions**

Run: `cargo clippy -p chunkshop -- -D warnings 2>&1 | tail -40`
Expected: clean (no warnings promoted to errors).

- [ ] **Step 3: Run `cargo build --release` to verify release build**

Run: `cargo build -p chunkshop --release 2>&1 | tail -5`
Expected: clean release build.

- [ ] **Step 4: Verify drift checkpoints from the spec**

Confirm each Success Criterion (R1-SC-001 through R1-SC-010 in the spec) has evidence:

- [ ] R1-SC-001 — `tree rust/chunkshop/src/` shows `backends/`, `sinks/`, `sources/` directories; no `sink.rs` or `source.rs`. Run: `ls rust/chunkshop/src/`. Expected: no `sink.rs`, no `source.rs`; `backends/`, `sinks/`, `sources/` present.
- [ ] R1-SC-002 — clean build (already verified Step 1).
- [ ] R1-SC-003 — existing PG integration tests pass (already verified Step 1).
- [ ] R1-SC-004 — parity tests pass (already verified Step 1).
- [ ] R1-SC-005 — BackendDialect unit tests cover the methods listed in the spec. Run: `cargo test -p chunkshop --lib backends::postgres::tests`. Expected: 22 tests pass.
- [ ] R1-SC-006 — legacy-form rejection tests pass. Run: `cargo test -p chunkshop --test config_legacy_rejection`. Expected: 3 tests pass.
- [ ] R1-SC-007 — cross-language parity fixture passes. Run: `cargo test -p chunkshop --test dialect_postgres_parity`. Expected: 8 tests pass.
- [ ] R1-SC-008 — sample YAMLs round-trip through the Rust binary. Run: `cargo run -p chunkshop --bin chunkshop-rs -- ingest --config docs/samples/sample.yaml --dry-run` (if `--dry-run` exists; otherwise just verify `chunkshop-rs --help` parses the new YAML format). Expected: the binary accepts the new shape without error.
- [ ] R1-SC-009 — `lib.rs` re-exports updated. Run: `grep "PgVectorSink\|crate::sink\|crate::source" rust/chunkshop/src/lib.rs`. Expected: empty output. Verify exports listed in spec §4.6 are all present.
- [ ] R1-SC-010 — Pipeline does not call `pool()` directly via the Sink trait. Run: `grep -n "pool()" rust/chunkshop/src/pipeline.rs`. Expected: only one match — inside `sample_row`'s `AnySink::Pg(pg_sink)` arm (acceptable per spec §4.6).

- [ ] **Step 5: Write the CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS summary**

Capture this summary in your final response to the user (or in a commit message body, if preferred):

```
CHANGES MADE:
- Created backends/{base.rs, mod.rs, postgres.rs} — BackendDialect + BackendConn traits + PostgresBackend impl + AnyBackend transport enum + load_backend factory
- Created sinks/{base.rs, mod.rs, pg.rs} — Sink trait + PgSink (modes/foreign-tag/append-preflight/write_document/etc.) + AnySink + load_sink
- Created sources/{base.rs, mod.rs, files.rs, json_corpus.rs, pg_table.rs, http.rs, s3.rs} — split per-source files + AnySource + load_source. PgTableSource migrated to use PostgresBackend.
- Modified config.rs — TargetConfig becomes discriminated enum; PostgresTargetConfig struct extracted; schema → database rename; legacy-form rejection in load_config
- Modified runner.rs, pipeline.rs — switched to load_backend / load_sink / load_source; Pipeline holds AnySink; delete_document goes through Sink trait; sample_row matches AnySink::Pg
- Modified lib.rs — removed sink + source module declarations; updated re-exports
- Deleted sink.rs (480 LOC) and source.rs (475 LOC)
- Updated 4 sample YAMLs in docs/samples/ + inline test YAMLs to v0.4.0 shape (type: postgres, database:)
- Added cross-language parity fixture tests/parity-fixtures/dialect-postgres.json + Rust assertions

THINGS I DIDN'T TOUCH (intentionally):
- chunker.rs, embedder.rs, extractor.rs, framer.rs, hf_cache.rs, sentence_split.rs, summarizer.rs, bakeoff/ — not in R1 scope
- main.rs — only calls run_cell + bakeoff entrypoints, no direct sink/source access
- BackendConn tx parameter type stays PG-concrete (sqlx::Transaction<'_, Postgres>) — R2 (MariaDB) introduces the GAT/executor abstraction with a second concrete impl in hand
- chunkshop-rs --version literal fix — drive-by item handled in the CLI-FIX sub-project

POTENTIAL CONCERNS:
- AnySink in R1 has only one variant; the `let AnySink::Pg(pg_sink) = &self.sink` in Pipeline::sample_row is irrefutable now. R2 introduces additional variants — sample_row gains additional arms or graceful fallbacks at that point.
- Python-side cross-language dialect parity test is NOT in this branch — needs a paired commit on the experimental/v4-modular-backends Python worktree to assert against tests/parity-fixtures/dialect-postgres.json. Flagged as follow-up.
- Tags-literal divergence from Python (no `tags_literal` method in BackendDialect): each Sink binds tags natively per its driver. PgSink binds &[String] directly via sqlx; MariadbSink in R2 will JSON-encode. Documented in spec §4.2.
- The AnySink match-delegate in sinks/mod.rs uses `#[allow(unreachable_patterns)]` on the mismatch arm — needed because R1 has only one (TargetConfig, AnyBackend) variant pair; the unreachable arm is provably dead in R1 but becomes meaningful in R2. The allow is removed when R2 lands.
```

- [ ] **Step 6: Push the branch + open PR (if applicable, per your workflow)**

(Out of scope for the implementation plan — handled by the merge step described in the roadmap §6: `git merge --no-ff` from the integration branch's worktree.)

---

## Plan summary

**29 tasks across 7 phases.** Each task ends with a commit. Total commits: 29.

**Phase breakdown:**

| Phase | Tasks | Theme |
|---|---|---|
| A | 1–3 | Trait skeleton (compile-clean placeholders) |
| B | 4–10 | PostgresBackend impl (TDD on dialect + integration tests for I/O) |
| C | 11–13 | Config migration + legacy-form rejection (FIRST, so PgSink builds against final config shape) |
| D | 14–16 | PgSink impl |
| E | 17–21 | Source migration (file moves + pg_table uses Backend) |
| F | 22–26 | Sum types + loaders + wire-up (runner.rs, pipeline.rs) |
| G | 27–29 | Cleanup + parity fixture + final verification |

**Key invariants enforced:**
- The crate compiles after every task (no half-broken intermediate states).
- The existing test suite passes after every task that doesn't intentionally migrate test fixtures.
- New unit tests are TDD'd (write failing test first, then implement).
- All existing PG integration tests pass against the final R1 layout (no behavior change).
- Cross-language dialect outputs are byte-for-byte identical via the parity fixture.
