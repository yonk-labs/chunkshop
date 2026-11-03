# Plan: more doc types for `chunkshop-parsers`

## TL;DR
Add HTML (Python parity gap), ODF (odt/odp/ods) and legacy Excel (xls/xlsb) with **zero new crates** (reuse calamine, zip, quick-xml already approved). Defer formats that need a new dependency (rtf, epub-via-html-parser, doc/ppt legacy, eml/msg) until the user approves a crate (ASK FIRST).

## Scope (tiered)
| Tier | Format | How | New dep | Reference for tests |
|---|---|---|---|---|
| 1 | html/htm | ~80-line tokenizer, mirrors bs4 `get_text("\n", strip=True)` minus script/style/comments | none | Python `HTMLParser` (bs4) → exact |
| 1 | xls, xlsb, ods | calamine `Xls`/`Xlsb`/`Ods`, same row/cell rendering as xlsx | none | ods hand-built fixture; xls/xlsb only garbage→Skip (no writer available locally) |
| 1 | odt, odp | zip + quick-xml over `content.xml`, `text:p`/`text:h`, `text:s`/`tab`/`line-break` | none | hand-built fixtures, expected text asserted by hand (no Python parser exists) |
| 2 (needs approval) | epub | zip + HTML extractor over spine order | none, but depends on html feature | defer |
| 2 | rtf | hand-rolled or `rtf-parser` crate | maybe | defer |
| 3 | doc/ppt/eml/msg | no mature pure-Rust crate verified | yes | out of scope |

## Rules carried over
- Local-only; no new crates this round (verification gate unchanged). Corrupt → `Skip` with warning; feature off → `FeatureDisabled`.
- Features: `html`, `odf`; `xlsx` now also gates xls/xlsb/ods; `all-parsers` includes both.
- Zip-bomb cap reused for ODF.

## Slices
1. html + tests (bs4 reference via `scripts/produce_rust_parser_reference.py`)
2. calamine formats (ods fixture, xls/xlsb Skip)
3. odt/odp + fixtures
4. README/CHANGELOG, full-feature + per-feature builds, network-denied test run

## Success
SC-1 every new format has a passing test; SC-2 each feature builds alone; SC-3 whole suite passes under `sandbox-exec` network deny; SC-4 `cargo tree` shows no new crates.

## Update: legacy DOC/PPT
`cfb 0.16` was approved and gated (MIT, source grep clean), but rejected Apple's real `.doc` (duplicate MiniFAT pointers), so `src/legacy.rs` has its own bounded, lenient OLE reader instead; `cfb` is now a dev-dependency only (builds containers in tests). MS-DOC piece-table and MS-PPT extractors on top. Tests: real Word-97 file (doc), synthetic records (ppt). Not done: encrypted doc/ppt (Skip), PPT notes/masters (skipped by design), validation on a real PowerPoint file.

## Update: security review follow-ups (two independent reviews)
Fixed: PPT recursion cap, DOC piece-table/field handling, ODF `text:c` clamp, PPTX slide dedupe, XLSX padding cap, linear HTML scanners, whole-parse `catch_unwind`, NUL stripping, regular-file/size checks. Added: full bs4 HTML entity table, CDATA and quote rules, Python float formatting, free PPT text boxes, and opt-in `isolate` (worker process with timeout, output cap, allocator-enforced memory ceiling). Open: `cargo audit` not run (install was blocked in the agent session); XLS/XLSB/PPT unvalidated on real files; PPT free text not interleaved per slide.
