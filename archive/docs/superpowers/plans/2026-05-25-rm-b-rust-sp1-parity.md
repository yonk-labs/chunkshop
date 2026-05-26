# RM-B Rust Parity for SP-1 Sync Primitives + Source Enhancements

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development OR superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Audit basis:** Honest gap analysis 2026-05-25 — Python shipped SP-1 sync primitives + pg_table/http/s3 incremental enhancements + RawStore primitive; Rust crate did not. Versions are nominally sync'd (Cargo.toml + pyproject.toml both at 0.6.0) but the Rust ingest behavior is functionally v0.5.0. Vectors are still cross-language compatible (target-table schema unchanged) — this plan closes the *behavioral* parity gap for users who pick the Rust crate.

**Goal:** Bring the Rust chunkshop crate to behavioral parity with chunkshop-python 0.6.0 on the foundational ingest primitives that landed in SP-1 + their pg_table/http/s3 follow-ups. **Explicitly out of scope** per spec D6: connector lifts (gdrive/github/blob/rss/slack/notion/dropbox/gitlab + 20 stubs), codeparse foundation, code_aware + symbol_aware chunkers, code_relationships + code_summary extractors, comment_extracts source, OAuth providers, file parsers (PDF/DOCX/etc.) — those stay Python-only per the SP-1 spec.

**Tracks:** "RM-B" — sibling to RM-A (which ported memory primitives to Rust per `docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md`).

**Tech stack:** Rust (Edition 2021), serde, anyhow, thiserror; sqlx (already in core?), reqwest or hyper for HTTP, aws-sdk-s3 for S3 if not already wired. No new heavy deps.

**Reference Python implementations** to mirror behavior + tests:

| Surface | Python file | Rust target file |
|---|---|---|
| SyncMode / IncrementalSource / PrunableSource / StaleCursorError | `python/src/chunkshop/sources/base.py` | `rust/chunkshop/src/sources/base.rs` |
| pg_table tuple cursor | `python/src/chunkshop/sources/pg_table.py` (`iter_changes_since`) | `rust/chunkshop/src/sources/pg_table.rs` |
| http depth-crawl + ETag/Last-Modified cursor | `python/src/chunkshop/sources/http.py` | `rust/chunkshop/src/sources/http.rs` |
| s3 ETag IncrementalSource | `python/src/chunkshop/sources/s3.py` | `rust/chunkshop/src/sources/s3.rs` |
| RawStore Protocol + local + s3 backends | `python/src/chunkshop/raw_store/{base,local,s3}.py` | `rust/chunkshop/src/raw_store/{mod,local,s3}.rs` (new) |

---

## Task 0: Pre-flight — audit current Rust source/base traits

**Files:** none yet (read-only).

- [ ] **Step 1:** Read `rust/chunkshop/src/sources/base.rs` end-to-end. Document what Source trait shape currently exists, what associated types it carries, and whether it has any cursor/sync notion at all.
- [ ] **Step 2:** Read the matching Python file `python/src/chunkshop/sources/base.py` for the v0.6.0 shape:
  - `SyncMode` enum (FULL_RESYNC / CURSOR / FINGERPRINT)
  - `Document` dataclass (gains `fingerprint: str | None`)
  - `IncrementalSource` Protocol (`empty_cursor`, `iter_changes_since`, `cursor_from`)
  - `PrunableSource` Protocol (`empty_prune_cursor`, `iter_deleted_since`)
  - `StaleCursorError` exception
- [ ] **Step 3:** Draft (in a scratch file or this task's notes) the Rust translation choice: trait + associated `Cursor` type? Trait object with `Box<dyn IncrementalSource>`? Open-ended generic? Pick whichever matches the existing `Source` trait's style.

No commit. Decision goes into task notes for downstream tasks.

---

## Task 1: SyncMode + IncrementalSource + PrunableSource traits + StaleCursorError

**Files:**
- Modify: `rust/chunkshop/src/sources/base.rs`
- Test: `rust/chunkshop/tests/sync_primitives.rs` (new)

- [ ] **Step 1: Write the failing tests**

```rust
// rust/chunkshop/tests/sync_primitives.rs
use chunkshop::sources::base::{Document, IncrementalSource, SyncMode, StaleCursorError, PrunableSource};
use std::collections::BTreeMap;

#[test]
fn sync_mode_serde() {
    // SyncMode round-trips through serde as a kebab-case string.
    let m = SyncMode::Cursor;
    let s = serde_json::to_string(&m).unwrap();
    assert_eq!(s, "\"cursor\"");
    let back: SyncMode = serde_json::from_str(&s).unwrap();
    assert!(matches!(back, SyncMode::Cursor));
}

#[test]
fn document_carries_fingerprint() {
    let d = Document {
        id: "d1".into(),
        content: "hello".into(),
        title: Some("Hi".into()),
        metadata: BTreeMap::new(),
        fingerprint: Some("sha256:abc".into()),
    };
    assert_eq!(d.fingerprint.as_deref(), Some("sha256:abc"));
}

#[test]
fn stale_cursor_error_displays_cleanly() {
    let e = StaleCursorError::new("server-side cursor expired");
    assert!(format!("{e}").contains("stale cursor"));
}

// Smoke test: a hand-rolled IncrementalSource impl
struct _FakeSource { /* state */ }
impl IncrementalSource for _FakeSource {
    type Cursor = BTreeMap<String, String>;
    fn empty_cursor(&self) -> Self::Cursor { BTreeMap::new() }
    fn iter_changes_since(&self, _cursor: &Self::Cursor) -> Result<Box<dyn Iterator<Item = Document>>, anyhow::Error> { Ok(Box::new(std::iter::empty())) }
    fn cursor_from(&self, _doc: &Document) -> Self::Cursor { BTreeMap::new() }
}
```

- [ ] **Step 2:** `cargo test --test sync_primitives` → FAIL (types don't exist).
- [ ] **Step 3: Implement**

Add to `rust/chunkshop/src/sources/base.rs`:

```rust
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

/// How a Source detects changes between runs. Mirrors `chunkshop.sources.base.SyncMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SyncMode {
    /// Re-emit all; consumer dedups by content hash.
    FullResync,
    /// Implements `IncrementalSource`; consumer persists cursor.
    Cursor,
    /// Enumerate all w/ per-doc fingerprint; consumer diffs.
    Fingerprint,
}

impl Default for SyncMode {
    fn default() -> Self { SyncMode::FullResync }
}

/// Raised by `iter_changes_since` when a server-side cursor is too old.
/// Consumers should treat this as a signal to fall back to a full resync.
#[derive(Debug, Error)]
#[error("stale cursor: {0}")]
pub struct StaleCursorError(String);

impl StaleCursorError {
    pub fn new(msg: impl Into<String>) -> Self { Self(msg.into()) }
}

/// Sources that support cursor-based incremental sync.
///
/// Cursor shape is source-specific (ETag map for S3, tuple for DB tables,
/// HEAD-SHA for git, opaque page token for APIs). Consumers treat it as opaque
/// and persist it between calls. chunkshop never stores it.
///
/// `cursor_from(doc)` returns a per-doc DELTA. Consumers build the next cursor
/// by starting from the previous cursor and merging each emitted doc's delta:
///
///   let mut next = prev.clone();
///   for d in docs { next.extend(source.cursor_from(d)); }
pub trait IncrementalSource {
    /// Per-source cursor type. Must serde-round-trip via serde_json::Value.
    type Cursor: Default + Serialize + for<'de> Deserialize<'de> + Clone;

    fn empty_cursor(&self) -> Self::Cursor;
    fn iter_changes_since(
        &self,
        cursor: &Self::Cursor,
    ) -> Result<Box<dyn Iterator<Item = Document>>, anyhow::Error>;
    fn cursor_from(&self, last_document: &Document) -> Self::Cursor;
}

/// Sources that can enumerate source-side deletions.
/// Typically called at lower cadence than `iter_changes_since`. Returns
/// source-IDs (Document.id), not Documents.
pub trait PrunableSource {
    type Cursor: Default + Serialize + for<'de> Deserialize<'de> + Clone;

    fn empty_prune_cursor(&self) -> Self::Cursor;
    fn iter_deleted_since(
        &self,
        cursor: &Self::Cursor,
    ) -> Result<Box<dyn Iterator<Item = String>>, anyhow::Error>;
}
```

Also: extend the existing `Document` struct to include `fingerprint: Option<String>` (default `None`). This must NOT break any existing Rust call sites — confirm via `cargo check --all-targets`.

The base `Source` trait gains a `fn sync_mode(&self) -> SyncMode { SyncMode::FullResync }` default-impl method so existing sources auto-inherit `FullResync`.

- [ ] **Step 4:** `cargo test --test sync_primitives` → PASS.
- [ ] **Step 5:** Full suite still green: `cargo test --workspace`.
- [ ] **Step 6: Commit**

```
feat(rust/sources): add SyncMode + IncrementalSource + PrunableSource + StaleCursorError

RM-B Task 1. Mirrors Python's chunkshop.sources.base (v0.6.0). Document
gains optional fingerprint field. Source trait gains sync_mode() default
returning FullResync so existing sources are unaffected.
```

---

## Task 2: pg_table tuple cursor `{after_ts, after_id}` mirror

**Files:**
- Modify: `rust/chunkshop/src/sources/pg_table.rs`
- Test: `rust/chunkshop/tests/pg_table_tuple_cursor.rs` (new)

The Python fix (commit `ff01268`) replaced `WHERE updated_at_col > %s ORDER BY updated_at_col` with `WHERE (updated_at_col, id_col::text) > (%s, %s) ORDER BY updated_at_col, id_col::text`. This defends against silent row loss when a row commits at the boundary timestamp.

- [ ] **Step 1: Write a failing test** that mirrors the Python `test_pg_table_handles_row_inserted_at_cursor_boundary`:

```rust
// rust/chunkshop/tests/pg_table_tuple_cursor.rs (DB-backed; gated on $CHUNKSHOP_TEST_DSN)
// 1. seed table with row c1@T
// 2. first sync → emit c1, cursor advances to {after_ts: T, after_id: "c1"}
// 3. INSERT c2@T (same timestamp)
// 4. second sync with the advanced cursor must emit {c2}
```

- [ ] **Step 2:** Run → FAIL on current Rust pg_table.
- [ ] **Step 3:** Mirror the Python implementation:
  - Cursor shape: `{"after_ts": <iso str>, "after_id": <str>}`
  - Query uses tuple comparison + ::text cast on id_col for type uniformity
  - `cursor_from(doc)` returns the delta for that doc
  - Empty cursor falls through to full table scan in canonical order
- [ ] **Step 4:** Test PASSES. Add the `IncrementalSource` impl to PgTableSource.
- [ ] **Step 5:** Run `tests/pg_table_source.rs` (existing) + the new test together — both pass.
- [ ] **Step 6: Commit** `fix(rust/sources): pg_table tuple cursor for boundary-row safety (RM-B Task 2)`.

---

## Task 3: s3 source ETag IncrementalSource impl

**Files:**
- Modify: `rust/chunkshop/src/sources/s3.rs`
- Test: `rust/chunkshop/tests/s3_incremental.rs` (new)

The Python impl (commit `f875450` + review fixes) uses `{key: etag}` map-style cursor with merge-delta semantics. List the bucket via S3 paginator, for each object compare the new ETag to the cursor's stored ETag; emit Documents only for keys whose ETag changed.

- [ ] **Step 1-2: Test/fail** mirroring `python/tests/chunkshop/test_s3_incremental.py` (which has the FakeS3 monkey-patched paginator pattern — Rust will use `aws-sdk-s3`'s `Behavior::Test` or hand-rolled trait abstraction).

For Rust, the test approach: trait-based seam so an in-memory fake `S3Lister` can drive the source. Look at how the existing Rust s3 source mocks its S3 client (if at all); if not, introduce a small `trait S3Lister { fn list(&self) -> Vec<(String, String, usize)>; fn get(&self, key: &str) -> Bytes; }` and feed a fake impl in tests.

- [ ] **Step 3:** Implement `IncrementalSource` for `S3Source`:
  - `type Cursor = BTreeMap<String, String>;` (key → ETag)
  - `empty_cursor` → empty map
  - `iter_changes_since(cursor)`: list bucket; for each object emit only when `cursor.get(key) != Some(&etag)`
  - `cursor_from(doc)`: returns `{doc.id: doc.metadata["etag"]}` — single-key delta, consumers merge per the contract
- [ ] **Step 4:** Run → PASS (4-test parity with Python's `test_s3_incremental`).
- [ ] **Step 5: Commit** `feat(rust/sources): s3 ETag IncrementalSource (RM-B Task 3)`.

---

## Task 4: http source — depth-crawl + ETag/Last-Modified cursor + robots.txt + politeness

**Files:**
- Modify: `rust/chunkshop/src/sources/http.rs`
- Modify: `rust/chunkshop/src/config.rs` (extend `HttpSource` config struct)
- Test: `rust/chunkshop/tests/http_crawl.rs` (new — mirrors Python's `test_http_crawl.py`)

The Python impl (commit `fcbad65`) adds `crawl_depth`, `allow_external`, `request_delay_seconds`, `respect_robots`, `max_pages`, `user_agent` config fields; implements BFS crawl with cycle-detection; conditional GETs via `If-None-Match` + `If-Modified-Since`; cursor `{url: {etag, last_modified}}`.

- [ ] **Step 1: Write tests** mirroring `python/tests/chunkshop/test_http_crawl.py` 17 cases. Use a Rust HTTP mock — `wiremock` crate is idiomatic. Tests run against a local wiremock server, no real network.

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement.** Use `reqwest` (already a likely Cargo dep for http?) with blocking client OR async + tokio. Match the Python user-agent default `chunkshop/0.6 (+https://github.com/yonk-labs/chunkshop)`.
  - Robots.txt: `robotparser`-equivalent in Rust is `robotxt` crate or hand-roll a minimal parser.
  - HTML→text via `scraper` crate or `html2text`.

- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(rust/sources): http depth-crawl + ETag-cursor incremental (RM-B Task 4)`.

---

## Task 5: RawStore primitive + local + s3 backends

**Files:**
- Create: `rust/chunkshop/src/raw_store/mod.rs`
- Create: `rust/chunkshop/src/raw_store/local.rs`
- Create: `rust/chunkshop/src/raw_store/s3.rs`
- Modify: `rust/chunkshop/src/config.rs` (add RawStoreConfig discriminated union)
- Test: `rust/chunkshop/tests/raw_store.rs` (new)

Python (SP-1 §4.4 / commits `88260a7` + `d4507b2` + `6df0d20` + `dbf5820`):
- `RawStore` Protocol: `put(doc_id, data, content_type, meta) -> ref`, `get(ref) -> bytes`, `exists(doc_id, fingerprint) -> bool`, `delete(doc_id)`
- LocalRawStore: zero-dep filesystem backend, layout `<root>/<sha256(doc_id)>/{blob,meta.json}` (path-traversal-safe via SHA-256)
- S3RawStore: reuses `[s3]` boto3 (Rust: `aws-sdk-s3`)
- `RawStoreConfig` discriminated union + `load_raw_store` factory

- [ ] **Step 1-2: Tests** mirror `python/tests/chunkshop/test_raw_store_{protocol,local,factory,s3}.py`. Include the path-traversal test: `put("../../etc/passwd", b"...")` doesn't escape root.
- [ ] **Step 3-4:** Implement; pass.
- [ ] **Step 5: Commit** `feat(rust/raw_store): RawStore trait + local + s3 backends (RM-B Task 5)`.

---

## Task 6: Sample YAML round-trip + cross-language parity test

**Files:**
- Test: `rust/chunkshop/tests/sp1_parity_python.rs` (new)
- Sample YAML: reuse `docs/samples/sample-incremental-pg.yaml` if it exists, else add a `docs/samples/sample-incremental-http.yaml` covering the new HTTP fields.

- [ ] **Step 1:** Load the same YAML in both Python and Rust; assert both produce the same chunks (count, doc_ids, content) for a fixed corpus. This is the chunkshop "cross-language byte-identical" promise from CLAUDE.md applied to the new surfaces.
- [ ] **Step 2-3:** Run / pass.
- [ ] **Step 4: Commit** `test(rust): SP-1 parity smoke — Rust ingest produces same chunks as Python (RM-B Task 6)`.

---

## Task 7: Update Rust README + the at-a-glance parity table

**Files:**
- Modify: `rust/README.md`
- Modify: `python/README.md` (if there's a parity-status table there)
- Modify: `docs/RELEASING.md` (note RM-B in the release process)

- [ ] **Step 1:** Document the new Rust surfaces. Bring the Python ↔ Rust at-a-glance table up to date.
- [ ] **Step 2: Commit** `docs(rust): document SP-1 sync primitives + RawStore parity (RM-B Task 7)`.

---

## Task 8: Gate + merge

- [ ] **Step 1:** `cargo test --workspace` → ALL pass.
- [ ] **Step 2:** `cargo clippy --workspace -- -D warnings` → clean (or document any allow-listed warnings).
- [ ] **Step 3:** `cargo fmt --check` → clean.
- [ ] **Step 4:** Full Python suite still passes (no regression — should be untouched by Rust work but verify).
- [ ] **Step 5: Commit + tag** the parity milestone:
  ```
  git tag rm-b-rust-sp1-parity
  ```
- [ ] **Step 6:** Run the `finishing-a-development-branch` skill to merge `feat/rm-b-rust-parity` back to `main` and decide whether to push to origin.

---

## Self-review checklist

**Spec coverage** — every primitive the Python session added that's NOT spec-D6-Python-only has a Rust counterpart. The Python-only deferrals (codeparse / symbol_aware / code_relationships / connectors / OAuth / file parsers) are explicitly OUT of scope and won't be confused for Rust work.

**Cross-language compatibility** — vectors and target-table schema stay byte-identical so users mixing Python and Rust ingest into the same pgvector table still get coherent search.

**Placeholder scan** — every task specifies exact files, signatures, and tests. No `# TODO` in committed code.

**Type consistency** — consume RM-A's Document type if it's been defined; don't fork the type definition. If RM-A added `Document { id, content, title, metadata }` without `fingerprint`, Task 1 extends it (additive — old construction sites keep working with `..Default::default()`).

---

## Risks

- **`Document` struct mutation** — adding `fingerprint: Option<String>` to a public struct is a non-breaking minor-version bump in Rust as long as we use `#[non_exhaustive]` or all existing struct-literal constructions use `..Default::default()` already. Audit before Task 1 lands.
- **HTTP client choice** — if Rust crate doesn't already have `reqwest`, this plan pulls it in. ~MB of compile time. Acceptable for v1 but worth flagging.
- **aws-sdk-s3 weight** — already in deps (Rust s3 source exists). If gated behind a feature flag, Task 3 + 5 might re-gate.
- **Cross-language parity test** — Rust calling Python (or vice versa) is awkward in CI. Easier: both produce JSON output for the same YAML, diff JSON. Implement Task 6 that way.

---

## Out of scope (do not build in RM-B)

- chunkshop-connectors plugin (all 25 connector names — Python-only per spec D6)
- `chunkshop.codeparse` (tree-sitter Python bindings — Python-only)
- `code_aware` chunker (stdlib `ast` — Python-only)
- `symbol_aware` chunker (depends on codeparse — Python-only)
- `code_relationships` extractor (depends on codeparse — Python-only)
- `code_summary` extractor (could be Rust but lower priority — defer)
- `comment_extracts` source (could be Rust but lower priority — defer)
- OAuth providers (Python-only by spec §4.3)
- File parsers (PDF/DOCX/PPTX/XLSX/HTML — Python-only via opt-in extras)
- CLI `search --by-symbol` / `impact-of` (Python CLI is the canonical surface)
