# chunkshop Rust pg_table Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-pg-table.md`.

**Goal:** Port `PgTableSource` from Python to Rust. ~25 LOC core; requires making `AnySource::iter_documents` async so sqlx queries can run.

**Architecture:** New `PgTableSourceConfig` variant on `SourceConfig`. New `PgTableSource` struct in `source.rs` with `async fn iter_documents`. Runner's `AnySource` enum wraps; `iter_documents` becomes `async fn`. Files + JsonCorpus keep their sync inner impls.

---

## Tasks

1. **Config:** add `PgTableSourceConfig` + `SourceConfig::PgTable` variant. Validate idents at load. Include `where_clause: Option<String>` (renamed via `#[serde(rename = "where")]`).
2. **Impl:** `PgTableSource::new(cfg)`; `async fn iter_documents(&self) -> Result<Vec<Document>>` reads DSN from env, builds quoted-identifier SELECT, runs sqlx query, materializes Documents.
3. **Runner:** `AnySource::iter_documents` becomes `async fn`. Files / JsonCorpus arms wrap sync impls. PgTable awaits the new async iter. `run_cell` awaits the call.
4. **Test:** `tests/pg_table_source.rs` — boot a temp schema, INSERT 3 rows, run PgTableSource, assert 3 Documents back. Skip-without-DSN.
5. **Regression:** `cargo test --workspace` GREEN. The framer + sink-modes integration tests already exercise `AnySource` through the new async path.
6. **Docs:** README + CHANGELOG.
7. **DC-FINAL + finishing-a-development-branch.**
