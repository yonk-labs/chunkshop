# DECISIONS

Locked decisions for chunkshop. Append with the date; do not silently change a
value recorded here. If a change would contradict one of these, stop and flag it.

---

## 2026-09-01 — RELEASE GATE: no new version until Rust media parity (Plan D) ships

**Decision.** No new chunkshop version is released until **Plan D — Rust parity
for media ingest** is complete.

**Why.** The v1-A branch adds media ingest to the Python implementation only:
a `Deriver` seam, a provenance-keyed derivation cache, and two keyless image
rungs (Pillow metadata, CLIP zero-shot tagging). chunkshop's standing promise is
that the same YAML drives both implementations and that vectors from either are
interchangeable. Shipping media in Python alone breaks that promise for anyone
on the Rust port.

**Scope of Plan D** (what "done" means for this gate):
- A Rust parser/deriver layer. **Note this is a bigger job than it looks:**
  Rust has *no* parser layer at all today — `sources/files.rs:36` does
  `std::fs::read_to_string` on every match, with no extension dispatch. The
  SP-3 parser layer (pdf/docx/pptx/xlsx/html) was never ported either, so Plan D
  must land that foundation before any media rung.
- `derive` / `on_empty` / `cache` modelled in `FilesSourceConfig`.
- CLIP image embedding via `fastembed-rs` — the `image-models` feature is
  **already enabled** at `rust/chunkshop/Cargo.toml:76`, so the dependency work
  is done.
- SC-12 parity as the spec defines it: **bit-level** for embedding (same input
  text + model → vectors within float tolerance) and **contract-level** for
  derivation (same YAML, schema, metadata keys and provenance fields; the
  engine is recorded in provenance, so a Rust-derived value is attributable
  rather than silently different).

**Interim mitigation, shipping on this branch (change "A").** `deny_unknown_fields`
on Rust's `FilesSourceConfig`, so a media YAML fed to the Rust binary fails
loudly naming the field it cannot honour, instead of silently dropping the
`derive` block and then dying on a PNG's invalid UTF-8. This does not satisfy
the gate; it makes the gap honest while the gate is open.

**Related pre-existing divergence surfaced by the same check.** Rust implements
`IncrementalSource` for `s3`, `pg_table` and `http` but **not** for `files`, and
`FilesSourceConfig` models no cursor field — so a `files` + `incremental:` YAML
has been silently running full-resync on Rust since v0.8.3. Change A makes that
loud too. Worth its own follow-up.
