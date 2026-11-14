# chunkshop-parsers

Opt-in add-on for `chunkshop-rs`: parse PDF / DOCX / PPTX / XLSX / HTML / ODT / ODP / ODS / XLS / XLSB / legacy DOC / PPT into core `Document`s.
Core stays parser-free. Local-only: no network, no external services.

```toml
chunkshop-parsers = { path = "...", features = ["all-parsers"] }
# or pick: pdf, docx, pptx, xlsx (+xls, xlsb, ods), odf (odt, odp), html, doc, ppt
# add `isolate` to parse hostile/untrusted files in a sandboxed worker process (below)
```

```rust
let docs = chunkshop_parsers::ParsedFilesSource::new(files_cfg).iter_documents()?;
for d in &docs { pipeline.ingest_document(d).await?; }
```

## Behavior
- Text and unknown extensions are decoded lossily; files with NUL bytes in the first 8 KiB are skipped. NULs anywhere else are stripped (Postgres TEXT rejects them).
- A recognized format whose feature is off is a hard error with a `--features` hint.
- Corrupt, encrypted, or image-only PDFs/DOC/PPT are skipped with a warning (no OCR). Non-regular files and files over 256 MiB are skipped.
- A panic inside any parser becomes a Skip.
- HTML mirrors Python's bs4 `get_text` (same entity table, CDATA, quoting rules). Spreadsheet numbers use Python `str(float)` formatting; dates still render as the Excel serial.
- Legacy `.doc`/`.ppt` use a built-in, lenient OLE reader (no OLE crate); tables in `.doc` flatten to tab-separated rows. `.ppt` reads slide outline text plus free text boxes (appended after the outline text, not interleaved per slide).

## Untrusted input: `isolate`
**Parsing untrusted files in-process is not safe, even with the caps below.** A hostile file can abort your whole host process (out-of-memory or stack overflow; a Rust allocation failure cannot be caught, and `catch_unwind` only handles panics). `isolate` is the supported way to parse untrusted files. It is off by default: enable the `isolate` feature and call `ParsedFilesSource::with_isolation(...)` (example below). Without it, only parse files you trust.

In-process, hostile files are bounded (per-part and aggregate size caps, a 128 MiB cap on total extracted text, a dense-range cap for XLSX/XLSB, depth caps, clamped allocations, linear-time scanners; see `tests/hostile.rs`), but a PDF or spreadsheet crate can still overflow the stack, hang or exhaust memory, which `catch_unwind` cannot catch. With the `isolate` feature, binary formats parse in `chunkshop-parsers-worker`:

```rust
let iso = chunkshop_parsers::Isolation::locate().expect("worker next to the binary"); // or Isolation::new(path)
let src = ParsedFilesSource::new(cfg).with_isolation(iso); // 120 s timeout, 1 GiB heap ceiling, 256 MiB output cap
```
Build the worker with the same features: `cargo build --features all-parsers,isolate`. A crash, timeout or memory-ceiling hit becomes a Skip. The ceiling is enforced by a counting allocator inside the worker (no libc). The worker only spawns itself; no network.

## Tests and parity
- DOCX/PPTX/XLSX byte-exact vs Python, HTML exact, PDF whitespace-insensitive. Regenerate references with `scripts/produce_rust_parser_reference.py`; the HTML entity table with `scripts/gen_html_entities.py`.
- Real files: `.doc` (macOS `textutil`, `scripts/make_doc_fixture.py`). Hand-built/synthetic only: ODT/ODP/ODS fixtures, `.ppt` records. XLS/XLSB: corrupt-input Skip only (no local writer).
- Plans and verification record: `docs/superpowers/plans/2026-11-02-rust-file-parsers.md`, `2026-11-02-rust-more-doc-types.md`.
