# R1 — Rust Modular Backends Skeleton — Design Spec

**Date:** 2026-05-05
**Status:** Draft (brainstorming complete, pending writing-plans)
**Sub-project:** R1 of the v0.4.0 finishing roadmap
**Worktree:** `/home/yonk/yonk-tools/chunkshop-rust-skeleton`
**Branch:** `experimental/v4-rust-backends-skeleton` (off `experimental/v4-modular-backends`)
**Roadmap parent:** [`2026-05-05-v4-finishing-roadmap-design.md`](2026-05-05-v4-finishing-roadmap-design.md)
**Architectural inheritance:** [`2026-04-30-v4-modular-backends-design.md`](2026-04-30-v4-modular-backends-design.md)

## 1. Goal

Refactor the Rust crate's PG-only sink + source code into the modular `backends/` + `sinks/` + `sources/` shape that mirrors the v4 Python layout. **No behavior change.** Postgres-only at the end of R1; R2 (MariaDB), R3 (SQLite), and R4 (ClickHouse) plug new backend impls into the trait surface this sub-project produces.

The output of R1 is the architectural foundation for Wave 2 of the roadmap. Done well, R2/R3/R4 each become a focused "add one backend impl" sub-project. Done poorly, every Wave-2 sub-project relitigates trait shape.

## 2. Non-goals

- Adding any backend other than Postgres. R2/R3/R4 own their respective impls.
- Generalizing `BackendConn` to be truly cross-backend. R1 ships PG-concrete tx parameter types; R2's first task is introducing the right abstraction (probably a GAT) once we have a second concrete impl to inform the shape.
- Connection pooling improvements, async I/O redesign, multi-backend bakeoff on Rust side, HNSW tuning, distance function selection, crates.io publishing. All deferred per roadmap §8.
- Fixing the `chunkshop-rs --version` literal (CLI-FIX is its own Wave-1 drive-by item).

## 3. Decisions settled in brainstorming

The four §9 questions from the roadmap, resolved:

| # | Question | Decision |
|---|---|---|
| Q1 | `Backend` trait shape: mirror Python one-for-one vs. lean on `sqlx::Database` | **Hybrid:** split into sync `BackendDialect` (pure helpers, all `-> String` / `-> Vec<String>`) + async `BackendConn` (I/O surface). Use AFIT (Rust ≥1.75 stable) and generic dispatch (`<B: Backend>`). No `async-trait` macro. No `dyn`. Super-trait `Backend: BackendDialect + BackendConn` for ergonomic bounds. |
| Q2 | Identifier safety: regex allowlist vs. sqlx params + thin validator | **Regex allowlist + `quote_ident()` doubles `"` defensively.** Matches Python verbatim (defense-in-depth). Regex `^[a-z_][a-z0-9_]*$` enforced at config-load (already in `config.rs::validate_ident`); `quote_ident` doubles any embedded `"`. |
| Q3 | Trait file location: `backends/mod.rs` vs. `backends/base.rs` | **`backends/base.rs` + `sinks/base.rs` hold the traits; `mod.rs` re-exports.** Mirrors Python's `base.py` for diff-review when porting. Public path stays clean (`chunkshop::backends::Backend`). Existing crate convention is `mod.rs`-style submodules (matching `bakeoff/mod.rs`). |
| Q4 | Refactor scope: what stays in `sink.rs` / `source.rs` | **Delete both files. Full split mirroring Python.** All 5 source impls move to per-file modules under `sources/`. Public-API rename `PgVectorSink → PgSink` folded in (matches Python; scales — won't have `MariaDbVectorSink`/`SqliteVectorSink`/etc. in the API). Pre-1.0 + experimental branch + V4-SC-006 already commits to YAML breaking changes — this is the right window. |

## 4. Architecture

### 4.1 Module layout (before / after)

**Before (current `rust/chunkshop/src/`):**

```
src/
├── sink.rs                 480 LOC, only PgVectorSink
├── source.rs               475 LOC, 5 source impls in one file
├── lib.rs                  re-exports PgVectorSink, FilesSource, Document
├── runner.rs               uses PgVectorSink directly
├── pipeline.rs             holds PgVectorSink field
├── config.rs               TargetConfig is flat struct (no `type` discriminator); field is `schema_name`
├── main.rs                 CLI; doesn't touch sink directly
└── ...                     chunker.rs, embedder.rs, extractor.rs, framer.rs, hf_cache.rs,
                            sentence_split.rs, summarizer.rs, bakeoff/  (untouched by R1)
```

**After R1:**

```
src/
├── backends/                       NEW
│   ├── mod.rs                      load_backend(cfg) factory + AnyBackend sum-type + re-exports
│   ├── base.rs                     BackendDialect + BackendConn + Backend super-trait + ColSpec
│   └── postgres.rs                 PostgresBackend (impls both traits)
├── sinks/                          NEW
│   ├── mod.rs                      load_sink(cfg, backend, dim) factory + AnySink sum-type + re-exports
│   ├── base.rs                     Sink trait
│   └── pg.rs                       PgSink — modes, foreign-tag safety, append preflight,
│                                   write_document, count_docs, delete_document, query_top_k
├── sources/                        NEW
│   ├── mod.rs                      load_source(cfg) factory + AnySource sum-type + re-exports
│   ├── base.rs                     Document struct (extracted from current source.rs)
│   ├── files.rs                    file move (no semantic change)
│   ├── json_corpus.rs              file move
│   ├── pg_table.rs                 migrates to use PostgresBackend for connection + quote_ident
│   ├── http.rs                     file move
│   └── s3.rs                       file move
├── lib.rs                          re-exports updated
├── runner.rs                       uses load_backend / load_sink / load_source
├── pipeline.rs                     `sink: AnySink` (was `PgVectorSink`); stays non-generic
├── config.rs                       TargetConfig becomes discriminated enum (1 variant: Postgres);
                                    schema → database rename; legacy-form pre-parse rejection
└── ...                             unchanged: chunker.rs, embedder.rs, extractor.rs, framer.rs,
                                    hf_cache.rs, sentence_split.rs, summarizer.rs, main.rs, bakeoff/

DELETED: sink.rs, source.rs
```

### 4.2 The `Backend` trait surface

`backends/base.rs`:

```rust
#[derive(Debug, Clone)]
pub struct ColSpec {
    pub name: &'static str,
    pub type_ddl: String,         // backend-specific, e.g. "text" or "VARCHAR(255)"
    pub nullable: bool,
    pub default: Option<&'static str>,
    pub is_primary_key: bool,
}

/// Pure dialect helpers. No I/O, no async. Each method returns a String (or Vec<String>)
/// that the Sink composes into SQL. Trivially unit-testable without a tokio runtime.
pub trait BackendDialect {
    const NAME: &'static str;             // "postgres"
    const SUPPORTS_UPSERT: bool;          // CH=false; PG/MariaDB/SQLite=true

    // Identifier safety (Q2: regex allowlist enforced at config-load + quote-doubling here)
    fn quote_ident(&self, name: &str) -> String;
    fn fq_table(&self, db: &str, table: &str) -> String;

    // Type DDL fragments
    fn vector_type_ddl(&self, dim: usize) -> String;
    fn json_type_ddl(&self) -> String;
    fn tags_array_type_ddl(&self) -> String;
    fn text_pk_type_ddl(&self) -> String;
    fn timestamp_now_default_ddl(&self) -> String;

    // Value literals (SQL-bindable strings; per-backend cast in placeholders)
    fn vector_literal(&self, arr: &[f32]) -> String;
    fn json_literal(&self, obj: &serde_json::Value) -> String;

    // Composition
    fn json_path_sql(&self, col_expr: &str, dotted_path: &str) -> String;
    fn upsert_clause(&self, key_cols: &[&str], update_cols: &[&str]) -> String;

    // DDL primitives
    fn create_database_sql(&self, name: &str) -> String;
    fn add_column_if_not_exists_sql(&self, fq: &str, col: &str, type_ddl: &str) -> String;
    fn drop_table_sql(&self, fq: &str) -> String;

    // Composite DDL — backend handles HNSW timing differences
    fn emit_chunks_table_ddl(
        &self,
        fq: &str,
        cols: &[ColSpec],
        hnsw: bool,
        dim: usize,
        engine: Option<&str>,
    ) -> Vec<String>;
}

/// I/O surface. AFIT (Rust ≥1.75, stable). No `async-trait` macro, no `dyn`.
///
/// **R1 caveat (deliberate seam):** the tx parameter type is PG-concrete
/// (`sqlx::Transaction<'_, sqlx::Postgres>`). R2 (MariaDB) introduces the GAT or
/// executor abstraction that makes this truly cross-backend, because the right
/// shape can only be designed once we have a second concrete impl.
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

/// Convenience super-trait for generic bounds: `<B: Backend>` everywhere.
pub trait Backend: BackendDialect + BackendConn {}
impl<T: BackendDialect + BackendConn> Backend for T {}
```

**Notable divergence from Python:** Python's `Backend` Protocol has `tags_literal` (returns `list[str]` for PG; JSON-string for MariaDB). Rust drops it from the trait — sqlx's static `Encode` types make a unified return type lossy. Each Sink binds tags natively per its driver (PG binds `&[String]` directly; MariaDB will bind a JSON string). Documented in the Sink layer.

**Reasoning for the PG-concrete `BackendConn` seam:** Rust's static typing forces a concrete type for the `tx` parameter. R3 (SQLite) may pick `rusqlite` per §9 of the roadmap (better extension-loading story for `sqlite-vec`), and R4 (ClickHouse) is HTTP-based — neither shares sqlx's `Transaction` type. Designing the abstraction speculatively in R1 with one impl is overfitting; we'd guess wrong and refactor at R2 anyway. **Better to ship the seam, document it, let R2 own the next layer of abstraction with a second concrete impl in hand.**

### 4.3 The `Sink` trait surface

`sinks/base.rs`:

```rust
use anyhow::Result;
use std::future::Future;
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

5 methods, mirroring Python's `sinks/base.py` Protocol exactly. **`PgSink` owns** mode dispatch (overwrite/append/create_if_missing), foreign-tag safety, append preflight, source write-once, `delete_orphans`, promote-metadata column ensure, write_document row composition. The 480-line `PgVectorSink` becomes ~230-line `PgSink` after extracting the dialect-shaped helpers into `PostgresBackend`.

### 4.4 Identifier-safety policy (Q2)

Two-layer defense, both preserved from Python:

1. **Regex allowlist at config-load** (already exists in `config.rs::validate_ident`). Identifiers `database`, `table`, `source_tag`, `PromoteColumn.column_name` segments must match `^[a-z_][a-z0-9_]*$`. Failures error at config-load, not at runtime — friendly UX.
2. **`quote_ident()` doubles `"` defensively** (new). Even if the regex were ever widened, embedded `"` characters in identifiers can't break out of the SQL quoting. Matches Python's `'"' + name.replace('"', '""') + '"'`.

Trait method `BackendDialect::quote_ident(&str) -> String` — each impl owns its quote character (`"` for PG; backtick for MariaDB; `"` for SQLite; `"` for CH).

### 4.5 Config + loaders

**`TargetConfig` becomes a discriminated enum** (matching the existing crate convention used by `SourceConfig`, `ChunkerConfig`, `EmbedderConfig`, `ExtractorConfig`, `FramerConfig`):

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    // R2/R3/R4 add: Mariadb, Sqlite, Clickhouse
}

#[derive(Debug, Clone, Deserialize)]
pub struct PostgresTargetConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,           // was schema_name (rename: schema → database)
    pub table: String,
    #[serde(default = "default_mode")]
    pub mode: String,                    // overwrite | append | create_if_missing
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    #[serde(default)]
    pub delete_orphans: bool,
}
```

**Legacy-form rejection** — `load_config` adds a pre-deserialize pass that parses the YAML as `serde_yml::Value` and walks for legacy keys before deserializing into typed structs. This satisfies V4-SC-006 ("rejected with clear error"):

| Legacy form | Error message |
|---|---|
| `target.type: pgvector` | "target.type 'pgvector' was renamed to 'postgres' in v0.4.0. Update your YAML." |
| `target.schema:` (top-level) | "target.schema was renamed to target.database in v0.4.0. Update your YAML." |
| `target.overwrite: true` (legacy bool) | "target.overwrite was replaced by target.mode: 'overwrite' in v0.4.0." |

Without the pre-pass, serde's default errors are cryptic ("unknown variant `pgvector`, expected `postgres`"). Cost: ~30 lines, fenced in one helper in `config.rs`.

**Loaders — sum-type enums** (the no-`dyn` constraint from Q1/C means runtime polymorphism uses sum types):

```rust
// backends/mod.rs
//
// AnyBackend is a TRANSPORT sum type — used by the loader to hand a backend to
// load_sink, where it's pattern-matched back to a concrete type. Sinks store
// concrete backends (PgSink holds PostgresBackend), not AnyBackend. So this
// enum does NOT impl Backend / BackendDialect / BackendConn — no match-delegate
// boilerplate. Trait methods on a backend are always called against a known
// concrete type.
pub enum AnyBackend {
    Postgres(PostgresBackend),
    // R2/R3/R4 add variants
}

pub fn load_backend(cfg: &TargetConfig) -> Result<AnyBackend> {
    match cfg {
        TargetConfig::Postgres(t) => Ok(AnyBackend::Postgres(PostgresBackend::new(t.dsn_env.clone()))),
    }
}
```

```rust
// sinks/mod.rs
pub enum AnySink {
    Pg(PgSink),
    // R2/R3/R4 add variants
}

impl Sink for AnySink { /* match-delegate all 5 async methods */ }

pub fn load_sink(cfg: &TargetConfig, backend: AnyBackend, dim: usize) -> Result<AnySink> {
    match (cfg, backend) {
        (TargetConfig::Postgres(t), AnyBackend::Postgres(b)) =>
            Ok(AnySink::Pg(PgSink::new(t.clone(), b, dim))),
        // R2/R3/R4: each adds matched (Variant, Variant) arm.
        // Cross-variant mismatches → unreachable!() — load_backend and load_sink
        // are always called paired with the same TargetConfig.
    }
}
```

```rust
// sources/mod.rs
pub enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
    PgTable(PgTableSource),
    Http(HttpSource),
    S3(S3Source),
    // R2/R3/R4 add: MariadbTable, SqliteTable, ClickhouseTable (CH source deferred to v4.1)
}

impl AnySource {
    pub async fn iter_documents(&self) -> Result<Vec<Document>> { /* match-delegate */ }
}

pub fn load_source(cfg: &SourceConfig) -> Result<AnySource> { /* dispatch on existing enum */ }
```

The `match`-delegate boilerplate is the cost of the no-`dyn` constraint, but only **`AnySink` needs trait-impl boilerplate** — Pipeline holds `AnySink` and calls Sink methods on it. `AnyBackend` and `AnySource` are pure transport sum types: they're pattern-matched back to concrete types at construction time (load_sink), and trait method calls always go through concrete types thereafter. R1 has **5 method-match-arms in `impl Sink for AnySink`**, each with one entry. Each new R2/R3/R4 backend adds 5 match arms to `AnySink`. `AnySource` similarly needs match-delegate only on the methods Runner actually calls (`iter_documents`).

### 4.6 Callsite migrations

**`runner.rs`** — current:
```rust
let sink = PgVectorSink::connect(cfg.target, embedder.dim()).await?;
sink.create_table().await?;
```
becomes:
```rust
let backend = backends::load_backend(&cfg.target)?;
let sink = sinks::load_sink(&cfg.target, backend, embedder.dim())?;
sink.create_table().await?;
```
Same change to source construction: `let source = sources::load_source(&cfg.source)?;` and call `source.iter_documents().await?` via `AnySource`.

**`pipeline.rs`** — `sink: PgVectorSink` becomes `sink: AnySink`. **Pipeline stays non-generic.** Rationale: `AnySink`'s match-dispatch cost is meaningless for query workloads, and a generic `Pipeline<S: Sink>` would force every caller to thread the type parameter. R2/R3/R4 grow `AnySink`; `Pipeline` stays unchanged.

The `pool()` accessor disappears from the public surface — the two callsites (`Pipeline::delete_document` and `Pipeline::query_top_k`) now go through the `Sink` trait methods. If anything else internal needs raw pool access, it stays as an inherent method on `PgSink` only.

**`lib.rs`** re-exports update:
```rust
pub mod backends;
pub mod sinks;
pub mod sources;
// removed: pub mod sink; pub mod source;

pub use backends::{Backend, BackendDialect, BackendConn, ColSpec, AnyBackend, PostgresBackend};
pub use sinks::{Sink, AnySink, PgSink};            // PgVectorSink removed
pub use sources::{Document, AnySource, FilesSource, JsonCorpusSource, PgTableSource,
                  HttpSource, S3Source};
```

External breakage scope: zero non-repo callers exist (no crates.io publish for v0.4.0 per roadmap §6). Internal callers (runner.rs, pipeline.rs, tests, samples) all in scope for R1.

**`main.rs`** — unchanged. Calls `run_cell` and bakeoff entrypoints; doesn't touch the sink directly.

## 5. Test strategy

R1 is "no behavior change" — the existing test suite IS the primary verification.

### 5.1 Existing tests must pass with minimal updates

19 integration test files in `rust/chunkshop/tests/` plus `#[cfg(test)]` blocks throughout `src/`:

- `embedding_parity.rs`, `parity.rs`, `hierarchy_parity.rs`, `sink_modes_parity.rs`, `pg_table_source.rs`, `jsonpath_parity.rs`, `http_source.rs`, `json_corpus_source.rs`, `s3_source.rs`, `semantic_smoke.rs`, `oversize.rs`, `summary_embed_parity.rs`, `extractor_*.rs`, `fixed_overlap_parity.rs`, `heading_boundary_parity.rs`, `hierarchical_summary_parity.rs`, `neighbor_expand_parity.rs`, `semantic_warning.rs`.
- YAML literals embedded in tests get the field rename: `schema: x` → `database: x`, plus `type: postgres` added under `target:`. Same change to the 4 sample YAMLs in `docs/samples/`.
- **If any test needs test-logic changes (not just YAML field renames), that's a behavior-change red flag worth investigating before committing.**

### 5.2 New unit tests for R1 (no DB required)

Pure-function dialect tests — the testability win Q1/C unlocks:

- `tests/backend_dialect_postgres.rs` (or `#[cfg(test)]` in `backends/postgres.rs`):
  - `quote_ident` doubles embedded `"` correctly
  - `fq_table` quotes both segments
  - `vector_type_ddl(384) == "vector(384)"`
  - `json_path_sql` composition matches Python byte-for-byte (see §5.4)
  - `upsert_clause` for typical key/update sets
  - `emit_chunks_table_ddl` produces correct statement count with/without `hnsw=true`

### 5.3 New unit tests for legacy-form rejection (config-only, no DB)

- `target.type: pgvector` → friendly error message names the new value
- `target.schema:` → friendly error message names the new field
- `target.overwrite: true` → friendly error message names `mode`

Each error message must explicitly reference the new field/value (V4-SC-006).

### 5.4 Cross-language byte-for-byte parity for `BackendDialect` outputs

A small fixture file (`tests/parity-fixtures/dialect-postgres.json`) lists input/expected output pairs for `quote_ident`, `fq_table`, `json_path_sql`, `upsert_clause`, `emit_chunks_table_ddl`, etc. **Both Python and Rust assert against the same fixture.** Cheap insurance against subtle PG-dialect divergence creeping in over R2/R3/R4 development.

One file, ~20 entries, both languages run the same checks.

### 5.5 Existing PG integration tests (skip-if-no-DSN)

Same `CHUNKSHOP_TEST_DSN` env var. Same `docker-compose.test.yaml`. Same skip discipline. R1 doesn't add new infrastructure.

## 6. Success criteria

| ID | Criterion | Verification |
|---|---|---|
| **R1-SC-001** | `backends/`, `sinks/`, `sources/` directories exist with the layout in §4.1; `sink.rs` + `source.rs` deleted | `tree rust/chunkshop/src/` matches §4.1 |
| **R1-SC-002** | Crate compiles with `cargo build -p chunkshop` and `cargo build -p chunkshop --release`; no warnings introduced | clean build |
| **R1-SC-003** | All existing PG integration tests pass after YAML field updates | `cargo test -p chunkshop` clean run with `CHUNKSHOP_TEST_DSN` set |
| **R1-SC-004** | All existing parity tests pass (cross-language vector parity preserved) | `cargo test -p chunkshop` clean run |
| **R1-SC-005** | `BackendDialect` unit tests cover quote_ident, fq_table, vector_type_ddl, json_path_sql, upsert_clause, emit_chunks_table_ddl | new tests pass |
| **R1-SC-006** | Legacy-form YAML produces friendly errors (`pgvector`, `schema`, `overwrite: true`) | new config-validation tests pass |
| **R1-SC-007** | Cross-language dialect parity fixture passes both Python and Rust | both `pytest` and `cargo test` assert against `tests/parity-fixtures/dialect-postgres.json` |
| **R1-SC-008** | 4 sample YAMLs in `docs/samples/` updated to v4.0 field names; round-trip through Rust binary cleanly | `chunkshop-rs ingest --config X.yaml` succeeds for each |
| **R1-SC-009** | `lib.rs` re-exports updated; `PgVectorSink` no longer in public API; `PgSink`, `AnySink`, `AnyBackend`, `AnySource`, trait re-exports present | `cargo doc -p chunkshop` inspection |
| **R1-SC-010** | `Pipeline::delete_document` and `Pipeline::query_top_k` go through `Sink` trait methods (no `pool()` accessor on public surface) | grep `pool()` in `pipeline.rs`; should not appear |

## 7. Drift checkpoints

- **DC-R1-A** (after directory skeleton + trait declarations): `backends/`, `sinks/`, `sources/` directories created with traits + 1 variant each; `sink.rs` + `source.rs` deleted. **R1-SC-001, R1-SC-002 green.**
- **DC-R1-B** (after migration of impls): All existing PG tests + parity tests pass against new layout. New BackendDialect unit tests pass. New legacy-form rejection tests pass. Cross-language dialect parity fixture passes both languages. **R1-SC-003 through R1-SC-007 green.**
- **DC-R1-FINAL**: `lib.rs` re-exports updated. Sample YAMLs migrated. Public surface verified (`pool()` gone, new exports present). `CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS` summary written. Branch ready for merge to `experimental/v4-modular-backends`. **R1-SC-008 through R1-SC-010 green.**

## 8. Out of scope

Owned by other sub-projects of the v0.4.0 roadmap:

- **R2 (MariaDB)**: backend impl, MariaDB driver pick, version floor (≥11.7), `VEC_FromText` literal handling. **R2's first task is generalizing `BackendConn`** — replacing R1's PG-concrete `sqlx::Transaction<'_, Postgres>` parameter with a GAT (`type Tx<'a>: Send;`) or executor-trait abstraction.
- **R3 (SQLite)**: backend impl, `sqlx::Sqlite` vs `rusqlite` pick, two-table dance, WAL mode, HNSW degradation warning.
- **R4 (ClickHouse)**: backend impl, driver pick, append-only semantics, `delete_orphans` no-op + warn, `ReplacingMergeTree` opt-in, streaming reads.
- **RT (Wave-3)**: 16-cell cross-backend matrix test on Rust side.
- **P1 (Wave-1, parallel)**: Python ClickHouse source. R1 is Rust-only.
- **CLI-FIX (Wave-1, drive-by)**: `chunkshop-rs --version` literal fix.

Permanently deferred for v0.4.0 (per predecessor spec §8 + roadmap §8):

- Connection pooling improvements (current per-document short-lived connection model preserved).
- Async I/O redesign — Rust is already async via tokio; AFIT is the only structural change.
- Cross-backend bakeoff on Rust side (bakeoff stays PG-only; Python side has multi-backend bakeoff).
- Rich HNSW tuning per backend (`hnsw: bool` is the only knob).
- Vector distance function selection (cosine hardcoded).
- crates.io publishing (Rust crate stays unpublished for v0.4.0 per roadmap §6).
- Migration scripts / `ALTER TABLE` from 0.3.x → 0.4.0 (re-ingest is policy).

## 9. References

- Roadmap parent: [`2026-05-05-v4-finishing-roadmap-design.md`](2026-05-05-v4-finishing-roadmap-design.md)
- Architectural inheritance: [`2026-04-30-v4-modular-backends-design.md`](2026-04-30-v4-modular-backends-design.md) — §4.2 (Backend Protocol shape), §4.3 (Sink Protocol), §4.4 (Sink portability matrix), §5 (YAML config shape)
- Python implementation reference: `/home/yonk/yonk-tools/chunkshop-v4/python/src/chunkshop/{backends,sinks,sources}/` on `experimental/v4-modular-backends`
- Current Rust state: `rust/chunkshop/src/{sink.rs,source.rs,config.rs}` on `main`
- Repo convention: `CLAUDE.md` — module layout, identifier-safety policy, mode semantics, schema-flex contract
