# Rust Media Parity (Plan D) — Parser Layer, Provenance Spine, Rung 0/1, SC-12 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port chunkshop's Python media-ingest surface (SP-3 parser layer + `Deriver`/provenance/cache spine + rung 0 image metadata + rung 1 CLIP zero-shot tagging) to Rust, so a mixed-format corpus (`docs/**/*` matching `.pdf`/`.docx`/`.xlsx`/`.html`/`.png`/…) produces the same schema, metadata keys, and provenance shape on both implementations, and CLIP-family vectors agree within a stated float tolerance. This closes the `⛔ RELEASE BLOCKER` in `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md` — no new chunkshop version ships until this plan's tasks are done (`DECISIONS.md`, 2026-09-01).

**Architecture:** Mirror the Python shape file-for-file: a `Deriver` trait (`derive(&Path) -> Result<ParseResult>`) generalizes today's bare `std::fs::read_to_string`; an extension → deriver registry replaces the missing dispatch in `sources/files.rs`; every deriver's output is wrapped in a provenance-keyed `DerivationCache` adapted from the sha256-content-addressed pattern `raw_store/local.rs` already has (fixing its non-atomic write along the way). Two image rungs (`image_meta`: `image` crate, keyless; `clip_tags`: `fastembed-rs`'s `ImageEmbedding`+`TextEmbedding`) land as ordinary text in the ordinary sink — no new pipeline stage, no schema change downstream of `Document`.

**Tech Stack:** Rust 1.8x (workspace toolchain), `fastembed` 5.13.1+ (`image-models` feature already enabled), `pdf-extract` 0.12, `calamine` 0.36.1, `docx-rust` 0.1.11 (crates.io name — **not** bokuweb's writer-only `docx-rs`), `html2text` 0.17.1, `image` 0.25.10, `sha2`/`serde_json` (already deps), `tempfile` 3 (promoted from dev- to real dependency — see Task 7). PPTX: **deferred**, see Task 2 §PPTX.

**Spec:** `spec/chunkshop-media-ingest/SPEC.md` — SC-12 (Rust parity, two levels), SC-8/SC-9/SC-10 (provenance), F1/F2/F11/F17. Scoping: `.superpowers/plan-d-scoping.md`. Forward roadmap + post-scoping findings (read before starting — this plan is built on them): `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md` §"Plan D — Rust media parity".

**Milestones:**
- **M1** (Tasks 1–9): parser layer (the missing SP-3 foundation) + spine (provenance, cache, `on_empty`, config fields) + rung 0 (image metadata). Closes the "silent UTF-8 misread of binary files" defect that blocks any mixed-format corpus on Rust today.
- **M2** (Tasks 10–14): rung 1 (CLIP zero-shot tagging) + SC-12 parity tests (bit-level embedding, contract-level derivation).

---

## Global Constraints

- **Verify before writing.** Every crate API, config field, and existing-test name cited in this plan was checked against `docs.rs`, `~/.cargo/registry/src/.../fastembed-5.13.3`, or this worktree's own source at planning time (2026-09-01, HEAD `8b0c5a7`). Where a detail could not be verified, the step says `⚠️ UNVERIFIED:` and names what to check before trusting it — do not silently fill the gap from memory.
- **`FilesSourceConfig` keeps `#[serde(deny_unknown_fields)]`.** Adding `on_empty`/`derive`/`cache` as real fields means 3 of the 4 existing rejection tests in `src/config.rs` (`rejects_files_source_derive_field`, `rejects_files_source_on_empty_field`, `rejects_files_source_cache_field`) **flip from "must error" to "must load correctly"** — this is intentional and each flip is a named step below, not an accidental regression. `rejects_files_source_incremental_field` is **unchanged** — `files`-source incremental sync is explicitly out of scope for this plan (separate roadmap item).
- **Existing text-only YAMLs produce unchanged output.** A `files` source with no `derive:` block must still read `.txt`/`.md`/etc. exactly as `std::fs::read_to_string` does today — no behavior change, no new required Cargo feature pulled into a build that doesn't ask for one.
- **Nothing derived is written without provenance:** `derived.rung`, `derived.model`, `derived.model_version`, `derived.engine`, `derived.params_hash`, `derived.ts`. The `engine` field is a **new addition on both Python and Rust** (Task 6) — the currently-shipped `stamp_provenance` has no such field, which is a real gap against SPEC.md's own SC-12 language ("the engine is recorded in provenance"). Fixing it now, before two divergent implementations both ship without it, is cheaper than fixing it later.
- **Provenance and the cache key must agree**, exactly as Python does: `derivation_hash()` and `stamp_provenance()` are called with the identical `(rung, model_id, model_version, params)` tuple from the same call site — proven by a same-input equality test, not two separate assertions (Task 6).
- **`model_version` tracks the model library version.** For `clip_tags`, this is the resolved `fastembed` crate version. Rust has no `importlib.metadata`-equivalent runtime introspection of a dependency's version without a build script; this plan uses a hardcoded constant plus a test that fails if it drifts from the `Cargo.toml` pin (Task 11) rather than silently risking stale-cache staleness.
- **New parser/media crates are Cargo-feature-gated, not always-on.** Mirrors the existing `embedder-core`/`embedder-hub`/`code-aware-*` convention: `dep:pdf-extract`, `dep:calamine`, `dep:docx-rust`, `dep:html2text`, `dep:image` go behind a new `media` feature (Task 1); `clip_tags` additionally requires `embedder-hub` (already exists).
- **Identifier safety, chunker-wins metadata merge, sink schema — unchanged.** Nothing in this plan touches `PromoteColumn`, `mode:` semantics, or the sink layer; `content_origin`/`derived_rung` ride the **already-existing** `promote_metadata` mechanism (verified: `PromoteColumn`/`promote_metadata` are in `config.rs` today, generic, not media-specific — no new sink code needed).
- Rust target: workspace toolchain. Test command: `cd rust/chunkshop && cargo test --features media,embedder-hub` (feature name from Task 1). Baseline: **427 tests passing**, HEAD `8b0c5a7`. A cold `cargo build --tests --features embedder-hub` takes ~54s with a warm cache; add ~10-15s for the new `media` feature's crates on the first build after Task 1.

---

## File Structure

**Create (Rust, `rust/chunkshop/`):**
- `src/sources/parsers/mod.rs` — `ParseResult`, `Deriver` trait, `call_deriver`, `DeriverError`
- `src/sources/parsers/registry.rs` — extension → deriver dispatch (`get_parser` equivalent), `DEFAULT_PARSERS`-equivalent table
- `src/sources/parsers/text.rs`, `html.rs`, `pdf.rs`, `xlsx.rs`, `docx.rs` — one deriver per format
- `src/sources/derivers/mod.rs` — deriver registry + `load_deriver()`
- `src/sources/derivers/provenance.rs` — `derivation_hash`, `stamp_provenance`
- `src/sources/derivers/cache.rs` — `DerivationCache`, `cached_derive`
- `src/sources/derivers/image_meta.rs` — rung 0
- `src/sources/derivers/clip_tags.rs` — rung 1
- `tests/media_parser_formats.rs` — pdf/docx/xlsx/html round-trip fixtures
- `tests/media_derivation_parity.rs` — SC-12 contract-level: schema/metadata-key + provenance-engine equality vs a Python-generated golden fixture
- `tests/clip_embedding_parity.rs` — SC-12 bit-level: CLIP text (tight) + CLIP vision (amended) tolerance
- `tests/parity-fixtures/clip_text_reference.bin`, `tests/parity-fixtures/clip_vision_reference.bin` — full-vector binary fixtures (same layout as `embedding_reference_bge_base_int8.bin`)
- `tests/parity-fixtures/media_derivation_reference.json` — golden schema fixture

**Modify:**
- `rust/chunkshop/Cargo.toml` — new optional deps + `media` feature; promote `tempfile` to `[dependencies]`
- `rust/chunkshop/src/config.rs` — `FilesSourceConfig` gains `on_empty`/`derive`/`cache`; new `ImageMetaDeriverConfig`/`ClipTagsDeriverConfig`/`DeriverConfig` union/`DerivationCacheSettings`; 3 rejection tests flip to acceptance tests
- `rust/chunkshop/src/sources/files.rs` — extension dispatch, `on_empty` handling, deriver wiring, `_derived_exts`-equivalent cache-eligibility gate
- `rust/chunkshop/examples/parity_probe.rs` — already has CLIP text/vision/bge probes (committed at HEAD `8b0c5a7`); extended in Task 13 to dump full vectors for fixture regeneration
- `python/src/chunkshop/sources/derivers/provenance.py` — add `engine` parameter/field (Task 6)
- `python/src/chunkshop/sources/derivers/image_meta.py`, `clip_tags.py` — pass `engine="python"` at their `stamp_provenance()` call sites (Task 6)
- `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md` — record the CLIP vision-tower SC-12 amendment decision (Task 10) and check off completed Plan D boxes as tasks land
- `spec/chunkshop-media-ingest/SPEC.md` — amend SC-12's bit-level clause for the vision tower (Task 10)

---

### Task 1: `ParseResult`, the `Deriver` trait, and the `media` Cargo feature

Foundation. No existing Rust behavior changes — `full`/`default` builds don't pull the new crates unless `media` is requested.

**Files:**
- Modify: `rust/chunkshop/Cargo.toml`
- Create: `rust/chunkshop/src/sources/parsers/mod.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs` (declare the new `parsers` submodule)
- Test: inline `#[cfg(test)] mod tests` in `parsers/mod.rs`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `ParseResult { text: String, metadata: serde_json::Map<String, Value> }`; `trait Deriver: Send + Sync { fn derive(&self, path: &Path) -> Result<ParseResult>; }`; `DeriverError` (thiserror, mirrors `StaleCursorError`'s convert-into-`anyhow::Error` idiom in `sources/base.rs`).

- [ ] **Step 1: Add the Cargo feature and optional deps**

In `rust/chunkshop/Cargo.toml`, add to `[dependencies]` (alongside the other optional-dep blocks, e.g. near the `extractor`/`whatlang` line):

```toml
# --- `media` feature: SP-3 parser layer (pdf/docx/xlsx/html) + rung-0 image
# metadata. Mirrors Python's per-format parser modules
# (sources/parsers/{pdf,docx,xlsx,html}.py) and the free image_meta deriver.
# `docx-rust` is the crates.io name for PoiScript's *reader* — NOT bokuweb's
# `docx-rs`, which is writer-only and would silently fail to extract text.
pdf-extract = { version = "0.12", optional = true }
calamine = { version = "0.36", optional = true }
docx-rust = { version = "0.1", optional = true }
html2text = { version = "0.17", optional = true }
# `image-models` already pulls `image` transitively via fastembed, but only
# fastembed's own code can `use` it that way — a direct dependency is needed
# for `image_meta.rs`'s own `image::open`/`ImageFormat`/`ColorType` calls.
image = { version = "0.25", optional = true }
```

Add to `[features]`, near the `code-aware`/`lede` blocks:

```toml
# SP-3 parser layer + rung 0 (image metadata, keyless/deterministic).
# `clip_tags` (rung 1) additionally requires `embedder-hub` — gated in code
# with #[cfg(all(feature = "media", feature = "embedder-hub"))], not a
# separate Cargo feature, because a YAML that asks for `rung: clip_tags`
# without embedder-hub built in should fail at config-load with a clear
# message (F14/NN-6 convention), not a missing-symbol compile error for
# users who only wanted image_meta.
media = ["dep:pdf-extract", "dep:calamine", "dep:docx-rust", "dep:html2text", "dep:image"]
```

- [ ] **Step 2: Write the failing test**

Create `rust/chunkshop/src/sources/parsers/mod.rs`:

```rust
//! Per-extension file derivers. Mirrors `python/src/chunkshop/sources/parsers/`.
//! `Deriver::derive(path) -> ParseResult` extracts a file's content plus any
//! metadata the extraction produced. `FilesSource` wraps the result into a
//! `Document`. Unlike Python's `FileParser`/`Deriver` split (kept separate
//! there for backward compatibility with pre-1.0 third-party parsers), Rust
//! has no such compatibility surface to preserve — one trait, from day one.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use thiserror::Error;

pub mod registry;
pub mod text;
#[cfg(feature = "media")]
pub mod docx;
#[cfg(feature = "media")]
pub mod html;
#[cfg(feature = "media")]
pub mod pdf;
#[cfg(feature = "media")]
pub mod xlsx;

/// What a deriver returns: extracted text plus whatever metadata the
/// extraction produced (dimensions, sheet names, model provenance, ...).
#[derive(Debug, Clone, PartialEq)]
pub struct ParseResult {
    pub text: String,
    pub metadata: Map<String, Value>,
}

impl ParseResult {
    pub fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            metadata: Map::new(),
        }
    }

    pub fn with_metadata(text: impl Into<String>, metadata: Map<String, Value>) -> Self {
        Self {
            text: text.into(),
            metadata,
        }
    }
}

/// Mirrors Python's `EmptyDerivationError` — a recognized file that produced
/// no text. Raised instead of silently indexing an empty document.
#[derive(Debug, Error)]
#[error("{path} produced no text via {parser}")]
pub struct EmptyDerivationError {
    pub path: PathBuf,
    pub parser: String,
}

/// Mirrors Python's `RuntimeError` "install the extra" convention (NN-6): a
/// known format was matched but the binary wasn't built with the feature
/// that provides it.
#[derive(Debug, Error)]
#[error("{format} parsing requires the `{feature}` Cargo feature; rebuild with --features {feature}")]
pub struct MissingFeatureError {
    pub format: &'static str,
    pub feature: &'static str,
}

/// A file recognized but not yet parseable — distinct from `MissingFeatureError`
/// (which names a feature flag that WOULD fix it). Used for PPTX (Task 2):
/// there is no Rust crate committed to yet, so this is not a build-config fix.
#[derive(Debug, Error)]
#[error("{format} parsing is not yet implemented in the Rust port (see docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md); falling back to text would silently corrupt binary content, so this file was refused instead")]
pub struct UnsupportedFormatError {
    pub format: &'static str,
}

pub trait Deriver: Send + Sync {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_result_new_has_empty_metadata() {
        let r = ParseResult::new("hello");
        assert_eq!(r.text, "hello");
        assert!(r.metadata.is_empty());
    }

    #[test]
    fn parse_result_with_metadata_holds_both() {
        let mut md = Map::new();
        md.insert("width".to_string(), Value::from(8));
        let r = ParseResult::with_metadata("hi", md.clone());
        assert_eq!(r.text, "hi");
        assert_eq!(r.metadata, md);
    }

    #[test]
    fn empty_derivation_error_names_path_and_parser() {
        let e = EmptyDerivationError {
            path: PathBuf::from("scanned.pdf"),
            parser: "PdfParser".to_string(),
        };
        let msg = e.to_string();
        assert!(msg.contains("scanned.pdf"), "{msg}");
        assert!(msg.contains("PdfParser"), "{msg}");
    }
}
```

- [ ] **Step 3: Declare the submodule and run**

In `rust/chunkshop/src/sources/mod.rs`, add near the top (alongside `pub mod files;` — check the existing module list before inserting to avoid a duplicate):

```rust
pub mod parsers;
```

Run: `cd rust/chunkshop && cargo test --lib sources::parsers -q`
Expected: PASS (3 tests). This compiles WITHOUT `--features media` because `ParseResult`/`Deriver`/error types have no feature gate — only the format-specific submodules (`docx`/`html`/`pdf`/`xlsx`) do.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/sources/parsers/mod.rs rust/chunkshop/src/sources/mod.rs
git commit -m "feat(rust,parsers): ParseResult + Deriver trait + media Cargo feature scaffold"
```

---

### Task 2: Parser registry + text/HTML derivers wired into `FilesSource`; PPTX explicitly refused

Wires the trait into the actual read path, replacing the bare `std::fs::read_to_string` — this is the fix for the scoping's headline defect (`files.rs:34-35` silently UTF-8-decoding binary files).

**Files:**
- Create: `rust/chunkshop/src/sources/parsers/registry.rs`, `text.rs`, `html.rs`
- Modify: `rust/chunkshop/src/sources/files.rs`
- Test: `rust/chunkshop/tests/media_parser_formats.rs` (new file; also holds Tasks 3-5's tests)

**Interfaces:**
- Consumes: `Deriver`, `ParseResult` (Task 1).
- Produces: `fn get_deriver(ext: &str) -> Box<dyn Deriver>` (falls back to `TextDeriver` for genuinely unknown extensions, mirroring Python's `get_parser` fallback — **except** for extensions Rust recognizes-but-doesn't-implement, which get a named refusal, not a silent text fallback).

- [ ] **Step 1: `TextDeriver` — always available, no feature gate**

Create `rust/chunkshop/src/sources/parsers/text.rs`:

```rust
//! Plain-text passthrough. Always available (no `media` feature needed) —
//! this is the format Rust already reads correctly today, and every
//! existing text-only YAML must keep working unchanged.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct TextDeriver;

impl Deriver for TextDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
```

`⚠️ UNVERIFIED — deliberate, not an oversight:` Python's `TextParser.parse` uses `errors="replace"` (lossy UTF-8 decode); Rust's `std::fs::read_to_string` errors on invalid UTF-8 (strict). This is a **pre-existing** divergence, not introduced here — today's Rust `read_to_string` call has the same strictness. Fixing it is a separate, scoped decision (it changes behavior for every existing text YAML with any malformed-UTF-8 file, which is exactly what the "byte-identical output" constraint says not to touch casually). Noted in this plan's executor notes; not fixed here.

- [ ] **Step 2: `HtmlDeriver` (media feature)**

Create `rust/chunkshop/src/sources/parsers/html.rs`:

```rust
//! HTML -> text via html2text (html5ever-backed). Mirrors
//! `python/src/chunkshop/sources/parsers/html.py`'s BeautifulSoup
//! `get_text(separator="\n", strip=True)` call — same rough shape (block-
//! level text extraction), not guaranteed byte-identical wrapping since the
//! two libraries make different line-wrap decisions.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct HtmlDeriver;

impl Deriver for HtmlDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let bytes = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        // Wide width avoids html2text's word-wrap inserting line breaks that
        // Python's BeautifulSoup extraction never would.
        let text = html2text::from_read(bytes.as_slice(), 100_000)
            .map_err(|e| anyhow::anyhow!("parsing HTML {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
```

- [ ] **Step 3: The registry, with PPTX explicitly refused (not silently text-decoded)**

Create `rust/chunkshop/src/sources/parsers/registry.rs`:

```rust
//! Extension -> Deriver dispatch. Mirrors `get_parser`/`DEFAULT_PARSERS` in
//! `python/src/chunkshop/sources/parsers/__init__.py`.
//!
//! PPTX is a DELIBERATE gap, not an oversight: the scoping
//! (`.superpowers/plan-d-scoping.md` §2) found no Rust crate with the
//! track record `pdf-extract`/`calamine`/`docx-rust`/`html2text` have —
//! `pptx-to-md`, `undoc`, and `office_oxide`'s PPTX path are all young or
//! low-traction, and a bad pick risks 1-2 days of mid-implementation
//! rework (scoping §5, risk 1). Rather than pick blind, `.pptx` gets a
//! named refusal here. This is a decision to STOP the fallback chain, not
//! to fall through to `TextDeriver` — the whole point of this parser layer
//! is that a binary file must never be silently read as UTF-8 text.
//! Follow-up: a short bakeoff task against real PPTX fixtures, tracked in
//! `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`.

use std::path::Path;

use super::text::TextDeriver;
use super::{Deriver, UnsupportedFormatError};

/// Extensions Python parses today with a real Rust crate lined up
/// (Tasks 3-5). Checked before the "recognized but refused" list.
#[cfg(feature = "media")]
fn media_deriver(ext: &str) -> Option<Box<dyn Deriver>> {
    match ext {
        "pdf" => Some(Box::new(super::pdf::PdfDeriver)),
        "xlsx" => Some(Box::new(super::xlsx::XlsxDeriver)),
        "docx" => Some(Box::new(super::docx::DocxDeriver)),
        "html" | "htm" => Some(Box::new(super::html::HtmlDeriver)),
        _ => None,
    }
}

#[cfg(not(feature = "media"))]
fn media_deriver(_ext: &str) -> Option<Box<dyn Deriver>> {
    None
}

/// Extensions Python has a real parser for that Rust explicitly refuses
/// rather than silently text-decoding. Distinct from "genuinely unknown"
/// (which correctly falls back to text, matching Python's behavior for
/// e.g. a custom `.myformat` file someone injects a custom parser for).
struct RefusingDeriver {
    format: &'static str,
}

impl Deriver for RefusingDeriver {
    fn derive(&self, _path: &Path) -> anyhow::Result<ParseResultUnreachable> {
        unreachable!()
    }
}
// (placeholder type only to keep this snippet syntactically self-contained;
// see Step 3b below for the real, compiling version)

pub fn get_deriver(ext: &str) -> Box<dyn Deriver> {
    let ext = ext.to_lowercase();
    if let Some(d) = media_deriver(&ext) {
        return d;
    }
    if ext == "pptx" {
        return Box::new(PptxRefusalDeriver);
    }
    Box::new(TextDeriver)
}

pub struct PptxRefusalDeriver;

impl Deriver for PptxRefusalDeriver {
    fn derive(&self, _path: &Path) -> anyhow::Result<super::ParseResult> {
        Err(UnsupportedFormatError { format: "pptx" }.into())
    }
}
```

The `RefusingDeriver`/`ParseResultUnreachable` block above is a **placeholder to delete** — it was scaffolding while drafting; the real file only needs `get_deriver` + `PptxRefusalDeriver`. Delete the `RefusingDeriver` struct and its impl before committing; it does not compile (`ParseResultUnreachable` is not a real type) and step 4's test run will catch this if left in.

- [ ] **Step 4: Wire into `FilesSource`, replacing the bare read**

In `rust/chunkshop/src/sources/files.rs`, replace the body of `iter_documents`'s per-path loop (currently `std::fs::read_to_string(&p)...`):

```rust
        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let ext = p
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_lowercase();
            let deriver = crate::sources::parsers::registry::get_deriver(&ext);
            let result = deriver
                .derive(&p)
                .with_context(|| format!("deriving {}", p.display()))?;
            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            let mut metadata = result.metadata;
            metadata.insert(
                "source_path".to_string(),
                serde_json::Value::String(p.display().to_string()),
            );
            out.push(Document {
                id: doc_id,
                content: result.text,
                title,
                metadata: serde_json::Value::Object(metadata),
                fingerprint: None,
            });
        }
        Ok(out)
```

Add `use crate::sources::parsers::ParseResult;` is not needed (accessed via `result.text`/`result.metadata` directly); add nothing else — `get_deriver` is reached via its full path above.

- [ ] **Step 5: Byte-identical-output guard test (SC-13 equivalent)**

Create `rust/chunkshop/tests/media_parser_formats.rs`:

```rust
//! Format-by-format round-trip tests for the SP-3 parser layer, plus the
//! guard that adding this layer does not change output for existing
//! text-only YAMLs (SC-13's Rust equivalent).

use std::io::Write;

use chunkshop::config::FilesSourceConfig;
use chunkshop::sources::files::FilesSource;

fn write_temp(dir: &std::path::Path, name: &str, contents: &[u8]) -> std::path::PathBuf {
    let p = dir.join(name);
    let mut f = std::fs::File::create(&p).unwrap();
    f.write_all(contents).unwrap();
    p
}

#[test]
fn text_file_output_is_unchanged_by_the_parser_layer() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.md", b"# Title\n\nBody text.\n");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.md", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let docs = src.iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "# Title\n\nBody text.\n");
    assert_eq!(docs[0].id, "a");
}

#[cfg(feature = "media")]
#[test]
fn pptx_is_refused_not_silently_decoded_as_text() {
    let dir = tempfile::tempdir().unwrap();
    // Not a real PPTX (no crate to generate one with in this test) — a
    // non-UTF-8 byte sequence is the adversarial case that matters: this
    // must NOT come back as a "successful" garbled-text Document.
    write_temp(dir.path(), "deck.pptx", &[0xFF, 0xFE, 0x00, 0x01, 0x02]);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.pptx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("not yet implemented"),
        "expected a named PPTX refusal, got: {msg}"
    );
}
```

`⚠️ UNVERIFIED:` confirm `FilesSourceConfig`'s exact field set at implementation time — Task 8 adds `on_empty`/`derive`/`cache` to it; if this test is written after Task 8 lands, the struct literal needs those fields too (or use `..Default::default()` if the config type gains a `Default` impl — check whether one exists before assuming).

Run: `cd rust/chunkshop && cargo test --features media --test media_parser_formats -q`
Expected: PASS (2 tests). Without `--features media`, only the first test compiles (the second is `#[cfg(feature = "media")]`-gated) — run `cargo test --test media_parser_formats -q` too and confirm 1 passed.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/sources/parsers/registry.rs rust/chunkshop/src/sources/parsers/text.rs \
        rust/chunkshop/src/sources/parsers/html.rs rust/chunkshop/src/sources/files.rs \
        rust/chunkshop/tests/media_parser_formats.rs
git commit -m "feat(rust,parsers): extension registry wired into FilesSource; PPTX refused, not silently mis-decoded"
```

---

### Task 3: PDF deriver (`pdf-extract`)

**Files:**
- Create: `rust/chunkshop/src/sources/parsers/pdf.rs`
- Test: append to `rust/chunkshop/tests/media_parser_formats.rs`

**Interfaces:**
- Consumes: `Deriver`, `ParseResult` (Task 1).
- Produces: `PdfDeriver` implementing `Deriver`.

- [ ] **Step 1: Write the deriver**

Create `rust/chunkshop/src/sources/parsers/pdf.rs`:

```rust
//! PDF -> text via `pdf-extract` 0.12. Mirrors
//! `python/src/chunkshop/sources/parsers/pdf.py`'s pypdf usage: page-by-page
//! `extract_text()`, joined with newlines. `pdf-extract::extract_text(path)`
//! returns the whole-document text directly (verified via docs.rs 0.12.0);
//! Python joins per-page strings with "\n" — the exact page-boundary
//! whitespace may differ, but `FilesSource`'s `on_empty` check only cares
//! whether the result is non-blank, which both sides agree on for any PDF
//! with extractable text.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct PdfDeriver;

impl Deriver for PdfDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let text = pdf_extract::extract_text(path)
            .map_err(|e| anyhow::anyhow!("failed to parse PDF {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
```

- [ ] **Step 2: Wire into the registry**

In `rust/chunkshop/src/sources/parsers/registry.rs`'s `media_deriver`, the `"pdf" =>` arm was already added in Task 2 Step 3 (written ahead since the registry needed to name all format arms up front) — confirm it compiles now that `pdf.rs` exists. If Task 2 was implemented literally as written, this step is a no-op; if the executor deferred the `pdf` arm, add it now.

- [ ] **Step 3: Round-trip test**

Append to `rust/chunkshop/tests/media_parser_formats.rs` (needs a real PDF fixture — generate one at test time rather than committing a binary, since `pdf-extract` has no writer half; use a minimal hand-written PDF byte string, which is well-defined and small):

```rust
#[cfg(feature = "media")]
#[test]
fn pdf_with_extractable_text_round_trips() {
    // Minimal valid single-page PDF with the literal text "Hello PDF".
    // Hand-built PDF 1.4 byte stream — small enough to inline, avoids
    // needing a PDF-writing crate as a dev-dependency just for one fixture.
    const MINI_PDF: &[u8] = include_bytes!("fixtures/hello.pdf");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("hello.pdf");
    std::fs::write(&p, MINI_PDF).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.pdf", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("Hello PDF"),
        "got: {:?}",
        docs[0].content
    );
}
```

`⚠️ UNVERIFIED:` `tests/fixtures/hello.pdf` does not exist yet and hand-authoring a byte-correct minimal PDF (xref table, correct byte offsets) inline is error-prone to get right without running it. **Do this instead at implementation time:** generate the fixture once with Python (`pypdf.PdfWriter` + a text-drawing library, or reuse the existing `writer.add_blank_page` pattern from `python/tests/chunkshop/test_empty_derivation.py` — that one is blank on purpose, so add a text-drawing step, e.g. via `reportlab` if available, or the simplest fpath: use `pdf-extract`'s own test fixtures if the crate ships any under its repo's `tests/` — check before writing a new one), commit the resulting small PDF under `rust/chunkshop/tests/fixtures/hello.pdf`, and confirm `pdf_extract::extract_text` recovers the known string before trusting this test. Do not write speculative PDF bytes into the repo unverified.

Run: `cd rust/chunkshop && cargo test --features media --test media_parser_formats pdf -q`

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sources/parsers/pdf.rs rust/chunkshop/tests/media_parser_formats.rs \
        rust/chunkshop/tests/fixtures/hello.pdf
git commit -m "feat(rust,parsers): PDF deriver via pdf-extract"
```

---

### Task 4: XLSX deriver (`calamine`)

**Files:**
- Create: `rust/chunkshop/src/sources/parsers/xlsx.rs`
- Test: append to `rust/chunkshop/tests/media_parser_formats.rs`

**Interfaces:**
- Consumes: `Deriver`, `ParseResult` (Task 1).
- Produces: `XlsxDeriver`, mirroring `python/src/chunkshop/sources/parsers/xlsx.py`'s "every worksheet, every row, tab-joined cells, newline-joined rows" shape.

- [ ] **Step 1: Write the deriver**

Create `rust/chunkshop/src/sources/parsers/xlsx.rs`:

```rust
//! XLSX -> text via `calamine` 0.36. Mirrors
//! `python/src/chunkshop/sources/parsers/xlsx.py`: every worksheet, every
//! row via `iter_rows(values_only=True)`, cells tab-joined, rows newline-
//! joined, `None`/empty cells become `""`. `calamine::Data` is the cell-value
//! enum (verified docs.rs 0.36.1) — `Data::Empty` maps to Python's `None`.

use std::path::Path;

use calamine::{open_workbook, Data, Reader, Xlsx};

use super::{Deriver, ParseResult};

pub struct XlsxDeriver;

impl Deriver for XlsxDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let mut wb: Xlsx<_> = open_workbook(path)
            .map_err(|e| anyhow::anyhow!("failed to parse XLSX {}: {e}", path.display()))?;
        let sheet_names = wb.sheet_names().to_vec();
        let mut rows_out: Vec<String> = Vec::new();
        for name in sheet_names {
            let range = wb
                .worksheet_range(&name)
                .map_err(|e| anyhow::anyhow!("reading sheet {name:?} in {}: {e}", path.display()))?;
            for row in range.rows() {
                let cells: Vec<String> = row.iter().map(cell_to_string).collect();
                rows_out.push(cells.join("\t"));
            }
        }
        Ok(ParseResult::new(rows_out.join("\n")))
    }
}

fn cell_to_string(cell: &Data) -> String {
    match cell {
        Data::Empty => String::new(),
        Data::String(s) => s.clone(),
        Data::Float(f) => f.to_string(),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        Data::Error(e) => format!("{e:?}"),
        Data::DateTime(d) => d.to_string(),
        Data::DateTimeIso(s) | Data::DurationIso(s) => s.clone(),
    }
}
```

`⚠️ UNVERIFIED:` the exact `Data` enum variant list (`DateTime`/`DateTimeIso`/`DurationIso` names and whether `DateTime` wraps a type with a `.to_string()` that produces something reasonable) was not confirmed against calamine 0.36.1's actual source — only the core `String`/`Float`/`Int`/`Empty` variants were verified via docs.rs. Check `calamine::Data`'s full variant list against the installed crate (`cargo doc --open -p calamine` or the vendored source under `~/.cargo/registry/src/`) before trusting this match arm to compile; add/remove variants as needed. Python's `openpyxl` cell values are far simpler (str/int/float/None via `str()`), so exact numeric formatting parity (e.g. `1.0` vs `1`) is not guaranteed either way — this is a **contract-level** (schema/non-blank) parity claim, not a value-level one, consistent with SC-12's derivation clause.

- [ ] **Step 2: Round-trip test**

Append to `rust/chunkshop/tests/media_parser_formats.rs`. `calamine` is read-only — generate the fixture with a writer. Check whether a writer crate is already available; if not, generate the fixture once via Python (`openpyxl.Workbook()`, write a cell, save) and commit the small `.xlsx` binary, same approach as the PDF fixture:

```rust
#[cfg(feature = "media")]
#[test]
fn xlsx_with_a_cell_round_trips() {
    const FIXTURE: &[u8] = include_bytes!("fixtures/one_cell.xlsx");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("one_cell.xlsx");
    std::fs::write(&p, FIXTURE).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.xlsx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("hello xlsx"),
        "got: {:?}",
        docs[0].content
    );
}
```

Generate the fixture: `python3 -c "import openpyxl; wb=openpyxl.Workbook(); wb.active['A1']='hello xlsx'; wb.save('rust/chunkshop/tests/fixtures/one_cell.xlsx')"` — run this once, verify the resulting file, then commit it.

Run: `cd rust/chunkshop && cargo test --features media --test media_parser_formats xlsx -q`

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/sources/parsers/xlsx.rs rust/chunkshop/tests/media_parser_formats.rs \
        rust/chunkshop/tests/fixtures/one_cell.xlsx
git commit -m "feat(rust,parsers): XLSX deriver via calamine"
```

---

### Task 5: DOCX deriver (`docx-rust`)

**Files:**
- Create: `rust/chunkshop/src/sources/parsers/docx.rs`
- Test: append to `rust/chunkshop/tests/media_parser_formats.rs`

**Interfaces:**
- Consumes: `Deriver`, `ParseResult` (Task 1).
- Produces: `DocxDeriver`, mirroring `python/src/chunkshop/sources/parsers/docx.py`'s "every paragraph's text, newline-joined" shape.

- [ ] **Step 1: Write the deriver**

`⚠️ UNVERIFIED — the traversal shape below was confirmed via docs.rs (`docx-rust` 0.1.11: `Document` → `Body` (Vec of `BodyContent`) → `BodyContent::Paragraph` → `Paragraph` (Vec of `ParagraphContent`) → `ParagraphContent::Run` → `Run` (Vec of `RunContent`) → `RunContent::Text` → `Text`), but the exact field name holding `Text`'s inner string was NOT confirmed (docs.rs's module page didn't expose it without a deeper fetch). Before trusting the code below, run `cargo doc --open -p docx-rust` (or read the vendored source once it's in `~/.cargo/registry/`) and confirm the field name — it's written as `.text` below as the most likely name but must be checked, not assumed.

Create `rust/chunkshop/src/sources/parsers/docx.rs`:

```rust
//! DOCX -> text via `docx-rust` 0.1.11 (crates.io name for PoiScript's
//! reader — NOT bokuweb's writer-only `docx-rs`, a common crate-name trap
//! per the scoping). Mirrors `python/src/chunkshop/sources/parsers/docx.py`:
//! every paragraph's text, newline-joined.

use std::path::Path;

use docx_rust::document::{BodyContent, ParagraphContent, RunContent};
use docx_rust::DocxFile;

use super::{Deriver, ParseResult};

pub struct DocxDeriver;

impl Deriver for DocxDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let docx_file = DocxFile::from_file(path)
            .map_err(|e| anyhow::anyhow!("failed to open DOCX {}: {e}", path.display()))?;
        let docx = docx_file
            .parse()
            .map_err(|e| anyhow::anyhow!("failed to parse DOCX {}: {e}", path.display()))?;

        let mut paragraphs = Vec::new();
        for content in &docx.document.body.content {
            if let BodyContent::Paragraph(p) = content {
                let mut text = String::new();
                for pc in &p.content {
                    if let ParagraphContent::Run(run) = pc {
                        for rc in &run.content {
                            if let RunContent::Text(t) = rc {
                                // ⚠️ UNVERIFIED field name — confirm `.text`
                                // against the actual crate before trusting.
                                text.push_str(&t.text);
                            }
                        }
                    }
                }
                paragraphs.push(text);
            }
        }
        Ok(ParseResult::new(paragraphs.join("\n")))
    }
}
```

- [ ] **Step 2: Round-trip test**

Append to `rust/chunkshop/tests/media_parser_formats.rs`, fixture generated once via `python-docx` (same pattern as Tasks 3-4):

```rust
#[cfg(feature = "media")]
#[test]
fn docx_with_a_paragraph_round_trips() {
    const FIXTURE: &[u8] = include_bytes!("fixtures/one_paragraph.docx");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("one_paragraph.docx");
    std::fs::write(&p, FIXTURE).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.docx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("hello docx"),
        "got: {:?}",
        docs[0].content
    );
}
```

Generate the fixture: `python3 -c "import docx; d=docx.Document(); d.add_paragraph('hello docx'); d.save('rust/chunkshop/tests/fixtures/one_paragraph.docx')"`.

Run: `cd rust/chunkshop && cargo test --features media --test media_parser_formats docx -q`. If the field-name guess in Step 1 is wrong, this is exactly where it fails loudly (a compile error naming the missing field) — fix the field access, not the test.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/sources/parsers/docx.rs rust/chunkshop/tests/media_parser_formats.rs \
        rust/chunkshop/tests/fixtures/one_paragraph.docx
git commit -m "feat(rust,parsers): DOCX deriver via docx-rust"
```

---

### Task 6: Provenance — `derivation_hash`, `stamp_provenance`, and the new `engine` field (Python + Rust)

Closes a real gap found while researching this plan: SPEC.md's SC-12 says "the engine is recorded in provenance," but the currently-shipped `stamp_provenance()` (both would-be Python and Rust) has no `engine` field — only `model`/`model_version`. Adding it now, before Rust ships any derivers, means both implementations carry it from day one instead of retrofitting later.

**Files:**
- Modify: `python/src/chunkshop/sources/derivers/provenance.py` (small, additive)
- Modify: `python/src/chunkshop/sources/derivers/image_meta.py`, `python/src/chunkshop/sources/derivers/clip_tags.py` (pass `engine="python"`)
- Modify: `python/tests/chunkshop/test_derivation_provenance.py` (extend, don't break)
- Create: `rust/chunkshop/src/sources/derivers/mod.rs`, `rust/chunkshop/src/sources/derivers/provenance.rs`
- Modify: `rust/chunkshop/src/sources/mod.rs` (declare `derivers` submodule)

**Interfaces:**
- Consumes: `ParseResult` (Task 1, Rust side; existing Python `ParseResult`).
- Produces (Rust): `fn derivation_hash(rung: &str, model_id: &str, model_version: &str, engine: &str, params: &Value) -> String`; `fn stamp_provenance(result: ParseResult, rung: &str, model_id: &str, model_version: &str, engine: &str, params: &Value) -> ParseResult`. Produces (Python, additive): same two functions gain an `engine: str` keyword-only parameter.

- [ ] **Step 1: Python side — add `engine`, keep the existing call sites working**

In `python/src/chunkshop/sources/derivers/provenance.py`, extend both functions (the `_params_hash` helper is unchanged):

```python
def derivation_hash(
    rung: str, model_id: str, model_version: str, engine: str, params: dict
) -> str:
    """Identity of *how* a derivation was produced.

    `engine` distinguishes implementations that can diverge even with the
    same model id/version (e.g. Python's fastembed vs Rust's fastembed-rs,
    or CTranslate2 vs whisper-rs for ASR) — SC-12's contract-level parity
    clause requires the engine be attributable, not just the model.
    """
    parts = [CACHE_SCHEMA_VERSION, rung, model_id, model_version, engine, _params_hash(params)]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def stamp_provenance(
    result: ParseResult,
    *,
    rung: str,
    model_id: str,
    model_version: str,
    engine: str,
    params: dict,
) -> ParseResult:
    """Return a new ParseResult carrying full derivation provenance."""
    metadata = {
        **result.metadata,
        "content_origin": "derived",
        "derived_rung": rung,
        "derived": {
            "rung": rung,
            "model": model_id,
            "model_version": model_version,
            "engine": engine,
            "params_hash": _params_hash(params),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
    return ParseResult(text=result.text, metadata=metadata)
```

`engine` is added as a **required keyword-only** parameter, not optional-with-default — every call site must state it explicitly (2 call sites, both fixed in this task), so there is no silent "someone forgot to pass it" gap.

In `python/src/chunkshop/sources/derivers/image_meta.py`'s `derive()`, add `engine="python"` to the `stamp_provenance(...)` call. In `python/src/chunkshop/sources/derivers/clip_tags.py`'s `derive()`, same.

- [ ] **Step 2: Update Python's existing provenance tests, and add the new coverage**

In `python/tests/chunkshop/test_derivation_provenance.py`, every call to `derivation_hash(...)` and `stamp_provenance(...)` needs an `engine` argument added — this WILL break the 5 existing tests until updated; that is expected (they're asserting against a function signature this task changes). Update each call site (e.g. `derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})` becomes `derivation_hash("clip_tags", "clip-ViT-B-32", "1", "python", {"top_k": 3})`), and add:

```python
def test_hash_changes_when_engine_changes():
    base = derivation_hash("clip_tags", "clip-ViT-B-32", "1", "python", {"top_k": 3})
    assert derivation_hash("clip_tags", "clip-ViT-B-32", "1", "rust", {"top_k": 3}) != base


def test_stamp_names_the_engine():
    out = stamp_provenance(
        ParseResult(text="a cat", metadata={}),
        rung="clip_tags", model_id="m", model_version="1", engine="python", params={},
    )
    assert out.metadata["derived"]["engine"] == "python"
```

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derivation_provenance.py tests/chunkshop/test_deriver_image_meta.py -q -m "not slow"`
Expected: PASS. `test_deriver_image_meta.py::test_provenance_is_stamped` asserts `md["derived"]["model"] == "deterministic"` — unaffected by the new field, should still pass unmodified.

- [ ] **Step 3: Rust side — mirror the (now-final) shape**

Create `rust/chunkshop/src/sources/derivers/mod.rs`:

```rust
//! Media derivers: turn non-text bytes into text plus metadata. Mirrors
//! `python/src/chunkshop/sources/derivers/`. Every deriver's output is
//! stamped with provenance and memoised on (content_hash, derivation_hash)
//! so nothing is derived twice and nothing is written without attribution.

pub mod cache;
pub mod clip_tags;
pub mod image_meta;
pub mod provenance;
```

Create `rust/chunkshop/src/sources/derivers/provenance.rs`:

```rust
//! Derivation provenance: the hash that keys the cache, and the metadata
//! stamp that records how a derivation was produced. Mirrors
//! `python/src/chunkshop/sources/derivers/provenance.py` byte-for-byte in
//! shape (same 6-part hash input: schema version, rung, model, version,
//! engine, params-hash), including the new `engine` field (this task).

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::sources::parsers::ParseResult;

/// Bump when the *shape* of the derivation hash changes, so old cache
/// entries invalidate cleanly. Mirrors Python's `CACHE_SCHEMA_VERSION`.
const CACHE_SCHEMA_VERSION: &str = "1";

/// Canonical hash of a params object. `serde_json::to_string` on a
/// `Map<String, Value>` iterates in insertion order by default — the
/// caller must build `params` with keys in a stable (e.g. sorted) order for
/// this to be order-independent the way Python's `json.dumps(sort_keys=True)`
/// is. See the params-ordering test below; do not assume `serde_json`
/// sorts for you.
fn params_hash(params: &Value) -> String {
    let sorted = sort_json_keys(params);
    let canonical = serde_json::to_string(&sorted).expect("params must be JSON-serializable");
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    format!("{:x}", hasher.finalize())[..16].to_string()
}

/// Recursively rebuild a `Value` with object keys in sorted order, so
/// `serde_json::to_string` output is order-independent — matching Python's
/// `json.dumps(..., sort_keys=True)`. `serde_json::Map` does NOT sort by
/// default (it preserves insertion order unless the `preserve_order`
/// feature is off, in which case it's a BTreeMap and DOES sort — verify
/// which mode this workspace's `serde_json` is compiled in before relying
/// on either behavior implicitly; this function makes the sort explicit so
/// the outcome doesn't depend on that compile-time detail).
fn sort_json_keys(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut sorted = Map::new();
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            for k in keys {
                sorted.insert(k.clone(), sort_json_keys(&m[k]));
            }
            Value::Object(sorted)
        }
        Value::Array(a) => Value::Array(a.iter().map(sort_json_keys).collect()),
        other => other.clone(),
    }
}

pub fn derivation_hash(
    rung: &str,
    model_id: &str,
    model_version: &str,
    engine: &str,
    params: &Value,
) -> String {
    let parts = [
        CACHE_SCHEMA_VERSION,
        rung,
        model_id,
        model_version,
        engine,
        &params_hash(params),
    ];
    let mut hasher = Sha256::new();
    hasher.update(parts.join("\0").as_bytes());
    format!("{:x}", hasher.finalize())
}

pub fn stamp_provenance(
    result: ParseResult,
    rung: &str,
    model_id: &str,
    model_version: &str,
    engine: &str,
    params: &Value,
) -> ParseResult {
    let mut metadata = result.metadata;
    metadata.insert("content_origin".to_string(), Value::String("derived".to_string()));
    metadata.insert("derived_rung".to_string(), Value::String(rung.to_string()));
    let mut derived = Map::new();
    derived.insert("rung".to_string(), Value::String(rung.to_string()));
    derived.insert("model".to_string(), Value::String(model_id.to_string()));
    derived.insert("model_version".to_string(), Value::String(model_version.to_string()));
    derived.insert("engine".to_string(), Value::String(engine.to_string()));
    derived.insert("params_hash".to_string(), Value::String(params_hash(params)));
    derived.insert(
        "ts".to_string(),
        Value::String(chrono_like_ts()),
    );
    metadata.insert("derived".to_string(), Value::Object(derived));
    ParseResult {
        text: result.text,
        metadata,
    }
}

/// RFC3339 UTC timestamp with no new dependency. `⚠️ UNVERIFIED:` this
/// workspace does not currently depend on `chrono` or `time` (checked:
/// absent from Cargo.toml's `[dependencies]`) — confirm that's still true
/// before implementing, and either add `time = "0.3"` (small, no chrono's
/// weight) as a real dependency, or use `std::time::SystemTime` +
/// hand-rolled RFC3339 formatting (more code, zero new deps). This plan
/// does not pick between them — it's a one-line call either way, decide
/// at implementation time based on whether `time`/`chrono` already exist
/// transitively (check `cargo tree | grep -E "chrono|^time "` first).
fn chrono_like_ts() -> String {
    unimplemented!("pick a timestamp strategy per the note above before implementing")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn hash_is_stable_for_same_inputs() {
        let a = derivation_hash("clip_tags", "clip-ViT-B-32", "1", "rust", &json!({"top_k": 3}));
        let b = derivation_hash("clip_tags", "clip-ViT-B-32", "1", "rust", &json!({"top_k": 3}));
        assert_eq!(a, b);
    }

    #[test]
    fn hash_ignores_dict_key_ordering() {
        let a = derivation_hash("r", "m", "1", "rust", &json!({"x": 1, "y": 2}));
        let b = derivation_hash("r", "m", "1", "rust", &json!({"y": 2, "x": 1}));
        assert_eq!(a, b, "params_hash must be order-independent — the real adversarial case, not just 'same object twice'");
    }

    #[test]
    fn hash_changes_when_engine_changes() {
        let base = derivation_hash("clip_tags", "m", "1", "python", &json!({}));
        assert_ne!(derivation_hash("clip_tags", "m", "1", "rust", &json!({})), base);
    }

    #[test]
    fn hash_changes_when_any_other_input_changes() {
        let base = derivation_hash("clip_tags", "m", "1", "rust", &json!({"top_k": 3}));
        assert_ne!(derivation_hash("ocr", "m", "1", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "other", "1", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "m", "2", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "m", "1", "rust", &json!({"top_k": 5})), base);
    }

    #[test]
    fn stamp_and_hash_agree_on_the_same_tuple() {
        // Provenance and the cache key must never drift apart — this test
        // proves AGREEMENT (both fed the literal same call), not two
        // independent assertions that happen to use equal-looking literals.
        let rung = "clip_tags";
        let model_id = "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text";
        let model_version = "5.13.3";
        let engine = "rust";
        let params = json!({"top_k": 3, "min_score": 0.2});

        let h = derivation_hash(rung, model_id, model_version, engine, &params);
        let stamped = stamp_provenance(
            ParseResult::new("cat"), rung, model_id, model_version, engine, &params,
        );
        let d = stamped.metadata["derived"].as_object().unwrap();
        assert_eq!(d["model"], model_id);
        assert_eq!(d["model_version"], model_version);
        assert_eq!(d["engine"], engine);
        // The params_hash embedded in the stamp must be the SAME 16-char
        // hash `derivation_hash` folds into its own output — recompute
        // independently and compare, proving the two functions can't
        // silently diverge on what "the params" means.
        assert!(h.len() == 64);
        assert_eq!(d["params_hash"].as_str().unwrap(), params_hash(&params));
    }
}
```

`⚠️ UNVERIFIED — flagged inline above:` the timestamp function is deliberately left `unimplemented!()`. This workspace's dependency list was checked for `chrono`/`time` and neither appeared in `Cargo.toml`'s `[dependencies]` at planning time — but re-check with `cargo tree | grep -E "chrono|^time "` before implementing, since either could be pulled transitively by `object_store`/`sqlx`/`reqwest` already, in which case reuse it rather than adding a new direct dependency.

- [ ] **Step 4: Run**

Run: `cd rust/chunkshop && cargo test --lib sources::derivers::provenance -q` — will fail to compile until Step 3's `chrono_like_ts` is resolved (deliberately, per the `⚠️ UNVERIFIED` above). Resolve it, then re-run. Expected: PASS (5 tests).

- [ ] **Step 5: Commit (two commits — Python and Rust are independently revertable)**

```bash
git add python/src/chunkshop/sources/derivers/provenance.py \
        python/src/chunkshop/sources/derivers/image_meta.py \
        python/src/chunkshop/sources/derivers/clip_tags.py \
        python/tests/chunkshop/test_derivation_provenance.py
git commit -m "feat(provenance): add engine field, closing SC-12's 'engine recorded in provenance' gap"

git add rust/chunkshop/src/sources/derivers/mod.rs rust/chunkshop/src/sources/derivers/provenance.rs \
        rust/chunkshop/src/sources/mod.rs
git commit -m "feat(rust,derivers): provenance hash + stamp, mirroring Python 1:1 including engine"
```

---

### Task 7: `DerivationCache` — adapted from `raw_store/local.rs`, made atomic

`raw_store/local.rs`'s `put` does a direct `std::fs::write` — **not atomic** (confirmed by reading it: no tempfile, no rename). Python's `cache.py` IS atomic (`tempfile.mkstemp` + `os.replace`). Copying `local.rs` verbatim would silently drop this guarantee — this task explicitly does not.

**Files:**
- Modify: `rust/chunkshop/Cargo.toml` (promote `tempfile` from `[dev-dependencies]` to `[dependencies]`)
- Create: `rust/chunkshop/src/sources/derivers/cache.rs`

**Interfaces:**
- Consumes: `ParseResult` (Task 1).
- Produces: `struct DerivationCache { root: PathBuf }` with `get`, `put`, `purge`; `fn cached_derive<F: FnOnce() -> anyhow::Result<ParseResult>>(cache: Option<&DerivationCache>, content_hash: &str, derivation_hash: &str, compute: F) -> anyhow::Result<(ParseResult, bool)>`.

- [ ] **Step 1: Promote `tempfile`**

In `rust/chunkshop/Cargo.toml`, move `tempfile = "3"` from `[dev-dependencies]` to `[dependencies]` (keep the version pin identical — the same crate now serves both test code and the cache's atomic-write path).

- [ ] **Step 2: Write the cache**

Create `rust/chunkshop/src/sources/derivers/cache.rs`:

```rust
//! Provenance-keyed memo cache for derivations. Mirrors
//! `python/src/chunkshop/sources/derivers/cache.py`. Layout matches
//! `raw_store/local.rs`'s content-addressed-directory pattern
//! (`sha256(key)[:2]/sha256(key).json`) — but `local.rs`'s `put` is a
//! direct `std::fs::write`, NOT atomic, unlike Python's cache.py
//! (tempfile + os.replace). This cache does its own atomic write via
//! `tempfile::Builder::tempfile_in` + `.persist()` rather than copying
//! `local.rs`'s non-atomic pattern verbatim.

use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::json;
use sha2::{Digest, Sha256};

use crate::sources::parsers::ParseResult;

pub fn default_cache_root() -> PathBuf {
    if let Ok(env) = std::env::var("CHUNKSHOP_CACHE_DIR") {
        return PathBuf::from(env);
    }
    let base = std::env::var("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs_home().join(".cache")
        });
    base.join("chunkshop").join("derived")
}

/// `⚠️ UNVERIFIED:` this workspace has no existing `$HOME`-lookup helper
/// that was found during research (checked `raw_store/`, `config.rs`) — the
/// `dirs` crate is the idiomatic answer but is NOT currently a dependency.
/// Confirm before implementing: either add `dirs = "5"` (tiny, no
/// transitive weight) as a real dependency, or read `std::env::var("HOME")`
/// directly (simpler, no new dep, but not Windows-correct — acceptable
/// here since the rest of this workspace already assumes a POSIX-ish
/// environment, e.g. `raw_store` file paths). Pick one at implementation
/// time; this stub exists so the file compiles for the test steps below.
fn dirs_home() -> PathBuf {
    unimplemented!("resolve $HOME per the note above before implementing")
}

pub struct DerivationCache {
    root: PathBuf,
}

impl DerivationCache {
    pub fn new(root: Option<PathBuf>) -> Self {
        Self {
            root: root.unwrap_or_else(default_cache_root),
        }
    }

    fn path_for(&self, content_hash: &str, derivation_hash: &str) -> PathBuf {
        let mut hasher = Sha256::new();
        hasher.update(content_hash.as_bytes());
        hasher.update(b"\0");
        hasher.update(derivation_hash.as_bytes());
        let key = format!("{:x}", hasher.finalize());
        self.root.join(&key[..2]).join(format!("{key}.json"))
    }

    pub fn get(&self, content_hash: &str, derivation_hash: &str) -> Option<ParseResult> {
        let path = self.path_for(content_hash, derivation_hash);
        let bytes = std::fs::read(&path).ok()?;
        let payload: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
        // Missing, unreadable, malformed, OR structurally wrong (e.g. valid
        // JSON that isn't an object, or an object missing "text"/"metadata")
        // entries are all misses, never errors — mirrors Python's
        // `except (OSError, ValueError, KeyError, TypeError)` catch-all,
        // which the adversarial test below specifically exercises with a
        // syntactically-valid-but-wrong-shape payload (e.g. a JSON array),
        // not just malformed JSON.
        let obj = payload.as_object()?;
        let text = obj.get("text")?.as_str()?.to_string();
        let metadata = obj.get("metadata")?.as_object()?.clone();
        Some(ParseResult { text, metadata })
    }

    pub fn put(&self, content_hash: &str, derivation_hash: &str, result: &ParseResult) -> anyhow::Result<()> {
        let path = self.path_for(content_hash, derivation_hash);
        let dir = path.parent().expect("path_for always has a parent");
        std::fs::create_dir_all(dir)?;
        let payload = json!({"text": result.text, "metadata": result.metadata});
        let bytes = serde_json::to_vec(&payload)?;

        // Atomic write: temp file in the SAME directory (same filesystem —
        // required for persist()'s rename to be atomic), then persist().
        let mut tmp = tempfile::Builder::new()
            .prefix(".tmp-")
            .tempfile_in(dir)?;
        tmp.write_all(&bytes)?;
        tmp.persist(&path)
            .map_err(|e| anyhow::anyhow!("atomic persist of cache entry failed: {e}"))?;
        Ok(())
    }

    pub fn purge(&self) -> anyhow::Result<usize> {
        let mut count = 0usize;
        if !self.root.exists() {
            return Ok(0);
        }
        for entry in walkdir_json_files(&self.root)? {
            let _ = std::fs::remove_file(&entry);
            count += 1;
        }
        Ok(count)
    }
}

/// `⚠️ UNVERIFIED:` no `walkdir` crate dependency exists in this workspace
/// today (checked Cargo.toml). Python's `purge()` uses `Path.rglob("*.json")`
/// — the simplest Rust equivalent needing no new dependency is a small
/// hand-rolled recursive directory walk (two levels deep by construction,
/// since `path_for` only ever nests one `key[..2]` directory under `root` —
/// so this does NOT need general recursion, just "list root's immediate
/// subdirectories, then list `*.json` in each"). Implement that directly
/// with `std::fs::read_dir` rather than adding `walkdir` for a two-level
/// walk; this stub exists so the file compiles for the test steps below.
fn walkdir_json_files(_root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    unimplemented!("two-level std::fs::read_dir walk per the note above")
}

/// Returns `(result, was_cache_hit)`. A `None` cache disables memoisation.
pub fn cached_derive<F>(
    cache: Option<&DerivationCache>,
    content_hash: &str,
    derivation_hash: &str,
    compute: F,
) -> anyhow::Result<(ParseResult, bool)>
where
    F: FnOnce() -> anyhow::Result<ParseResult>,
{
    let Some(cache) = cache else {
        return Ok((compute()?, false));
    };
    if let Some(hit) = cache.get(content_hash, derivation_hash) {
        return Ok((hit, true));
    }
    let result = compute()?;
    cache.put(content_hash, derivation_hash, &result)?;
    Ok((result, false))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cache(tmp: &tempfile::TempDir) -> DerivationCache {
        DerivationCache::new(Some(tmp.path().to_path_buf()))
    }

    #[test]
    fn miss_then_hit() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        assert!(c.get("c1", "d1").is_none());
        c.put("c1", "d1", &ParseResult::new("cat")).unwrap();
        let got = c.get("c1", "d1").unwrap();
        assert_eq!(got.text, "cat");
    }

    #[test]
    fn different_derivation_hash_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("old")).unwrap();
        assert!(c.get("c1", "d2").is_none());
    }

    #[test]
    fn different_content_hash_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("old")).unwrap();
        assert!(c.get("c2", "d1").is_none());
    }

    #[test]
    fn cached_derive_computes_once() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        let calls = std::cell::Cell::new(0);
        let compute = || {
            calls.set(calls.get() + 1);
            Ok(ParseResult::new("expensive"))
        };
        let (r1, hit1) = cached_derive(Some(&c), "c1", "d1", compute).unwrap();
        let (r2, hit2) = cached_derive(Some(&c), "c1", "d1", compute).unwrap();
        assert_eq!(r1.text, "expensive");
        assert_eq!(r2.text, "expensive");
        assert!(!hit1 && hit2);
        assert_eq!(calls.get(), 1, "second call must not recompute");
    }

    #[test]
    fn purge_empties_the_cache() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x")).unwrap();
        c.put("c2", "d1", &ParseResult::new("y")).unwrap();
        assert_eq!(c.purge().unwrap(), 2);
        assert!(c.get("c1", "d1").is_none());
    }

    #[test]
    fn malformed_json_entry_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x")).unwrap();
        for entry in std::fs::read_dir(tmp.path()).unwrap() {
            let sub = entry.unwrap().path();
            if sub.is_dir() {
                for f in std::fs::read_dir(&sub).unwrap() {
                    std::fs::write(f.unwrap().path(), b"{not json").unwrap();
                }
            }
        }
        assert!(c.get("c1", "d1").is_none());
    }

    #[test]
    fn well_formed_but_wrong_shaped_json_is_also_a_miss() {
        // The adversarial case the Python plan's own retro named:
        // "corrupt entries are misses (tested malformed JSON, missed
        // valid-non-dict)". A syntactically valid JSON ARRAY (not an
        // object with text/metadata) must also degrade to a miss, not
        // panic or silently return garbage.
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x")).unwrap();
        for entry in std::fs::read_dir(tmp.path()).unwrap() {
            let sub = entry.unwrap().path();
            if sub.is_dir() {
                for f in std::fs::read_dir(&sub).unwrap() {
                    std::fs::write(f.unwrap().path(), b"[1, 2, 3]").unwrap();
                }
            }
        }
        assert!(
            c.get("c1", "d1").is_none(),
            "a syntactically valid but wrong-shaped JSON payload must be a miss, not a panic"
        );
    }
}
```

- [ ] **Step 3: Resolve the two `unimplemented!()` stubs**

Both `dirs_home()` and `walkdir_json_files()` are deliberately stubbed with `⚠️ UNVERIFIED` notes above — resolve each per its comment (check `cargo tree` for an existing `dirs` dependency before adding one; hand-roll the two-level walk with `std::fs::read_dir` rather than adding `walkdir` for what is structurally a two-level, not general-recursive, directory scan).

- [ ] **Step 4: Run**

Run: `cd rust/chunkshop && cargo test --lib sources::derivers::cache -q`
Expected: PASS (6 tests, including the two adversarial-input tests above that the Python plan's own retro flagged as previously under-tested).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/sources/derivers/cache.rs
git commit -m "feat(rust,derivers): DerivationCache with atomic writes (local.rs's pattern is NOT atomic; this is)"
```

---

### Task 8: `FilesSourceConfig` gains `on_empty`/`derive`/`cache`; 3 rejection tests flip; `on_empty` wired into `FilesSource`

**Files:**
- Modify: `rust/chunkshop/src/config.rs`
- Modify: `rust/chunkshop/src/sources/files.rs`

**Interfaces:**
- Consumes: nothing new from prior tasks (config-only; deriver loading is Task 9).
- Produces: `FilesSourceConfig` gains `on_empty: String` (default `"error"`), `derive: Option<HashMap<String, DeriverConfig>>`, `cache: DerivationCacheSettings`; `DeriverConfig` is `#[serde(tag = "rung", rename_all = "snake_case")]` (Python's discriminator field is `rung`, not `type` — do not copy the `type`-tagged pattern used by `SourceConfig`/`ExtractorConfig` without checking the field name).

- [ ] **Step 1: Extend `FilesSourceConfig` and add the deriver-config union**

In `rust/chunkshop/src/config.rs`, replace the `FilesSourceConfig` struct (verified at `config.rs:454-461` at planning time — re-locate before editing, since Tasks 1-7 don't touch this file and line numbers should be stable, but confirm) and its doc comment (the doc comment currently *explains why the media fields are absent* — that explanation is now wrong and must be replaced, not left stale):

```rust
/// Mirrors Python `chunkshop.config.FilesSource`. `on_empty`/`derive`/`cache`
/// (media-ingest fields) now have Rust support (Plan D). `incremental` is
/// still unmodeled for the `files` source specifically (unlike `s3`,
/// `pg_table`, and `http`, which do implement `IncrementalSource`) — that
/// remains a separate, tracked follow-up (see MEDIA-INGEST-ROADMAP.md),
/// not part of this plan. `deny_unknown_fields` still refuses `incremental`
/// with a named error rather than silently full-resyncing.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilesSourceConfig {
    pub glob: String,
    #[serde(default = "default_id_from")]
    pub id_from: String,
    #[serde(default = "default_encoding")]
    pub encoding: String,
    #[serde(default = "default_on_empty")]
    pub on_empty: String,
    pub derive: Option<std::collections::HashMap<String, DeriverConfig>>,
    #[serde(default)]
    pub cache: DerivationCacheSettings,
}

fn default_on_empty() -> String {
    "error".to_string()
}

/// Mirrors Python's `ImageMetaDeriverConfig`/`ClipTagsDeriverConfig` /
/// `DeriverConfig = Annotated[Union[...], Field(discriminator="rung")]`.
/// The discriminator field is `rung`, NOT `type` — unlike every other
/// discriminated union in this file (`SourceConfig`, `ExtractorConfig`,
/// etc.), which tag on `type`. Do not copy that convention here; Python's
/// `config.py:85-103` uses `rung` and the YAML shape must match.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "rung", rename_all = "snake_case")]
pub enum DeriverConfig {
    ImageMeta(ImageMetaDeriverConfig),
    ClipTags(ClipTagsDeriverConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct ImageMetaDeriverConfig {
    #[serde(default = "default_true")]
    pub path_segments: bool,
    #[serde(default = "default_segment_depth")]
    pub segment_depth: u32,
}

fn default_segment_depth() -> u32 {
    3
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClipTagsDeriverConfig {
    pub vocabulary: Vec<String>,
    #[serde(default = "default_top_k")]
    pub top_k: usize,
    #[serde(default = "default_min_score")]
    pub min_score: f32,
    #[serde(default = "default_vision_model")]
    pub vision_model: String,
    #[serde(default = "default_text_model")]
    pub text_model: String,
}

fn default_top_k() -> usize {
    3
}
fn default_min_score() -> f32 {
    0.20
}
fn default_vision_model() -> String {
    "Qdrant/clip-ViT-B-32-vision".to_string()
}
fn default_text_model() -> String {
    "Qdrant/clip-ViT-B-32-text".to_string()
}

/// Mirrors Python's `DerivationCacheSettings`.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct DerivationCacheSettings {
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub root: Option<String>,
}
```

`default_true` already exists in this file (used by `SessionEpisodeFramerConfig`, confirmed at `config.rs:393`) — reuse it, don't redeclare.

- [ ] **Step 2: Flip 3 rejection tests to acceptance tests; leave `incremental`'s alone**

In `rust/chunkshop/src/config.rs`'s `mod tests`, replace `rejects_files_source_derive_field`, `rejects_files_source_on_empty_field`, and `rejects_files_source_cache_field` (all three currently assert a load error) with acceptance tests. This is a deliberate, named flip — not a silent test deletion:

```rust
    #[test]
    fn files_source_derive_field_now_loads_correctly() {
        // Was `rejects_files_source_derive_field` before Plan D — the
        // interim-guard test is now obsolete because real support landed.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png|jpg":
      rung: image_meta
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("derive block must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        let derive = f.derive.as_ref().expect("derive must be Some");
        assert!(derive.contains_key("png|jpg"));
    }

    #[test]
    fn files_source_on_empty_field_now_loads_correctly() {
        // Was `rejects_files_source_on_empty_field`.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", on_empty: skip }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("on_empty must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert_eq!(f.on_empty, "skip");
    }

    #[test]
    fn files_source_cache_field_now_loads_correctly() {
        // Was `rejects_files_source_cache_field`.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  cache:
    enabled: false
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("cache block must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert!(!f.cache.enabled);
    }

    #[test]
    fn typo_in_derive_block_is_still_rejected() {
        // deny_unknown_fields must still catch config typos INSIDE the now-
        // modeled derive block — this is the equivalent of Python's
        // test_typo_in_derive_block_is_rejected (test_derive_config.py).
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: clip_tags
      vocabluary: ["x"]
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        assert!(load_config(&path).is_err(), "typo'd field must fail to load");
    }
```

`rejects_files_source_incremental_field` is **not touched** — it must still pass unmodified (files-source incremental sync remains unimplemented in Rust; out of scope here).

- [ ] **Step 3: Wire `on_empty` into `FilesSource`**

In `rust/chunkshop/src/sources/files.rs`, wrap the per-path body added in Task 2 Step 4 with an empty-text check, mirroring Python's `_document_for`:

```rust
            if result.text.trim().is_empty() {
                match self.cfg.on_empty.as_str() {
                    "error" => {
                        return Err(crate::sources::parsers::EmptyDerivationError {
                            path: p.clone(),
                            parser: ext.clone(),
                        }
                        .into());
                    }
                    "skip" => continue,
                    "index" => {} // fall through, index the empty document
                    other => {
                        return Err(anyhow::anyhow!("unknown on_empty: {other}"));
                    }
                }
            }
```

Insert this immediately after `let result = deriver.derive(&p)...?;` and before building `metadata`/`Document` in Task 2 Step 4's loop body.

- [ ] **Step 4: Test `on_empty` behavior**

Add to `rust/chunkshop/tests/media_parser_formats.rs`:

```rust
#[test]
fn on_empty_error_is_the_default() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.txt", b"   \n  ");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.txt", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: "error".to_string(),
        derive: None,
        cache: Default::default(),
    };
    let err = FilesSource::new(cfg).iter_documents().unwrap_err();
    assert!(format!("{err:#}").contains("a.txt"));
}

#[test]
fn on_empty_skip_omits_the_document() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.txt", b"   \n  ");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.txt", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: "skip".to_string(),
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 0);
}
```

`⚠️ UNVERIFIED:` `DerivationCacheSettings` needs a `Default` impl for `Default::default()` above — Step 1's struct derives `Default` already (`#[derive(..., Default)]`), so this should compile, but confirm `bool: Default` gives `false`, not `default_true()`'s `true` — **it does**, `#[derive(Default)]` ignores `#[serde(default = ...)]` and uses the type's own `Default` (`false` for `bool`). This means `DerivationCacheSettings::default()` (used in plain Rust construction, e.g. these tests) has `enabled: false`, while YAML-deserializing an absent `cache:` block gives `enabled: true` (via `#[serde(default = "default_true")]`). **These two "defaults" disagree** — a real, non-obvious footgun. Fix: implement `Default` by hand instead of deriving it, so both paths agree:

```rust
impl Default for DerivationCacheSettings {
    fn default() -> Self {
        Self { enabled: true, root: None }
    }
}
```

Remove `Default` from the `#[derive(...)]` list on `DerivationCacheSettings` in Step 1 and add this impl instead.

Run: `cd rust/chunkshop && cargo test --test media_parser_formats on_empty -q`

- [ ] **Step 5: Full-suite regression check**

Run: `cd rust/chunkshop && cargo test -q` (no `--features media` — confirm the base build, matching CI's likely default invocation, still compiles and passes) and `cargo test --features media,embedder-hub -q` (full feature set). Expected: prior baseline (427) plus this task's new/flipped tests, zero failures, zero unexpected `unimplemented!()` panics (both Task 6 and Task 7's stubs must already be resolved by this point since `files.rs` and `config.rs` don't depend on them directly, but the crate must still compile as a whole).

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/src/config.rs rust/chunkshop/src/sources/files.rs \
        rust/chunkshop/tests/media_parser_formats.rs
git commit -m "feat(rust,config): FilesSourceConfig on_empty/derive/cache fields; 3 rejection tests flip to acceptance"
```

---

### Task 9: Rung 0 `ImageMetaDeriver` + full `FilesSource` deriver wiring + contract-level parity test

Closes M1. Wires `cfg.derive` into an actual per-extension deriver table (mirroring Python's `_derived_exts`-gated cache eligibility — the "subtle correctness point" the scoping calls out), and proves rung-0 schema parity against Python via a golden fixture.

**Files:**
- Create: `rust/chunkshop/src/sources/derivers/image_meta.rs`
- Modify: `rust/chunkshop/src/sources/files.rs` (deriver table + cache-eligibility gate)
- Modify: `rust/chunkshop/src/sources/derivers/mod.rs` (`load_deriver`)
- Create: `rust/chunkshop/tests/media_derivation_parity.rs`, `rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json`

**Interfaces:**
- Consumes: `stamp_provenance` (Task 6), `Deriver` (Task 1), `DerivationCache`/`cached_derive` (Task 7), `ImageMetaDeriverConfig` (Task 8).
- Produces: `ImageMetaDeriver { path_segments: bool, segment_depth: u32 }` implementing `Deriver`; `fn load_deriver(cfg: &DeriverConfig) -> Box<dyn Deriver>`.

- [ ] **Step 1: `humanise()` and the deriver**

Create `rust/chunkshop/src/sources/derivers/image_meta.rs`:

```rust
//! Rung 0 for images: deterministic metadata, no model, no network. Mirrors
//! `python/src/chunkshop/sources/derivers/image_meta.py`.

use std::path::Path;

use image::{ColorType, GenericImageView, ImageFormat};
use serde_json::{json, Map, Value};

use crate::sources::parsers::{Deriver, ParseResult};
use super::provenance::stamp_provenance;

/// `goblin_warrior-02` -> `goblin warrior 02`. Splits on `_`, `-`, `.`.
pub fn humanise(stem: &str) -> String {
    stem.split(|c| c == '_' || c == '-' || c == '.')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Matches Pillow's `img.format` string exactly for the 8 extensions this
/// deriver supports — NOT `ImageFormat`'s `extensions_str()[0]`, which
/// would give "jpg" (not Pillow's "JPEG") for .jpg files. Verified via
/// docs.rs: `image` 0.25's `ImageFormat::extensions_str()` for `Jpeg`
/// returns `["jpg", "jpeg"]` (jpg first) — using it naively would silently
/// diverge from Python's metadata value on every JPEG.
fn pillow_style_format_name(fmt: ImageFormat) -> &'static str {
    match fmt {
        ImageFormat::Png => "PNG",
        ImageFormat::Jpeg => "JPEG",
        ImageFormat::WebP => "WEBP",
        ImageFormat::Gif => "GIF",
        ImageFormat::Bmp => "BMP",
        ImageFormat::Tiff => "TIFF",
        other => {
            // Anything else reaching here is a bug in the extension
            // registry (Task 2's registry only routes the 8 supported
            // extensions to this deriver) — fall back to Debug rather
            // than panic, since a metadata *value* mismatch is much
            // cheaper than a crashed ingest.
            Box::leak(format!("{other:?}").to_uppercase().into_boxed_str())
        }
    }
}

pub struct ImageMetaDeriver {
    pub path_segments: bool,
    pub segment_depth: usize,
}

impl Default for ImageMetaDeriver {
    fn default() -> Self {
        Self { path_segments: true, segment_depth: 3 }
    }
}

impl Deriver for ImageMetaDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let img = image::open(path)
            .map_err(|e| anyhow::anyhow!("failed to parse image {}: {e}", path.display()))?;
        let (width, height) = img.dimensions();
        let color = img.color();
        let fmt = ImageFormat::from_path(path).unwrap_or(ImageFormat::Png);
        let fmt_name = pillow_style_format_name(fmt);

        // `has_alpha`: `image` 0.25's ColorType::has_alpha() covers the
        // decoded color type (Rgba8/La8/etc). Pillow ADDITIONALLY checks
        // `"transparency" in img.info` for paletted PNGs with a tRNS
        // chunk — the `image` crate decodes palettes away entirely (no
        // "P" mode equivalent), so this is a KNOWN, documented divergence
        // for that specific case, not a silently-assumed match. See the
        // adversarial test below.
        let has_alpha = color.has_alpha();

        let mut words = vec![humanise(
            path.file_stem().and_then(|s| s.to_str()).unwrap_or(""),
        )];
        if self.path_segments {
            if let Some(parent) = path.parent() {
                let segs: Vec<&str> = parent
                    .components()
                    .filter_map(|c| c.as_os_str().to_str())
                    .collect();
                let start = segs.len().saturating_sub(self.segment_depth);
                for s in &segs[start..] {
                    words.push(humanise(s));
                }
            }
        }
        let mut text = words
            .into_iter()
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        if text.trim().is_empty() {
            text = format!("{} image", fmt_name.to_lowercase());
        }

        let mut metadata = Map::new();
        metadata.insert("width".to_string(), json!(width));
        metadata.insert("height".to_string(), json!(height));
        metadata.insert("image_format".to_string(), Value::String(fmt_name.to_string()));
        metadata.insert("image_mode".to_string(), Value::String(color_type_mode(color)));
        metadata.insert(
            "aspect_ratio".to_string(),
            json!(if height > 0 { (width as f64 / height as f64 * 10000.0).round() / 10000.0 } else { 0.0 }),
        );
        metadata.insert("has_alpha".to_string(), Value::Bool(has_alpha));

        let result = ParseResult::with_metadata(text, metadata);
        let params = json!({"path_segments": self.path_segments, "segment_depth": self.segment_depth});
        Ok(stamp_provenance(result, "image_meta", "deterministic", "1", "rust", &params))
    }
}

/// Best-effort Pillow-style mode string. `image`'s `ColorType` has no
/// palette ("P") variant (palettes are decoded away), so this is NOT a
/// guaranteed value-level match with Python's Pillow `img.mode` for
/// paletted sources — only for the RGB/RGBA/L/LA fixtures this plan's
/// tests actually use. Contract-level parity (the key `image_mode` exists,
/// is a string) holds regardless; value-level parity is asserted only for
/// the fixtures covered here.
fn color_type_mode(c: ColorType) -> String {
    match c {
        ColorType::L8 | ColorType::L16 => "L",
        ColorType::La8 | ColorType::La16 => "LA",
        ColorType::Rgb8 | ColorType::Rgb16 | ColorType::Rgb32F => "RGB",
        ColorType::Rgba8 | ColorType::Rgba16 | ColorType::Rgba32F => "RGBA",
        _ => "UNKNOWN",
    }
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn humanise_splits_on_underscore_dash_dot() {
        assert_eq!(humanise("goblin_warrior-02"), "goblin warrior 02");
    }

    fn write_rgb_png(dir: &Path, name: &str, w: u32, h: u32) -> std::path::PathBuf {
        let p = dir.join(name);
        let img: image::RgbImage = image::ImageBuffer::from_pixel(w, h, image::Rgb([200, 30, 30]));
        img.save(&p).unwrap();
        p
    }

    fn write_rgba_png(dir: &Path, name: &str, w: u32, h: u32) -> std::path::PathBuf {
        let p = dir.join(name);
        let img: image::RgbaImage =
            image::ImageBuffer::from_pixel(w, h, image::Rgba([200, 30, 30, 255]));
        img.save(&p).unwrap();
        p
    }

    #[test]
    fn emits_dimensions_and_format() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 64, 32);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["width"], json!(64));
        assert_eq!(result.metadata["height"], json!(32));
        assert_eq!(result.metadata["image_format"], json!("PNG"));
        assert_eq!(result.metadata["aspect_ratio"], json!(2.0));
    }

    #[test]
    fn has_alpha_true_for_rgba() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgba_png(dir.path(), "sprite.png", 8, 8);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["has_alpha"], json!(true));
    }

    #[test]
    fn has_alpha_false_for_rgb() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 8, 8);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["has_alpha"], json!(false));
    }

    #[test]
    fn text_includes_humanised_filename_and_path_segments() {
        let dir = tempfile::tempdir().unwrap();
        let sprites_dir = dir.path().join("sprites");
        std::fs::create_dir(&sprites_dir).unwrap();
        let p = write_rgb_png(&sprites_dir, "goblin_warrior.png", 8, 8);
        let text = ImageMetaDeriver::default().derive(&p).unwrap().text.to_lowercase();
        assert!(text.contains("goblin"), "{text}");
        assert!(text.contains("warrior"), "{text}");
        assert!(text.contains("sprites"), "{text}");
    }

    #[test]
    fn never_returns_empty_text_even_for_a_bare_stem() {
        // Adversarial case per this plan's own rule 3: a filename that
        // humanises to nothing (all separator characters) must still fall
        // back to "<format> image", not blank text that on_empty=error
        // would then reject.
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "___.png", 8, 8);
        let deriver = ImageMetaDeriver { path_segments: false, segment_depth: 3 };
        let text = deriver.derive(&p).unwrap().text;
        assert!(!text.trim().is_empty(), "must never be empty, got {text:?}");
        assert!(text.contains("image"));
    }

    #[test]
    fn provenance_is_stamped_with_rust_engine() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 8, 8);
        let md = ImageMetaDeriver::default().derive(&p).unwrap().metadata;
        assert_eq!(md["content_origin"], json!("derived"));
        assert_eq!(md["derived_rung"], json!("image_meta"));
        assert_eq!(md["derived"]["engine"], json!("rust"));
        assert_eq!(md["derived"]["model"], json!("deterministic"));
    }
}
```

`⚠️ UNVERIFIED:` `pillow_style_format_name`'s fallback arm uses `Box::leak` to satisfy the `&'static str` return type for a code path that this plan believes is unreachable (Task 2's registry only routes the 8 supported extensions here). `Box::leak` is a real, deliberate small memory leak — acceptable for an unreachable-in-practice error path, but confirm this reasoning holds once Task 2's final registry is in place (i.e., no extension reaches `ImageMetaDeriver::derive` that isn't one of png/jpg/jpeg/webp/gif/bmp/tiff/tif) before shipping; if it can be reached, change the function to return `anyhow::Result<&'static str>` instead and propagate an error rather than leaking.

- [ ] **Step 2: `load_deriver`**

In `rust/chunkshop/src/sources/derivers/mod.rs`, add:

```rust
use crate::config::DeriverConfig;
use crate::sources::parsers::Deriver;

pub fn load_deriver(cfg: &DeriverConfig) -> Box<dyn Deriver> {
    match cfg {
        DeriverConfig::ImageMeta(c) => Box::new(image_meta::ImageMetaDeriver {
            path_segments: c.path_segments,
            segment_depth: c.segment_depth as usize,
        }),
        DeriverConfig::ClipTags(_) => {
            // Task 11 replaces this arm. Left as an explicit panic (not a
            // silent no-op) so a `derive: {rung: clip_tags}` YAML fails
            // loudly if this task lands ahead of Task 11 in a partial
            // checkout, rather than mysteriously returning wrong results.
            unimplemented!("ClipTagsDeriver lands in Task 11")
        }
    }
}
```

- [ ] **Step 3: Wire the deriver table + cache-eligibility gate into `FilesSource`**

In `rust/chunkshop/src/sources/files.rs`, extend `FilesSource` to hold a per-extension deriver override table and cache, built from `cfg.derive`, mirroring Python's `__init__` (the `_derived_exts` set + the comment explaining why ONLY derive-configured extensions are cache-eligible):

```rust
pub struct FilesSource {
    cfg: FilesSourceConfig,
    derived: std::collections::HashMap<String, Box<dyn crate::sources::parsers::Deriver>>,
    cache: Option<crate::sources::derivers::cache::DerivationCache>,
}

impl FilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        let mut derived: std::collections::HashMap<String, Box<dyn crate::sources::parsers::Deriver>> =
            std::collections::HashMap::new();
        if let Some(derive_cfg) = &cfg.derive {
            for (pattern, dcfg) in derive_cfg {
                let deriver = crate::sources::derivers::load_deriver(dcfg);
                for ext in pattern.split('|') {
                    // NOTE: a HashMap<String, Box<dyn Deriver>> cannot hold
                    // the SAME Box under multiple keys without cloning the
                    // trait object, which `dyn Deriver` doesn't support
                    // generically. Rebuild per extension instead — see
                    // Step 3b below; this initial pass is illustrative only.
                    let _ = ext;
                }
                let _ = deriver;
            }
        }
        let cache = if cfg.derive.is_some() && cfg.cache.enabled {
            Some(crate::sources::derivers::cache::DerivationCache::new(
                cfg.cache.root.clone().map(std::path::PathBuf::from),
            ))
        } else {
            None
        };
        Self { cfg, derived, cache }
    }
}
```

- [ ] **Step 3b: Fix the illustrative gap — rebuild a deriver per extension, not a shared `Box`**

`load_deriver` (Step 2) must be callable once per extension when a pattern like `"png|jpg"` fans out to multiple keys, OR the config loader should build ONE deriver and the map should hold `Rc<dyn Deriver>` (cheaply cloneable) instead of `Box`. `Rc` is simpler and avoids re-parsing config per extension — revise `derived`'s type to `HashMap<String, std::rc::Rc<dyn crate::sources::parsers::Deriver>>` and Step 2's `load_deriver` to return `Rc<dyn Deriver>`, then:

```rust
        let mut derived: std::collections::HashMap<String, std::rc::Rc<dyn crate::sources::parsers::Deriver>> =
            std::collections::HashMap::new();
        if let Some(derive_cfg) = &cfg.derive {
            for (pattern, dcfg) in derive_cfg {
                let deriver: std::rc::Rc<dyn crate::sources::parsers::Deriver> =
                    crate::sources::derivers::load_deriver(dcfg).into();
                for ext in pattern.split('|') {
                    derived.insert(ext.trim().to_lowercase(), deriver.clone());
                }
            }
        }
```

(`Box<dyn Deriver>: Into<Rc<dyn Deriver>>` — verify this conversion exists for the actual trait object; if not, change `load_deriver`'s return type to `Rc<dyn Deriver>` directly instead of converting after the fact.) `FilesSource` becomes `!Send`/`!Sync` once it holds `Rc` — check whether anything in the pipeline requires `FilesSource: Send` (the orchestrator spawns cells as **subprocesses**, per `CLAUDE.md`'s "Thread discipline" note, not threads sharing a `FilesSource` — so this is likely fine, but confirm no async trait bound on `AnySource`/`Deriver` requires `Send` before committing to `Rc` over `Arc`).

- [ ] **Step 4: Route matched extensions through the deriver table + cache-eligibility gate**

Replace Task 2 Step 4's `get_deriver(&ext)` call with a lookup that prefers `self.derived`, falling back to the registry, and only routes cache-eligible (i.e., `derived`-table) extensions through the cache — mirroring the "subtle correctness point" from `files.py:165-180`:

```rust
            let (result, content_hash) = if let Some(deriver) = self.derived.get(&ext) {
                let bytes = std::fs::read(&p)
                    .with_context(|| format!("reading {}", p.display()))?;
                let content_hash = hash_bytes(&bytes);
                let d_hash = crate::sources::derivers::provenance::derivation_hash(
                    "TODO_RUNG", "TODO_MODEL", "TODO_VERSION", "rust", &serde_json::json!({}),
                );
                // `⚠️ UNVERIFIED / INCOMPLETE:` the derivation_hash inputs
                // above are placeholders. Each concrete Deriver (image_meta,
                // clip_tags) needs to expose its OWN (rung, model_id,
                // model_version, params) so this call site can build the
                // real hash — mirrors Python's `getattr(parser,
                // "derivation_hash", None)` duck-typed callback. Add a
                // second trait, `CacheableDeriver: Deriver { fn cache_key_parts(&self) -> (String, String, String, serde_json::Value); }`,
                // implemented by ImageMetaDeriver/ClipTagsDeriver, and use
                // `derived.get(&ext)` plus a downcast (or a trait-object
                // method) to obtain real parts here instead of literal
                // "TODO" strings. Resolve this before Step 5's tests, which
                // will fail loudly (wrong cache keys, permanent misses) if
                // left as-is — that failure is the intended signal to fix
                // this properly, not a flake to retry past.
                let (r, _hit) = crate::sources::derivers::cache::cached_derive(
                    self.cache.as_ref(),
                    &content_hash,
                    &d_hash,
                    || deriver.derive(&p),
                )?;
                (r, Some(content_hash))
            } else {
                let deriver = crate::sources::parsers::registry::get_deriver(&ext);
                (deriver.derive(&p).with_context(|| format!("deriving {}", p.display()))?, None)
            };
```

Add the small helper (mirrors Python's `_hash_bytes`):

```rust
fn hash_bytes(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}
```

- [ ] **Step 4b: Resolve the `CacheableDeriver` gap named in Step 4**

Add to `rust/chunkshop/src/sources/parsers/mod.rs`:

```rust
/// Derivers that participate in the derivation cache expose their own
/// (rung, model_id, model_version, params) — the exact tuple both
/// `derivation_hash` and `stamp_provenance` are called with, so cache key
/// and provenance stamp can never silently disagree (Global Constraints).
/// Mirrors Python's duck-typed `getattr(parser, "derivation_hash", None)`.
pub trait CacheableDeriver: Deriver {
    fn cache_key_parts(&self) -> (String, String, String, serde_json::Value);
}
```

Implement it for `ImageMetaDeriver` (Task 9) and `ClipTagsDeriver` (Task 11):

```rust
impl crate::sources::parsers::CacheableDeriver for ImageMetaDeriver {
    fn cache_key_parts(&self) -> (String, String, String, serde_json::Value) {
        (
            "image_meta".to_string(),
            "deterministic".to_string(),
            "1".to_string(),
            serde_json::json!({"path_segments": self.path_segments, "segment_depth": self.segment_depth}),
        )
    }
}
```

Change `derived`'s map type (Step 3b) to `HashMap<String, Rc<dyn CacheableDeriver>>` (supertrait, so `.derive()` is still callable) and Step 4's placeholder call becomes:

```rust
            let (result, content_hash) = if let Some(deriver) = self.derived.get(&ext) {
                let bytes = std::fs::read(&p).with_context(|| format!("reading {}", p.display()))?;
                let content_hash = hash_bytes(&bytes);
                let (rung, model_id, model_version, params) = deriver.cache_key_parts();
                let d_hash = crate::sources::derivers::provenance::derivation_hash(
                    &rung, &model_id, &model_version, "rust", &params,
                );
                let (r, _hit) = crate::sources::derivers::cache::cached_derive(
                    self.cache.as_ref(), &content_hash, &d_hash, || deriver.derive(&p),
                )?;
                (r, Some(content_hash))
            } else {
                let deriver = crate::sources::parsers::registry::get_deriver(&ext);
                (deriver.derive(&p).with_context(|| format!("deriving {}", p.display()))?, None)
            };
```

- [ ] **Step 5: Contract-level parity test (rung 0 only — rung 1 joins in Task 14)**

Generate the golden fixture once via Python (`docs/samples/images/sprite_knight.png` already exists — reuse it, don't create a new fixture):

```bash
cd python && uv run --no-sync python3 - <<'EOF'
import json
from pathlib import Path
from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

img = Path("../docs/samples/images/sprite_knight.png")
result = ImageMetaDeriver().derive(img)
out = {"metadata_keys": sorted(result.metadata.keys()),
       "derived_keys": sorted(result.metadata["derived"].keys())}
Path("../rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json").write_text(json.dumps(out, indent=2))
print(out)
EOF
```

Create `rust/chunkshop/tests/media_derivation_parity.rs`:

```rust
//! SC-12 contract-level parity: same input, same SCHEMA (metadata keys +
//! provenance shape) on both implementations. NOT value-level (the "ts"
//! timestamp and "source_path" absolute path legitimately differ) — see
//! SPEC.md's SC-12 for the bit-level/contract-level split.

use std::collections::HashSet;

use chunkshop::sources::derivers::image_meta::ImageMetaDeriver;
use chunkshop::sources::parsers::Deriver;

#[test]
fn rung0_metadata_keys_match_python() {
    let fixture: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/parity-fixtures/media_derivation_reference.json").unwrap(),
    )
    .unwrap();
    let expected_keys: HashSet<String> = fixture["metadata_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let expected_derived_keys: HashSet<String> = fixture["derived_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();

    let img_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/samples/images/sprite_knight.png");
    let result = ImageMetaDeriver::default().derive(std::path::Path::new(img_path)).unwrap();
    let actual_keys: HashSet<String> = result.metadata.keys().cloned().collect();
    let actual_derived_keys: HashSet<String> = result.metadata["derived"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();

    assert_eq!(actual_keys, expected_keys, "top-level metadata key set must match Python");
    assert_eq!(actual_derived_keys, expected_derived_keys, "derived.* key set must match Python");
}

#[test]
fn engine_field_names_the_implementation() {
    let img_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/samples/images/sprite_knight.png");
    let result = ImageMetaDeriver::default().derive(std::path::Path::new(img_path)).unwrap();
    assert_eq!(result.metadata["derived"]["engine"], serde_json::json!("rust"));
}
```

Run: `cd rust/chunkshop && cargo test --features media --test media_derivation_parity -q`
Expected: PASS (2 tests). If `rung0_metadata_keys_match_python` fails on the `derived_keys` set, the likely cause is Task 6's Python-side `engine` addition not yet regenerating the fixture — regenerate it after Task 6 lands, not before.

- [ ] **Step 6: Full regression + commit**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub -q`

```bash
git add rust/chunkshop/src/sources/derivers/image_meta.rs rust/chunkshop/src/sources/derivers/mod.rs \
        rust/chunkshop/src/sources/parsers/mod.rs rust/chunkshop/src/sources/files.rs \
        rust/chunkshop/tests/media_derivation_parity.rs \
        rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json
git commit -m "feat(rust,derivers): rung 0 image_meta deriver + full FilesSource wiring; closes M1"
```

**M1 complete at this commit.** A `files` source pointed at a mixed corpus (pdf/docx/xlsx/html/png mixed with md) now produces attributable, schema-matching output on Rust instead of silently UTF-8-misreading binary files.

---

### Task 10: CLIP vision-tower preprocessing — investigate, then record the SC-12 amendment

**No new production code.** This task is a decision point, not an implementation task — matching this plan's own "verify before writing" discipline: the roadmap already recorded (commit `8b0c5a7`, same day as this scoping) that the CLIP **vision** tower FAILS the 1e-6 tolerance that the text towers pass, with a root cause already identified during this plan's own research (see below) that this plan should not silently re-litigate or re-guess.

**Files:**
- Modify: `spec/chunkshop-media-ingest/SPEC.md` (SC-12's bit-level clause)
- Modify: `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md` (mark this decision made, not just measured)

**Interfaces:** none (docs-only task).

- [ ] **Step 1: Confirm the root cause found during this plan's research**

`rust/chunkshop/examples/parity_probe.rs` (already committed, HEAD `8b0c5a7`) measured `cosine(py, rust) = 0.9999893129` (0.265°) on `docs/samples/images/sprite_knight.png` — a **6.8e-4 max abs delta**, ~700x the 1e-6 tolerance the text towers pass at. The roadmap correctly identifies this as "image preprocessing divergence... not an inference difference" but leaves the exact mechanism as "almost certainly." This plan's research pinned it down: `fastembed-rs` 5.13.3's `image_embedding/utils.rs::load_preprocessor` **hardcodes `FilterType::CatmullRom`** for the CLIP preprocessor's resize step regardless of what `preprocessor_config.json`'s `resample` field actually specifies (verified by reading the vendored crate source — `~/.cargo/registry/src/.../fastembed-5.13.3/src/image_embedding/utils.rs`, the `"CLIPImageProcessor" =>` match arm). Python's PIL-based preprocessing, by contrast, respects HF's CLIP default (`PILImageResampling.BICUBIC`). `CatmullRom` and PIL's `BICUBIC` are both "bicubic-family" filters but are **different implementations** (different kernel support width / edge handling) — a decode-and-resample library difference, confirming the roadmap's hypothesis with a named, verified cause rather than a plausible guess.

- [ ] **Step 2: Decide — do not attempt to close the gap in this plan**

Closing it for real would mean bypassing `fastembed-rs`'s built-in `ImageEmbedding::try_new` preprocessing entirely and hand-rolling a bit-exact resize/crop/normalize pipeline feeding the ONNX session directly — the same shape of work `embedder.rs`'s `UserDefinedRunner` already does for the BGE int8 text path, but for images (a comparably-sized task, not a one-line fix, and outside a crate this plan doesn't own — `fastembed-rs` itself would need a patch or fork to fix `load_preprocessor`'s hardcoded filter for everyone, which is out of scope for a chunkshop port). Given the measured divergence is small in absolute terms (0.265°) and SPEC.md already has direct precedent for exactly this situation — the ASR engine-divergence amendment ("Python CTranslate2 vs Rust whisper-rs... the engine is recorded in provenance") — **this plan amends SC-12 the same way for CLIP vision, rather than chasing bit-parity against an upstream crate's fixed preprocessing choice.**

- [ ] **Step 3: Amend SC-12 in SPEC.md**

In `spec/chunkshop-media-ingest/SPEC.md`, in the SC-12 row (currently: *"Bit-level: same input text + same model → vectors match within float tolerance"*), add a parenthetical scoped to the vision tower specifically — do not weaken the text-tower clause, which the CLIP text tower and `Xenova/bge-base-en-v1.5-int8` both already pass at `1e-6` (verified, roadmap + `40775a2`/`16e1ae7`):

```
| **SC-12** | **Rust parity, two levels.** *Bit-level:* same input text + same model → vectors match within float tolerance (`1e-6` for text-tower embeddings — verified for `Qdrant/clip-ViT-B-32-text` and `Xenova/bge-base-en-v1.5-int8`; the CLIP **vision** tower is measurably looser, `cosine ≥ 0.9999`, due to a verified image-preprocessing divergence between PIL and `fastembed-rs`'s hardcoded resize filter — see MEDIA-INGEST-ROADMAP.md — not an inference difference). *Contract-level:* same YAML, schema, metadata keys and provenance fields for derivation — engines may differ and the engine is recorded in provenance. | Cross-impl test asserts vector tolerance (tiered per model as above) on shared text/images, and schema/metadata-key equality on derived rows; provenance names the engine. |
```

In `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`'s CLIP vision-tower section, add a closing line under "Consequences for the plan": mark decision point 3 (*"A Plan D task should attempt to close the gap... Either way the decision must be explicit"*) as resolved, citing this task and its reasoning (root cause pinned to `load_preprocessor`'s hardcoded `FilterType::CatmullRom`; decision is to amend SC-12 rather than fork/patch `fastembed-rs`).

- [ ] **Step 4: Commit**

```bash
git add spec/chunkshop-media-ingest/SPEC.md docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md
git commit -m "docs(spec): amend SC-12 for CLIP vision tower — cosine>=0.9999, root cause pinned to fastembed-rs's hardcoded resize filter"
```

---

### Task 11: `ClipTagsDeriver` (Rust)

**Files:**
- Create: `rust/chunkshop/src/sources/derivers/clip_tags.rs`

**Interfaces:**
- Consumes: `ImageMetaDeriver` (Task 9, for the metadata half), `stamp_provenance`/`CacheableDeriver` (Task 6/9's `Step 4b` trait).
- Produces: `ClipTagsDeriver { vocabulary, top_k, min_score, vision_model, text_model }` implementing `Deriver` + `CacheableDeriver`.

- [ ] **Step 1: The deriver**

`⚠️ IMPORTANT — verified during planning:` `ClipTagsDeriver` must construct `ImageEmbedding`/`TextEmbedding` **directly** via `fastembed::{ImageEmbedding, ImageInitOptions, ImageEmbeddingModel, TextEmbedding, InitOptions, EmbeddingModel}` — **not** through `FastembedEmbedder`/`resolve_model_name` (`embedder.rs`). That resolution path is specifically for the main `embedder:` YAML stage's text embedder; Python's `clip_tags.py::_ensure_models` likewise imports `ImageEmbedding, TextEmbedding` from `fastembed` directly, bypassing `embedders/_registry.py` entirely. Routing CLIP through `resolve_model_name` would require adding a `ClipVitB32` entry to that table for no reason — it isn't used for the main embedder stage, and doing so risks confusing the two call paths.

Create `rust/chunkshop/src/sources/derivers/clip_tags.rs`:

```rust
//! Rung 1 for images: zero-shot tagging with CLIP, keyless and offline.
//! Mirrors `python/src/chunkshop/sources/derivers/clip_tags.py`. Embeds a
//! label vocabulary with the CLIP TEXT tower, tags each image by nearest
//! label via the CLIP VISION tower. Output is text, landing in the ordinary
//! text plane.
//!
//! Requires the `embedder-hub` Cargo feature (hf-hub auto-download of both
//! CLIP towers) in addition to `media`.

use std::path::Path;

use fastembed::{EmbeddingModel, ImageEmbedding, ImageEmbeddingModel, ImageInitOptions, InitOptions, TextEmbedding};
use serde_json::{json, Map, Value};

use super::image_meta::ImageMetaDeriver;
use super::provenance::stamp_provenance;
use crate::sources::parsers::{CacheableDeriver, Deriver, ParseResult};

const VISION_DEFAULT: &str = "Qdrant/clip-ViT-B-32-vision";
const TEXT_DEFAULT: &str = "Qdrant/clip-ViT-B-32-text";

/// Pinned to match `Cargo.toml`'s `fastembed = { version = "5.13.1", ... }`
/// pin. `Xenova/bge-base-en-v1.5-int8`'s cache-staleness lesson (Global
/// Constraints) applies equally here: bump this const in the SAME commit
/// that bumps the Cargo.toml version pin, or a crate upgrade serves stale
/// cached tags forever. `test_model_version_matches_cargo_toml_pin` below
/// makes a drift here a test failure, not a silent bug.
const FASTEMBED_CRATE_VERSION: &str = "5.13.1";

pub struct ClipTagsDeriver {
    pub vocabulary: Vec<String>,
    pub top_k: usize,
    pub min_score: f32,
    pub vision_model: String,
    pub text_model: String,
}

impl ClipTagsDeriver {
    pub fn new(
        vocabulary: Vec<String>,
        top_k: usize,
        min_score: f32,
        vision_model: String,
        text_model: String,
    ) -> anyhow::Result<Self> {
        if vocabulary.is_empty() {
            anyhow::bail!(
                "ClipTagsDeriver needs a non-empty vocabulary; a generic vocabulary \
                 produces generic tags, so choose it for your corpus."
            );
        }
        Ok(Self { vocabulary, top_k, min_score, vision_model, text_model })
    }

    fn label_vectors(&self) -> anyhow::Result<Vec<Vec<f32>>> {
        // `⚠️ UNVERIFIED:` this plan assumes the CLIP text-tower model id
        // resolves via fastembed-rs's stock `EmbeddingModel::ClipVitB32`
        // (verified: maps to `Qdrant/clip-ViT-B-32-text`, docs.rs +
        // vendored source). If `self.text_model` is ever configured to
        // something OTHER than the default (a user override), this stock
        // enum path cannot honor it — fastembed-rs has no "load an
        // arbitrary registry-name text model by string" API distinct from
        // the enum. Document this as a real config-vs-capability gap:
        // `text_model`/`vision_model` overrides in `ClipTagsDeriverConfig`
        // (Task 8) currently only work if they equal the compiled-in
        // default. A YAML setting them to something else must fail loudly
        // at construction, not silently ignore the override — add that
        // check before shipping this deriver.
        if self.text_model != TEXT_DEFAULT {
            anyhow::bail!(
                "ClipTagsDeriver.text_model override {:?} is not supported by the Rust port yet — \
                 only the default {:?} is wired to a fastembed-rs stock variant",
                self.text_model, TEXT_DEFAULT
            );
        }
        let opts = InitOptions::new(EmbeddingModel::ClipVitB32).with_show_download_progress(false);
        let mut text_model = TextEmbedding::try_new(opts)?;
        let vecs = text_model.embed(self.vocabulary.clone(), None)?;
        Ok(vecs.into_iter().map(l2_normalize).collect())
    }

    fn vision_embed(&self, path: &Path) -> anyhow::Result<Vec<f32>> {
        if self.vision_model != VISION_DEFAULT {
            anyhow::bail!(
                "ClipTagsDeriver.vision_model override {:?} is not supported by the Rust port yet — \
                 only the default {:?} is wired to a fastembed-rs stock variant",
                self.vision_model, VISION_DEFAULT
            );
        }
        let opts = ImageInitOptions::new(ImageEmbeddingModel::ClipVitB32).with_show_download_progress(false);
        let mut vision_model = ImageEmbedding::try_new(opts)?;
        let path_str = path.to_string_lossy().to_string();
        let vecs = vision_model.embed(vec![path_str], None)?;
        Ok(l2_normalize(vecs.into_iter().next().expect("embed of 1 path returns 1 vector")))
    }
}

fn l2_normalize(v: Vec<f32>) -> Vec<f32> {
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm == 0.0 {
        return v;
    }
    v.into_iter().map(|x| x / norm).collect()
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

impl Deriver for ClipTagsDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let meta = ImageMetaDeriver::default();
        let base = meta.derive(path)?;

        let label_vecs = self.label_vectors()?;
        let img_vec = self.vision_embed(path)?;

        let mut scored: Vec<(usize, f32)> = label_vecs
            .iter()
            .enumerate()
            .map(|(i, lv)| (i, dot(lv, &img_vec)))
            .collect();
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());

        let mut tags = Vec::new();
        let mut tag_scores = Vec::new();
        for (i, score) in scored.into_iter().take(self.top_k) {
            if score >= self.min_score {
                tags.push(self.vocabulary[i].clone());
                tag_scores.push((score * 10000.0).round() / 10000.0);
            }
        }

        let mut text_parts = vec![base.text.clone()];
        text_parts.extend(tags.iter().cloned());
        let text = text_parts.join(" ").trim().to_string();

        let mut metadata = base.metadata;
        metadata.insert(
            "clip_tags".to_string(),
            Value::Array(tags.iter().map(|t| Value::String(t.clone())).collect()),
        );
        metadata.insert(
            "clip_scores".to_string(),
            Value::Array(tag_scores.iter().map(|s| json!(s)).collect()),
        );

        let result = ParseResult::with_metadata(text, metadata);
        let (rung, model_id, model_version, params) = self.cache_key_parts();
        Ok(stamp_provenance(result, &rung, &model_id, &model_version, "rust", &params))
    }
}

impl CacheableDeriver for ClipTagsDeriver {
    fn cache_key_parts(&self) -> (String, String, String, Value) {
        (
            "clip_tags".to_string(),
            format!("{}+{}", self.vision_model, self.text_model),
            FASTEMBED_CRATE_VERSION.to_string(),
            json!({"vocabulary": self.vocabulary, "top_k": self.top_k, "min_score": self.min_score}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requires_a_non_empty_vocabulary() {
        let err = ClipTagsDeriver::new(vec![], 3, 0.2, VISION_DEFAULT.to_string(), TEXT_DEFAULT.to_string())
            .unwrap_err();
        assert!(err.to_string().contains("vocabulary"));
    }

    #[test]
    fn model_version_matches_cargo_toml_pin() {
        // Adversarial case per this plan's own rule 3: the const above is
        // hand-maintained (Rust has no clean runtime dependency-version
        // introspection without a build script — see Task 6's timestamp
        // note for the same class of tradeoff). This test is what keeps
        // "bump the pin, forget the const" from being silent.
        let cargo_toml = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml")).unwrap();
        let pin_line = cargo_toml
            .lines()
            .find(|l| l.trim_start().starts_with("fastembed = "))
            .expect("fastembed dependency line must exist in Cargo.toml");
        assert!(
            pin_line.contains(FASTEMBED_CRATE_VERSION),
            "FASTEMBED_CRATE_VERSION={FASTEMBED_CRATE_VERSION:?} does not appear in Cargo.toml's \
             fastembed pin line ({pin_line:?}) — bump the const alongside the pin"
        );
    }

    #[test]
    #[ignore = "downloads ~0.6GB of ONNX weights on first run; run manually with --ignored, not in CI by default"]
    fn tags_are_text_and_correct() {
        let dir = tempfile::tempdir().unwrap();
        let red = dir.path().join("red.png");
        let blue = dir.path().join("blue.png");
        let red_img: image::RgbImage = image::ImageBuffer::from_pixel(224, 224, image::Rgb([220, 30, 30]));
        red_img.save(&red).unwrap();
        let blue_img: image::RgbImage = image::ImageBuffer::from_pixel(224, 224, image::Rgb([30, 30, 220]));
        blue_img.save(&blue).unwrap();

        let vocab = vec![
            "a solid red square".to_string(),
            "a solid blue square".to_string(),
            "a photograph of a cat".to_string(),
        ];
        let d = ClipTagsDeriver::new(vocab, 1, 0.20, VISION_DEFAULT.to_string(), TEXT_DEFAULT.to_string()).unwrap();

        let red_result = d.derive(&red).unwrap();
        let red_tags = red_result.metadata["clip_tags"].as_array().unwrap();
        assert_eq!(red_tags[0], json!("a solid red square"), "if this fails, STOP and investigate — do not weaken the assertion");

        let blue_result = d.derive(&blue).unwrap();
        let blue_tags = blue_result.metadata["clip_tags"].as_array().unwrap();
        assert_eq!(blue_tags[0], json!("a solid blue square"));
    }
}
```

`⚠️ UNVERIFIED:` `TextEmbedding::embed`'s exact bound is `embed<S: AsRef<str> + Send + Sync>(&mut self, texts: impl AsRef<[S]>, batch_size: Option<usize>)` (confirmed via vendored source, `text_embedding/impl.rs:447`) — `self.vocabulary.clone()` (a `Vec<String>`) satisfies `impl AsRef<[String]>` directly. `ImageEmbedding::embed<S: AsRef<Path> + Send + Sync>(&mut self, images: impl AsRef<[S]>, batch_size: Option<usize>)` (confirmed, `image_embedding/impl.rs:206`) — passing `vec![path_str]` (`Vec<String>`) works since `String: AsRef<Path>`. Both confirmed against the actual crate source, not guessed.

- [ ] **Step 2: Run the fast (non-`#[ignore]`) tests**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub --lib sources::derivers::clip_tags -q`
Expected: PASS (2 tests; the network-downloading test is `#[ignore]`d).

- [ ] **Step 3: Run the ignored test once, manually**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub --lib sources::derivers::clip_tags -- --ignored`
Expected: PASS (1 test). First run downloads both CLIP ONNX models (~0.6GB combined, per the Python plan's own note for the same models). If `red.png` doesn't rank `"a solid red square"` first, stop and investigate — do not weaken the assertion (same discipline the Python plan states for its own version of this test).

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/sources/derivers/clip_tags.rs
git commit -m "feat(rust,derivers): rung 1 CLIP zero-shot tagging deriver"
```

---

### Task 12: Wire `ClipTagsDeriver` into `load_deriver`

**Files:**
- Modify: `rust/chunkshop/src/sources/derivers/mod.rs`

**Interfaces:**
- Consumes: `ClipTagsDeriver::new` (Task 11), `ClipTagsDeriverConfig` (Task 8).

- [ ] **Step 1: Replace the `unimplemented!()` arm from Task 9 Step 2**

```rust
pub fn load_deriver(cfg: &DeriverConfig) -> anyhow::Result<Box<dyn crate::sources::parsers::CacheableDeriver>> {
    Ok(match cfg {
        DeriverConfig::ImageMeta(c) => Box::new(image_meta::ImageMetaDeriver {
            path_segments: c.path_segments,
            segment_depth: c.segment_depth as usize,
        }),
        DeriverConfig::ClipTags(c) => Box::new(clip_tags::ClipTagsDeriver::new(
            c.vocabulary.clone(),
            c.top_k,
            c.min_score,
            c.vision_model.clone(),
            c.text_model.clone(),
        )?),
    })
}
```

Note the return type changed from `Box<dyn Deriver>` (Task 9's draft) to `Box<dyn CacheableDeriver>`, and the function is now fallible (`ClipTagsDeriver::new` can error on an empty vocabulary) — update `FilesSource::new` (Task 9 Step 3b) to `?`-propagate this, changing its own signature from `pub fn new(cfg: ...) -> Self` to `pub fn new(cfg: ...) -> anyhow::Result<Self>`, and update every call site (`sources/mod.rs::load_source`, and every test in `media_parser_formats.rs`/`media_derivation_parity.rs` written in Tasks 2–9 that calls `FilesSource::new(cfg)` directly) to handle the `Result`. This is a real, cascading signature change — do it as one deliberate step, not scattered fixups, and re-run the full test suite (Step 2) to catch every call site the compiler flags.

- [ ] **Step 2: Run**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub -q`
Expected: compile errors at every `FilesSource::new(cfg)` call site until each is updated to `FilesSource::new(cfg)?` or `.unwrap()` (tests) or proper `?`-propagation (`load_source`). Fix each, then PASS across the full suite.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/src/sources/derivers/mod.rs rust/chunkshop/src/sources/files.rs \
        rust/chunkshop/src/sources/mod.rs rust/chunkshop/tests/media_parser_formats.rs \
        rust/chunkshop/tests/media_derivation_parity.rs
git commit -m "feat(rust,derivers): wire ClipTagsDeriver into load_deriver; FilesSource::new becomes fallible"
```

---

### Task 13: SC-12 bit-level parity test — CLIP text (tight) + CLIP vision (amended)

Extends the already-proven pattern in `tests/embedding_parity.rs` (full binary reference vector + three-threshold comparison) rather than the JSON-summary shape in `tests/parity/embedding-reference.json` (which stores only `first8`/`sum`/`l2_norm` — insufficient for a real per-component tolerance check on the full 512-dim vector).

**Files:**
- Modify: `tests/parity/generate_embedding_reference.py` (or a new sibling script — decide at implementation time based on whether extending the existing generator vs. adding `tests/parity/generate_clip_full_vectors.py` is cleaner; both dump the same binary layout `embedding_reference_bge_base_int8.bin` already uses)
- Create: `rust/chunkshop/tests/parity-fixtures/clip_text_full_reference.bin`, `rust/chunkshop/tests/parity-fixtures/clip_vision_full_reference.bin`
- Create: `rust/chunkshop/tests/clip_embedding_parity.rs`

**Interfaces:**
- Consumes: `ClipTagsDeriver`'s underlying `TextEmbedding`/`ImageEmbedding` calls (Task 11), the existing `docs/samples/images/sprite_knight.png` fixture.

- [ ] **Step 1: Generate full-vector binary fixtures from Python**

```bash
cd python && uv run --no-sync python3 - <<'EOF'
import struct
from pathlib import Path
from fastembed import TextEmbedding, ImageEmbedding
import numpy as np

FIXTURE_TEXT = "a game sprite or pixel art"
IMG = "../docs/samples/images/sprite_knight.png"

def write_bin(path, vectors):
    n, dim = len(vectors), len(vectors[0])
    with open(path, "wb") as f:
        f.write(struct.pack("<II", n, dim))
        for v in vectors:
            f.write(struct.pack(f"<{dim}f", *[float(x) for x in v]))

text_model = TextEmbedding("Qdrant/clip-ViT-B-32-text")
text_vec = next(iter(text_model.embed([FIXTURE_TEXT])))
write_bin("../rust/chunkshop/tests/parity-fixtures/clip_text_full_reference.bin", [text_vec])

vision_model = ImageEmbedding("Qdrant/clip-ViT-B-32-vision")
vision_vec = next(iter(vision_model.embed([IMG])))
write_bin("../rust/chunkshop/tests/parity-fixtures/clip_vision_full_reference.bin", [vision_vec])
print("wrote clip_text_full_reference.bin and clip_vision_full_reference.bin")
EOF
```

`⚠️ UNVERIFIED:` the binary layout above (`<II` header then `<{dim}f` per vector) is written to MATCH `embedding_reference_bge_base_int8.bin`'s format as read by `tests/embedding_parity.rs::read_reference` (confirmed: `u32` n, `u32` dim, little-endian, then `n*dim` little-endian f32s) — this is a fresh write, not a copy of an existing generator script (no existing script was found that writes this exact `.bin` layout; `embedding_reference_bge_base_int8.bin` predates this plan and its generator wasn't located during research). Confirm byte-for-byte compatibility by running `read_reference`-equivalent code against a freshly generated file before trusting it in Step 3's test.

- [ ] **Step 2: Write the parity test**

Create `rust/chunkshop/tests/clip_embedding_parity.rs`:

```rust
//! SC-12 bit-level parity: CLIP text tower (tight, 1e-6) and CLIP vision
//! tower (amended per Task 10 — cosine >= 0.9999, due to a verified
//! fastembed-rs image-preprocessing divergence, not an inference bug).
//! Reuses `tests/embedding_parity.rs`'s proven three-threshold pattern
//! rather than inventing a new comparison scheme.

use fastembed::{EmbeddingModel, ImageEmbedding, ImageEmbeddingModel, ImageInitOptions, InitOptions, TextEmbedding};

fn fixtures_dir() -> std::path::PathBuf {
    let mut p = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn read_single_vector(name: &str) -> Vec<f32> {
    let bytes = std::fs::read(fixtures_dir().join(name)).unwrap();
    let n = u32::from_le_bytes(bytes[0..4].try_into().unwrap()) as usize;
    let dim = u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
    assert_eq!(n, 1, "these fixtures hold exactly one reference vector");
    (0..dim)
        .map(|i| {
            let off = 8 + i * 4;
            f32::from_le_bytes(bytes[off..off + 4].try_into().unwrap())
        })
        .collect()
}

fn cosine_distance(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let nb: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    1.0 - dot / (na * nb)
}

fn max_abs_diff(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| (x - y).abs()).fold(0.0, f32::max)
}

#[test]
fn clip_text_tower_matches_python_within_1e6() {
    let expected = read_single_vector("clip_text_full_reference.bin");
    let opts = InitOptions::new(EmbeddingModel::ClipVitB32).with_show_download_progress(false);
    let mut model = TextEmbedding::try_new(opts).expect("load CLIP text tower");
    let actual = model
        .embed(vec!["a game sprite or pixel art"], None)
        .unwrap()
        .into_iter()
        .next()
        .unwrap();

    assert_eq!(actual.len(), expected.len(), "dim mismatch");
    assert!(
        max_abs_diff(&actual, &expected) <= 1e-6,
        "max abs diff {} exceeds 1e-6 — text towers are expected to be near-exact (verified: 4e-8 at planning time)",
        max_abs_diff(&actual, &expected)
    );
    assert!(cosine_distance(&actual, &expected) <= 1e-6);
}

#[test]
fn clip_vision_tower_matches_python_within_amended_tolerance() {
    // Amended per Task 10: cosine >= 0.9999 (NOT the tight 1e-6 the text
    // towers pass), due to a verified fastembed-rs preprocessing
    // divergence (hardcoded resize filter), not an inference bug.
    let expected = read_single_vector("clip_vision_full_reference.bin");
    let img_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/samples/images/sprite_knight.png");
    let opts = ImageInitOptions::new(ImageEmbeddingModel::ClipVitB32).with_show_download_progress(false);
    let mut model = ImageEmbedding::try_new(opts).expect("load CLIP vision tower");
    let actual = model.embed(vec![img_path.to_string()], None).unwrap().into_iter().next().unwrap();

    assert_eq!(actual.len(), expected.len(), "dim mismatch");
    let cos_dist = cosine_distance(&actual, &expected);
    let cos_sim = 1.0 - cos_dist;
    assert!(
        cos_sim >= 0.9999,
        "cosine similarity {cos_sim} below the amended 0.9999 floor — if this REGRESSES below the \
         previously-measured 0.9999893, that's a new divergence beyond the known preprocessing gap \
         and needs investigation, not a further tolerance loosening"
    );
}
```

- [ ] **Step 3: Run**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub --test clip_embedding_parity -q`
Expected: PASS (2 tests). Downloads both CLIP models on first run if not already cached from Task 11's manual `--ignored` run.

- [ ] **Step 4: Commit**

```bash
git add tests/parity/generate_embedding_reference.py \
        rust/chunkshop/tests/parity-fixtures/clip_text_full_reference.bin \
        rust/chunkshop/tests/parity-fixtures/clip_vision_full_reference.bin \
        rust/chunkshop/tests/clip_embedding_parity.rs
git commit -m "test(rust): SC-12 bit-level parity for CLIP text (1e-6) and vision (cosine>=0.9999)"
```

---

### Task 14: Contract-level derivation parity — rung 1 + promoted columns

Closes M2 and this plan. Extends Task 9's rung-0 golden-fixture test to rung 1, and proves `content_origin`/`derived_rung` reach real SQL columns via the **already-existing** `promote_metadata` mechanism (verified in Task research: `PromoteColumn`/`promote_metadata` are generic, already in `config.rs` — no new sink code needed).

**Files:**
- Modify: `rust/chunkshop/tests/media_derivation_parity.rs`
- Modify: `rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json` (regenerate to include rung 1)

**Interfaces:**
- Consumes: `ClipTagsDeriver` (Task 11), existing `PromoteColumn` (verified pre-existing).

- [ ] **Step 1: Regenerate the golden fixture with rung 1 included**

```bash
cd python && uv run --no-sync python3 - <<'EOF'
import json
from pathlib import Path
from chunkshop.sources.derivers.image_meta import ImageMetaDeriver
from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

img = Path("../docs/samples/images/sprite_knight.png")

rung0 = ImageMetaDeriver().derive(img)
rung1 = ClipTagsDeriver(vocabulary=["a knight sprite", "a landscape photo", "a chart"]).derive(img)

out = {
    "rung0": {"metadata_keys": sorted(rung0.metadata.keys()), "derived_keys": sorted(rung0.metadata["derived"].keys())},
    "rung1": {"metadata_keys": sorted(rung1.metadata.keys()), "derived_keys": sorted(rung1.metadata["derived"].keys())},
}
Path("../rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json").write_text(json.dumps(out, indent=2))
print(out)
EOF
```

- [ ] **Step 2: Extend the test**

Replace `rung0_metadata_keys_match_python`'s fixture-shape assumptions in `rust/chunkshop/tests/media_derivation_parity.rs` (the fixture is now nested under `"rung0"`/`"rung1"` instead of flat — update the field access accordingly), and add:

```rust
#[test]
fn rung1_metadata_keys_match_python() {
    let fixture: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/parity-fixtures/media_derivation_reference.json").unwrap(),
    )
    .unwrap();
    let expected_keys: HashSet<String> = fixture["rung1"]["metadata_keys"]
        .as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
    let expected_derived_keys: HashSet<String> = fixture["rung1"]["derived_keys"]
        .as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();

    let img_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/samples/images/sprite_knight.png");
    let d = chunkshop::sources::derivers::clip_tags::ClipTagsDeriver::new(
        vec!["a knight sprite".to_string(), "a landscape photo".to_string(), "a chart".to_string()],
        3, 0.20,
        "Qdrant/clip-ViT-B-32-vision".to_string(), "Qdrant/clip-ViT-B-32-text".to_string(),
    ).unwrap();
    let result = d.derive(std::path::Path::new(img_path)).unwrap();

    let actual_keys: HashSet<String> = result.metadata.keys().cloned().collect();
    let actual_derived_keys: HashSet<String> = result.metadata["derived"].as_object().unwrap().keys().cloned().collect();

    assert_eq!(actual_keys, expected_keys, "rung 1 top-level metadata key set must match Python");
    assert_eq!(actual_derived_keys, expected_derived_keys, "rung 1 derived.* key set must match Python");
}

#[test]
fn content_origin_and_derived_rung_are_promotable_columns() {
    // Proves SC-10 (content vs metadata separately addressable) via the
    // ALREADY-EXISTING PromoteColumn mechanism — no new sink code. This
    // test asserts the CONFIG SHAPE compiles and the column-name derivation
    // is what write_document expects, per CLAUDE.md's load-bearing note:
    // "PromoteColumn.column_name is the single source of truth for
    // jsonb-path -> Postgres-ident. Always use pc.column_name."
    use chunkshop::config::PromoteColumn;
    // ⚠️ UNVERIFIED: PromoteColumn's exact constructor/field API (path,
    // type_) was not re-verified in this task — it was cited from
    // CLAUDE.md's prose description, not read from config.rs directly
    // during this plan's research pass. Read `PromoteColumn`'s struct
    // definition and `column_name()` method (config.rs, confirmed present
    // at lines ~104-146) before writing this test's body, and confirm
    // "content_origin" and "derived_rung" (both single-segment, no dots)
    // produce the column names "content_origin" and "derived_rung" via
    // `column_name()` — flat top-level keys should need no dot-to-underscore
    // translation, but verify rather than assume.
    todo!("verify PromoteColumn's real constructor against config.rs before writing this test")
}
```

`⚠️ UNVERIFIED — flagged inline:` the last test is intentionally left as `todo!()`. `PromoteColumn`'s exact API (constructor signature, whether it's built from YAML deserialization only or also has a programmatic constructor usable from a test) needs a direct read of `config.rs` lines ~104-146 (noted present during Task 8's research but not read in full) before this step can be completed with real code — resolve this at implementation time rather than guessing the constructor shape here.

- [ ] **Step 3: Run**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub --test media_derivation_parity -q`

- [ ] **Step 4: Full-suite final regression + commit**

Run: `cd rust/chunkshop && cargo test --features media,embedder-hub -q` (full suite) and `cargo test -q` (base, no media — confirm the default/CI build is unaffected). Expected: 427 (baseline) + all tasks' new tests, zero failures.

```bash
git add rust/chunkshop/tests/media_derivation_parity.rs \
        rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json
git commit -m "test(rust): SC-12 contract-level parity extended to rung 1; promoted-columns coverage for content_origin/derived_rung"
```

Update `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`'s Plan D checklist — check off every completed box (`Rust parser/extension-dispatch layer`, `Deriver equivalent + ParseResult`, `derive/on_empty/cache modelled`, `Provenance + derivation cache`, `CLIP via fastembed-rs`, `SC-12 parity tests`) and move the whole Plan D section to "complete" status, mirroring how Plan A was marked complete at the top of that file.

**M2 complete at this commit. Plan D closed — the release gate in `DECISIONS.md` (2026-09-01) can be re-evaluated.**

---

## Success Criteria coverage for this plan

| SC | Task |
|---|---|
| SC-8 provenance (model id, version, timestamp, params hash) | Task 6 |
| SC-9 no silent overwrite (different model = different cache key) | Task 6 (`derivation_hash` includes model/version/engine) |
| SC-10 content vs metadata separately addressable | Task 2 (content), Task 14 (promoted columns) |
| SC-12 Rust parity, bit-level | Task 10 (decision), Task 13 (test) |
| SC-12 Rust parity, contract-level | Task 9 (rung 0), Task 14 (rung 1) |
| F1-equivalent (Deriver trait, no Rust-side back-compat burden — Rust has no pre-1.0 third-party parsers to preserve) | Task 1 |
| F2 image parser rung 0 | Task 9 |
| F17 Rust parity (`image-models` feature) | Tasks 1, 11, 13 |

**Not in this plan's scope** (see `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`): Plan B's OCR/VLM/ASR rungs, `files`-source `IncrementalSource` (separate roadmap item, deliberately still refused by `deny_unknown_fields`), PPTX (deferred — Task 2, follow-up bakeoff tracked in the roadmap), the systemic `dsn`/`fts` config-permissiveness audit (already landed separately per merge `1a05884`, unrelated to media).

## Notes for the executor

- Work in the existing worktree: `/home/yonk/yonk-tools/chunkshop-rust-media`, branch `feat/rust-media-parity`. Do not create a new worktree.
- `cargo test --features media,embedder-hub -q` is the full-suite command. Base `cargo test -q` (no features) must keep passing throughout — it's the "does adding this plan touch anything outside `media`" guard.
- Every `⚠️ UNVERIFIED` marker in this plan names a specific thing to check before trusting the code around it — treat these as required pre-flight checks, not optional nice-to-haves. There are more of them in this plan than in the Python plan it ports, deliberately: Rust crate APIs for `docx-rust`, `calamine`'s full `Data` enum, `PromoteColumn`'s constructor, and the `$HOME`/timestamp/directory-walk helpers in Tasks 6-7 were not all confirmed to the same depth as the core `fastembed-rs` surface (which WAS read from vendored source in full). Resolve each before shipping the task it's attached to.
- **`Rc` vs `Send`:** Task 9 Step 3b chooses `Rc<dyn CacheableDeriver>` for the per-extension deriver table. If a later refactor makes `FilesSource` need to cross a thread boundary (unlikely given the orchestrator's subprocess-isolation model per `CLAUDE.md`'s "Thread discipline" note, but worth re-checking if this assumption changes), swap to `Arc` — a one-line type change, not a redesign.
- **PPTX bakeoff is a follow-up, not part of this plan.** If the team decides to close that gap later, it's a small standalone task: fixture-test `pptx-to-md`, `undoc`, and `office_oxide`'s PPTX path against 2-3 real `.pptx` files, pick a winner, add it to the `media` feature the same way Tasks 3-5 did for pdf/xlsx/docx.
- If an existing test breaks because of Task 8's `on_empty` default, fix **that test** explicitly (pass `on_empty: "index"` or handle the new error) and say so in the commit body — do not weaken the default, matching the discipline the Python plan (`2026-09-01-media-ingest-v1a-seam-spine-images.md`) already established for the same situation.
