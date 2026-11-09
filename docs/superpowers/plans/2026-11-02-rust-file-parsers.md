# Plan: Rust file parsers (PDF/DOCX/PPTX/XLSX/HTML) at parity with Python SP-3

Status: DRAFT, awaiting user decisions Q1-Q7 and crate approval (slice 0).
Authored by an Opus Plan subagent; repo facts marked [V] were checked by that agent, [U] were not. Not independently re-verified by the main session.
Research: `skill-output/research-and-design/Research-Report-rust-file-parsers.md` (gitignored).

**Hard constraint (user):** every crate added must be verified to run locally and require no external services. Reading over the network (S3, Drive, HTTP) is fine; sending document data out, hosted OCR/parse APIs, telemetry, or external subprocesses are not. No OCR in scope.

## REVISION 2026-11-02 (user decision): add-on crate, D6 relaxed

**Q1 resolved:** D6 is relaxed for parsers only as an **opt-in add-on crate**, not core. Core `chunkshop-rs` is untouched; no parser deps, features, or `full` changes. This also resolves Q2 (nothing goes in `full`), Q4 (no trait), and makes sections 1/3 below apply to the add-on as follows:
- New workspace member `rust/chunkshop-parsers` (`chunkshop-parsers`, `publish = false` until slice 0 passes). Depends on core as a library (`package = "chunkshop-rs"`, `default-features = false, features = ["source"]`).
- Seam is core's existing public API: the add-on yields `Document`s via `ParsedFilesSource::iter_documents()`; callers feed `Pipeline::ingest_document`. Core has no plugin seam (closed `AnySource`/`SourceConfig`), so there is no YAML `type:` for it and no CLI integration; adding one would need a core edit (new question Q8, skipped by default).
- `files.rs`/`http.rs` edits in slices 1-2 are dropped: HTML would need `http::strip_html_to_text` made public in core (Q9), so HTML is deferred. Slice 1 is DONE in the add-on (text path, NUL skip, lossy UTF-8, `metadata.parser`, 4 tests). Pdf/docx/pptx/xlsx currently skip with "no parser for .X yet" so binary is never ingested as text.
- Parser features (`pdf`, `docx`, `pptx`, `xlsx`) live on the add-on crate, one slice each, each after the local-only verification gate (slice 0).
- Remaining docs: add-on README + CHANGELOG + README:209 note (still stale: core remains Python-only for built-in parsing, Rust add-on exists).

## SLICE 0 RESULTS (2026-11-02, macOS Darwin 25.6, spike in scratchpad, not in repo)

Candidate set: `pdf-extract 0.12.1` (lopdf 0.42.0), `calamine 0.38.0` (default-features off), `zip 9.2.2` (deflate only), `quick-xml 0.42.0`. 91 locked packages total, release binary 2.6 MB.

| Gate | Result |
|---|---|
| Dependency tree | No reqwest/hyper/ureq/curl/h2/tungstenite/TLS/tokio/mio. PASS |
| Source grep (network, process, env, telemetry, build.rs) | Only hits: calamine "hyperlinks" (substring false positive); zip `Command::new("icacls")` is inside a `#[cfg(windows)] #[test]`; `nix` build.rs/env (libc wrapper, no network). No telemetry/API keys. PASS |
| Licence | pdf-extract, lopdf, calamine, zip, quick-xml, nix all MIT. PASS |
| Network-blocked run | `sandbox-exec (deny network*)`: output hash identical to networked run. PASS |
| Determinism | 5 runs, 1 distinct hash. PASS |
| Extraction (existing Python fixtures) | docx "hello from docx", pptx "deck title", xlsx "cell value\n", pdf "\n\nhello from pdf". PASS (loose PDF parity: leading newlines) |
| Malformed | truncated pdf/docx, encrypted pdf (clean `Err`), blank pdf returns `""` (must map to Skip). No panic, no hang. PASS |
| Zip bomb | 600 KB zip expanding to 600 MB: `.take(256 MiB)` bounds memory, but returns truncated text as `Ok`. FINDING: parser must treat hitting the cap as `Skip`, not success |
| quick-xml >= 0.38 | 0.42.0. PASS. NOTE: 0.42 API differs from older versions (`xml10_content`, `local_name().as_ref()` is `str`) |
| `cargo audit` | NOT RUN: cargo-audit not installed locally (would need a network install of a dev tool). Open |
| Not yet done | Real-PDF corpus quality/panics, full-parity fixtures (slice 3), build-time/weight delta inside the real workspace, by-page PDF extraction, pdf_oxide comparison |

DC-001 check: no candidate needs network, subprocess, or native library; quick-xml is >= 0.38. Gate passes for these four crates, pending `cargo audit` and the real-PDF corpus.

## 0. Findings that change the brief

1. **Project already decided "Python-only".** `README.md:209` says file parsers are "Python-only by design (spec D6)"; D6 is in `docs/superpowers/specs/2026-05-25-chunkshop-connector-plugin-foundation-design.md:28` ("Re-evaluate after real usage"). This work reverses it; needs explicit sign-off (Q1) and a README:209 change. [V]
2. **Python emits no page/slide/sheet markers.** Python metadata is `{"source_path", "parser": "<ClassName>"}` (`python/src/chunkshop/sources/files.py:127-132`). Parity = add `parser` with the same class names (`TextParser`, `PDFParser`, `DOCXParser`, `PPTXParser`, `XLSXParser`, `HTMLParser`). Markers are out of scope (the research report's "should have" was a misread). [V]
3. **Python text-join semantics (exact parity targets).** [V]
   - PDF: pages joined with `\n`.
   - DOCX: top-level body paragraphs only (no tables/headers/footers); `w:tab`→`\t`, `w:br`/`w:cr`→`\n`, hyperlink text included.
   - PPTX: slide order from `presentation.xml`, not file names; only text-frame shapes (groups/tables skipped); paragraphs joined `\n`, `a:br`→`\v`.
   - XLSX: all sheets in workbook order, no header; cells `\t`, rows `\n`; Python `str()` formatting (`1.0`, `True`, `2024-01-01 00:00:00`); openpyxl read-only pads rows from row 1.
   - HTML: bs4 `get_text("\n", strip=True)`, script/style removed.
4. **Error handling differs.** Python: `ParserError` (corrupt/encrypted) fails the whole cell (`runner.py:267`); scanned PDF returns `""`; missing extra raises `RuntimeError` with install hint. Requested Rust behavior (loud skip with reason) is a deliberate divergence (Q6).
5. **Fingerprints unaffected.** Python sets `sha256(raw bytes)` only on the incremental path; Rust `FilesSource` has no `IncrementalSource` and sets `fingerprint: None`. [V]
6. **S3/HTTP.** Python `s3.py` does not parse (UTF-8 `errors="replace"`); Rust `s3.rs:96` uses `from_utf8_lossy`, so a PDF in S3 is ingested as junk in both today. Rust `http.rs:311-317` already skips binary MIME types. `http.rs:526 strip_html_to_text` is a regex stripper reusable for `.html` (no entity decoding). [V]
7. **Security waiver goes stale.** `quick-xml 0.37.5` is in `rust/Cargo.lock` under waivers RUSTSEC-2026-0194/0195, justified as "reachable ONLY via object_store". Parsing untrusted OOXML makes that false: new parsing paths must use quick-xml >=0.38. calamine's quick-xml version is [U] and a blocking check in slice 0. [V]
8. **CI runs no Rust tests** (`.github/workflows/ci.yml`; only `dependency-audit.yml` runs `cargo audit`). "Tests pass between slices" is a local gate unless CI is added (Q7). [V]
9. **Existing fixtures too small** (`python/tests/fixtures/parsers/sample.*` hold one string each); local venv pypdf 6.14.2 vs lockfile 6.16.2, so generate references after a locked `uv sync`. [V]
10. **Rust `FilesSource` has no tests**; the new test file is its first. [V]

## 1. Architecture

**A dispatch function, not a trait (yet).** New module `rust/chunkshop/src/sources/parsers/`, gated by the `source` feature:

```rust
pub struct Parsed { pub text: String, pub parser: &'static str }

#[derive(Debug, thiserror::Error)]
pub enum ParseError {
    /// Format recognized, cargo feature off. HARD error (fails the cell).
    #[error("`.{ext}` parsing requires the `{feature}` cargo feature (rebuild with --features {feature})")]
    FeatureDisabled { ext: String, feature: &'static str },
    /// Corrupt, encrypted, no text layer, binary on text path. Caller warns + skips. Never falls back to text.
    #[error("{0}")]
    Skip(String),
}

pub fn parse(ext: &str, bytes: &[u8]) -> Result<Parsed, ParseError>
```

- **Dispatch** on lowercase extension (as Python): `txt|md|markdown|rst|log|csv|tsv|""|unknown` → text; `html|htm` → HTML (always on, reuses `http::strip_html_to_text` as `pub(crate)`); `pdf|docx|pptx|xlsx` → one `#[cfg(feature)]` arm each plus a `#[cfg(not)]` arm returning `FeatureDisabled`.
- **No magic-byte dispatch.** Text path only defends with git's heuristic: NUL in first 8 KiB → `Skip("binary content; no parser for .{ext}")` (`// ponytail:` UTF-16 skipped; add encoding_rs sniffing if needed).
- **Text decoding:** `String::from_utf8_lossy` (Python `errors="replace"`); `cfg.encoding` stays ignored.
- **`files.rs` hook** (~25 lines replacing lines 33-49): `std::fs::read` → `parsers::parse` → `Skip`: `tracing::warn!(target:"chunkshop::files", ...)` + continue; `FeatureDisabled`: return error; add `"parser"` to metadata. `fingerprint` stays `None`. All files skipped → empty Vec + warning; "no files matched glob" error only when glob matches nothing.
- **S3** unchanged in v1 (optional slice 9, Q3). **HTTP** unchanged.

**Robustness (do not simplify away):**
- PDF: `catch_unwind(AssertUnwindSafe(..))` → `Skip` (no `panic = "abort"` profile [V]). Encrypted → `Skip("encrypted PDF")`; empty text with >=1 page → `Skip("no extractable text layer (scanned/image-only?); OCR is out of scope")`.
- OOXML: cap each zip entry read with `.take(MAX_PART_BYTES)` (~256 MiB, `ponytail:` comment naming the ceiling); bad/missing parts → `Skip`.
- Hangs are not covered by `catch_unwind` (known ceiling; upgrade path: parse thread with timeout).

**Cargo features (mirror Python extras):**
```toml
pdf  = ["source", "dep:pdf-extract"]          # per slice 0
docx = ["source", "dep:zip", "dep:quick-xml"]
pptx = ["source", "dep:zip", "dep:quick-xml"]
xlsx = ["source", "dep:calamine"]
office = ["pdf", "docx", "pptx", "xlsx"]
all-parsers = ["office"]
full = [ ...existing..., "all-parsers" ]      # Q2
```
No `html` feature (no dep). `zip` with `default-features=false, features=["deflate"]`; `quick-xml >= 0.38`; `calamine`/PDF crate `default-features=false` unless the spike needs one. Slice 0 measures dependency count, clean build time, binary size.

## 2. Crate selection (needs user approval: new runtime deps)

**Verification gate, per crate+version, BEFORE `Cargo.toml`** (throwaway spike crate in scratchpad or gitignored `skill-output/spikes/`, never in `rust/chunkshop`):
1. `cargo tree -e normal,build --prefix none | sort -u` diffed vs baseline `--features source`; flag reqwest/hyper/ureq/curl/isahc/surf/attohttpc/h2/tungstenite/native-tls/openssl/rustls/tokio/mio; any hit explained as input-only or rejected.
2. Grep `~/.cargo/registry/src/*/<crate>-<ver>` and new transitive crates for `Command::new|std::process|TcpStream|UdpSocket|reqwest|ureq|hyper|api[_-]?key|env::var|telemetry|sentry|posthog|https?://`; check `build.rs` for downloads/`links=`; `println!` stdout pollution. One-line disposition per hit.
3. Build with network, run with network denied (macOS `sandbox-exec -p '(version 1)(allow default)(deny network*)'` [U on Darwin 25]; Linux `unshare -rn` / `docker run --network none`); sha256 per fixture output identical to networked run.
4. Determinism: 5 runs across 2 processes, identical hashes.
5. Licence via `cargo tree --format "{p} {l}"`: MIT/Apache-2.0/BSD/Zlib/ISC ok; GPL/AGPL rejected.
6. `cargo audit` clean with no new waivers; every parsing-path quick-xml >=0.38.
7. Malformed input: truncated zip, truncated PDF, encrypted PDF, image-only PDF, zip bomb → `Skip`, no escaped panic, no hang >30 s.
8. Record crate, version, features, date, host, results and build-weight deltas in this doc.

| Format | Recommended | Fallback | Rejected (why) | Parity target |
|---|---|---|---|---|
| PDF | **pdf-extract** (pure Rust on lopdf). Prove by-page extraction [U], licence [U], stdout prints [U] | **pdf_oxide** default-features off (ML/OCR off) [U]; fallback 2 `lopdf::Document::extract_text` | pdfium-render (native lib); poppler (subprocess); anydoc (hosted-OCR uploads docs, heavy); LlamaParse (network); parser-core (OCR) | **Loose**: equal after whitespace collapse on fixtures; real-PDF corpus reports token Jaccard, not gated |
| XLSX | **calamine** (MIT) | hand-rolled zip+quick-xml (~100 lines more) | umya-spreadsheet (heavy) | **Exact** on fixture via shim formatting like Python `str()` and openpyxl padding |
| DOCX | **zip + quick-xml >=0.38** (we control semantics) | anytomd with `reqwest` off | anytomd/undoc/dm2xcod as main pick (Markdown output; exact parity impossible) | **Exact** |
| PPTX | **zip + quick-xml** (shared helper) | anytomd (same caveat) | same | **Exact**, incl. `\v` and presentation.xml slide order |
| HTML | **no crate**, reuse `http::strip_html_to_text` | `scraper`/html5ever if exact bs4 parity required | — | **Loose** (entity decoding differs; slice 10) |

**Decision point:** approve three new direct dependencies (one PDF crate, `calamine`, `zip`+`quick-xml`), each conditional on passing the gate.

## 3. Slices

Each slice: one change, tree compiles, `cargo test --features <slice feature>` and `cargo test --no-default-features --features source` pass.

| # | Slice | Files | Tests | Acceptance | Size |
|---|---|---|---|---|---|
| 0 | **Verification spike** (no repo code) | throwaway crate; results into this doc | gate steps 1-8 per candidate on slice-3 fixtures + 3-5 real local PDFs | results table recorded; user approves crates; build-weight numbers | 0 repo lines |
| 1 | Parser seam + text path, no new deps | new `src/sources/parsers/mod.rs`; `src/sources/mod.rs` (+1 `pub mod`); `src/sources/files.rs` | new `tests/files_parsers.rs`: .txt unchanged + `parser=="TextParser"`; invalid UTF-8 lossy; NUL `.bin` skipped w/ warning (`#[traced_test]`); unknown ext as text | existing tests green; `parity.rs`/`sink_modes_parity.rs` unaffected | ~70 src + 60 test |
| 2 | HTML via existing stripper | `http.rs` (`pub(crate)`), `parsers/mod.rs` | `.html/.htm` → `HTMLParser`, no tags, script/style removed | — | ~10 + 25 |
| 3 | Shared fixtures + Python reference | new `scripts/produce_rust_parser_reference.py`; `rust/chunkshop/tests/parity-fixtures/parsers/*` | script uses Python parsers under locked `uv sync --extra all-parsers` | `parsers_reference.json`: `{parser,text,input_sha256}` + lib versions; `cargo package --list` excludes fixtures [U] | script ~90; binary fixtures |
| 4 | XLSX | `Cargo.toml`, `parsers/xlsx.rs`, mod.rs arm | `#[cfg(feature="xlsx")]` exact equality; corrupt → Skip; feature off → FeatureDisabled | network-blocked test run passes | ~60 + 30 |
| 5 | DOCX + shared zip helper | `Cargo.toml`, `parsers/ooxml.rs` | exact: tabs/breaks/hyperlinks; tables excluded; truncated zip + zip bomb → Skip | same | ~90 + 30 |
| 6 | PPTX | `Cargo.toml`, `parsers/ooxml.rs` | exact: order from rels; `\v`; group/table skipped | same | ~80 + 25 |
| 7 | PDF | `Cargo.toml`, `parsers/pdf.rs` | whitespace-collapsed equality on 2-page fixture; encrypted/blank/truncated → Skip, no panic | same + hashes match networked vs blocked | ~60 + 35 |
| 8 | Umbrella features + `full` (Q2) | `Cargo.toml` | `cargo build --bin chunkshop-rs` parses all four fixtures end to end with SQLite sink (pattern `tests/sqlite_sink_*.rs` [U]) | binary-size delta measured | ~10 + 40 |
| 9 | *(optional, Q3)* S3 dispatch | `src/sources/s3.rs` | extend `tests/s3_source.rs` (MinIO-gated) with `.docx` | fingerprint still ETag | ~25 + 30 |
| 10 | *(optional, Q5)* entity decoding | `http.rs` | `&amp; &lt; &#39; &nbsp;` numeric | fixes HTTP source too | ~20 + 15 |
| 11 | Docs | section 5 | — | — | docs only |

Slices 4-7 depend only on 1 and 3; XLSX first (smallest single-crate adapter).

## 4. Tests and fixtures

- One fixture set, two languages, generated by python-docx/python-pptx/openpyxl (already in `all-parsers`): DOCX (heading, tab/break/hyperlink paragraph, excluded table, header/footer); PPTX (3 slides with file names not matching order, soft break, group shape, table); XLSX (2 sheets; int/float/bool/date/empty; leading empty row+col); PDF (2-page raw-bytes Helvetica `Tj`, no reportlab [V not installed]; encrypted copy via `pypdf.PdfWriter.encrypt`; blank "scanned" via pypdf; truncated); corrupt truncated docx.
- Reference JSON stores `input_sha256` and Python lib versions (follows `scripts/produce_rust_*_reference.py` → `tests/parity-fixtures/*_reference.json` [V]).
- Parity: DOCX/PPTX/XLSX byte-exact; PDF/HTML equal after `split_whitespace().join(" ")`. Real PDFs scored in spike only, never committed.
- Offline proof: `cargo test --features all-parsers --test files_parsers --no-run`, run the binary under network deny with `-- --nocapture`; record the command here.
- Python tests unchanged.

## 5. Docs to update

`README.md:209`; `rust/README.md:78`; `rust/chunkshop/README.md` (parser feature matrix + binary-size deltas); `docs/cookbook/file-parsing.md` (Rust section, no-network guarantee + verified-crate table); `docs/AGENT_REFERENCE.md:76-78, 896-903` (FeatureDisabled message); `CHANGELOG.md` Unreleased (new features; `metadata.parser` added to Rust files — visible jsonb change; invalid UTF-8 now lossy; NUL files skipped; corrupt/encrypted skipped in Rust vs fail-cell in Python); `dependency-audit.yml` no change if quick-xml >=0.38 holds, else stop and escalate, do not widen the waiver.

## 6. Risks, open questions, out of scope

**Risks:** pdf-extract quality/panics/hangs; calamine quick-xml may sit in waived range; XLSX date/float formatting deltas; no Rust CI so post-merge drift; parsers in `full` raise build time for all consumers.

**Open questions for the user:**
- **Q1:** Override spec D6 ("Python-only by design") for file parsers?
- **Q2:** Put `all-parsers` in `full`/default? (recommended yes)
- **Q3:** Also parse S3 objects (slice 9)? Puts Rust ahead of Python.
- **Q4:** Public `FileParser` trait + custom injection (`FilesSource::with_parsers`) for Python API parity? Skipped by default.
- **Q5:** Fix HTML entity decoding in the shared stripper (slice 10)?
- **Q6:** Accept "skip with warning" vs Python's "fail the cell" for corrupt/encrypted as a recorded divergence, or change Python?
- **Q7:** Add a minimal `cargo test` CI job?

**Out of scope:** OCR; page/slide/sheet markers; `.doc/.rtf/.epub/.xls/.odt`; `cfg.encoding`; Rust files incremental/prune; magic-byte sniffing beyond NUL; HTTP binary-skip changes; Python-side changes; writing a PDF parser.

## 7. Success criteria and drift checkpoints

- **SC-001:** every new crate has a recorded verification row before its `Cargo.toml` line lands.
- **SC-002:** `FilesSource` never emits binary as text; unparseable/NUL files produce a `tracing::warn!` skip naming file and reason, no `Document`.
- **SC-003:** DOCX/PPTX/XLSX output equals Python reference byte for byte; PDF/HTML equal after whitespace collapse.
- **SC-004:** encrypted and image-only PDFs skip with distinct reasons; truncated PDF/zip never panics out of `parse`.
- **SC-005:** recognized format with feature off fails the cell with a `--features <x>` hint.
- **SC-006:** `metadata.parser` uses Python class-name strings for all six parsers.
- **SC-007:** parser test binary passes with outbound network denied; PDF output hashes match networked run.
- **SC-008:** `cargo audit` clean, no new waivers; `--no-default-features --features source` pulls no parser crate.
- **SC-009:** docs, CHANGELOG, README:209 updated; binary-size table measured.

- **DC-001** (after slice 0): stop if any candidate needs network, subprocess, native library, or pulls quick-xml <0.38 on a parsing path; do not swap crates without user.
- **DC-002** (every slice): diff ~100 lines or less, one thing, compiles, tests green with and without the slice feature.
- **DC-003:** no markers, OCR, trait/registry, or S3 parsing without Q3/Q4 approval.
- **DC-004:** never change a parity reference to make Rust pass; only on Python lib bump.
- **DC-005:** no `http.rs` behavior change beyond `pub(crate)` (slice 10 excepted).

## Critical files

`rust/chunkshop/src/sources/files.rs`, `rust/chunkshop/Cargo.toml`, `rust/chunkshop/src/sources/http.rs` (`strip_html_to_text`, line 526), `python/src/chunkshop/sources/parsers/`, `python/src/chunkshop/sources/files.py`, `rust/chunkshop/src/sources/s3.rs` (slice 9), `.github/workflows/dependency-audit.yml`.
New: `rust/chunkshop/src/sources/parsers/{mod,xlsx,ooxml,pdf}.rs`, `rust/chunkshop/tests/files_parsers.rs`, `rust/chunkshop/tests/parity-fixtures/parsers/`, `scripts/produce_rust_parser_reference.py`.
