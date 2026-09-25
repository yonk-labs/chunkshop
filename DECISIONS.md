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
  **already enabled** at `rust/chunkshop/Cargo.toml:104`, so the dependency work
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

---

## 2026-09-01 — `ross` is an added rung, not a replacement for Plan D's derivers

**Decision.** `../ross` (yonk-labs/ross, Apache-2.0, v0.1.0) becomes an **opt-in
rung-5 subprocess deriver**. Plan D's Tasks 9, 11 and 12 proceed **unchanged** —
rung 0 `ImageMetaDeriver` and rung 1 `ClipTagsDeriver` stay in-process Rust via
`fastembed-rs`, exactly as planned.

**What ross is.** A single Rust binary: media in → metadata, tags, description,
summary out. Sniffs type by magic bytes, extracts deterministic facts
(ffprobe for a/v, exiftool for images with a native PNG/WebP fallback, sha256,
size, mtime), then describes via either locally-run zero-shot CLIP/CLAP
(`--clip` / `--clap`, fully offline, replaceable vocabulary) or one LLM call per
file to any OpenAI-compatible endpoint. `--no-llm --clap --clip` needs no network.

**Where it overlaps Plan D:**

| ross | Plan D equivalent |
|---|---|
| Extract step | rung 0 — `ImageMetaDeriver` (Task 9) |
| `--clip` zero-shot tags | rung 1 — `ClipTagsDeriver` (Tasks 11, 12) |
| LLM pass | rung 3 — remote VLM |
| `--clap` audio tags | **nothing** — chunkshop has no audio tagger at all |

**Why an added rung rather than a replacement.**
- ross is **bin-only** (no `lib.rs`), so chunkshop would shell out, not link —
  that is NN-4's rung-5 escape-hatch shape by construction, not an in-process
  deriver.
- ross is v0.1.0 at 3 commits. The shipped default path should not depend on a
  sibling at that maturity.
- CLAUDE.md's standing rule: sibling repos are **user-wired via config**, never
  imported into core. A rung-5 subprocess deriver honours that; replacing the
  in-process path would not.
- ross requires `ffprobe`/`exiftool` for a/v and image metadata, which breaks
  **NN-2** (zero-config: no key, no GPU, no model choice). Keeping in-process
  rung 0 preserves a floor that works with nothing installed.
- It is *better* on **NN-6** (text-only users pay nothing) — a subprocess
  dependency costs them literally zero compile-time and runtime weight.

**Consequence to watch.** *(Note corrected 2026-09-01 — the decision above is
unchanged; only this attached factual note was wrong.)* ross drives `ort`
directly, **not** `fastembed-rs`, so it does its own image preprocessing.
Task 10's original framing — a looser `cosine >= 0.9999` bar caused by
`fastembed-rs`'s hardcoded `FilterType::CatmullRom` — was **misdiagnosed on
both counts**. `CatmullRom` *is* PIL's `BICUBIC`; the real cause was
`resize_exact` ignoring `size.shortest_edge`'s aspect-preserving semantics
(`utils.rs:51`/`:215`), an upstream bug chunkshop now preprocesses around.

*(Bar superseded 2026-09-01, same day, later: `cosine >= 0.99998` was itself
fitted to three flat-shaded fixtures and is FALSE for photographic content —
it fails 21 of 28 rows of a proper corpus. SC-12's vision bar is now two-tier:
`1 - cosine <= 1e-9` for no-resize images, `cosine >= 0.9995` plus
`max abs <= 1.2e-2` for resampled ones, measured over a 28-image corpus. See
SPEC.md SC-12 and `rust/chunkshop/tests/clip_embedding_parity.rs`. The
DECISION above — in-process rung 0 rather than a ross subprocess — is
unchanged by either revision; only this attached note moves.)*

The consequence for THIS decision is sharper than
before: ross **rounds** the CLIP long edge where Python truncates (326 vs 325 on
`chart_revenue.png`, worth 4.06°), so ross's vectors are *not* interchangeable
with chunkshop's today. If ross ever becomes the image path, its preprocessing
must be measured against Python directly, not assumed to inherit this bar.

**Follow-up (not scheduled).** A `ross` rung-5 deriver, and the audio question
it raises: chunkshop has no CLAP/audio tagger in any plan, and ross already does.
Tracked in MEDIA-INGEST-ROADMAP.md, not in Plan D.
