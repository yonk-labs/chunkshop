# Media ingest — forward roadmap

Status as of 2026-09-01. Plan A (v1-A: Deriver seam, provenance/cache spine,
keyless image path) is **complete and merged-ready** on `feat/media-ingest-v1a`.
Spec: `spec/chunkshop-media-ingest/SPEC.md`.

## Release gate

**No new version ships until Plan D is done.** See `DECISIONS.md` (2026-09-01).

---

## Plan D — Rust media parity  ⛔ RELEASE BLOCKER

**Scoped 2026-09-01: 13.5-18.5 engineering-days.** Full study (gitignored working
copy): `.superpowers/plan-d-scoping.md` in the `feat/rust-media-parity` worktree.

### ✅ Top schedule risk RESOLVED before any port code was written

The scoping's #1 risk was "CLIP tokenizer/pooling divergence between Python
`fastembed` and `fastembed-rs` is unverified — test it early, not last."

**The concern was real and specific.** Python's `CLIPOnnxEmbedding._post_process_onnx_output`
(`fastembed/text/clip_embedding.py`) does `return output.model_output` — **no pooling**.
`fastembed-rs` 5.13.3 declares `EmbeddingModel::ClipVitB32 => Some(Pooling::Mean)`
(`text_embedding/impl.rs:247`). On paper, a divergence.

**Empirically identical.** Same fixture, same model, both L2-normalised, dim 512:

```
py   first8 = [ 0.02402054, -0.00598646,  0.00069226, -0.04729067, ...]  sum = 1.62794122
rust first8 = [ 0.02402058, -0.00598644,  0.00069229, -0.04729069, ...]  sum = 1.62794113
```

Agreement to ~1e-7 — float32 rounding, not semantics. The `Pooling::Mean` declaration is
**inert**: the Qdrant ONNX export already emits a pooled `[batch, 512]` tensor, so there is
no token axis to average and both sides pass it through. Probe:
`rust/chunkshop/examples/parity_probe.rs`. Reference vectors + generator: `tests/parity/`.

**`Xenova/bge-base-en-v1.5-int8` ALSO VERIFIED** (2026-09-01), through chunkshop's own
`FastembedEmbedder` rather than raw fastembed — the distinction matters, because this model
is not a stock fastembed variant on either side. Rust routes it via `user_defined_source`
to `("Xenova/bge-base-en-v1.5", "onnx/model_quantized.onnx")` on a hand-rolled CLS-pooled
path (`embedder.rs:92-102`). Max |Δ| over the first 8 components:

```
clip_text        ~4e-8   PASS (tol 1e-6)
text_bge_int8    ~5e-7   PASS (tol 1e-6)
```

### ✅ CLIP **vision** tower — upstream `fastembed-rs` bug, FIXED 2026-09-01

**Status: fixed in chunkshop.** All three models now meet SC-12. The section below records
what the bug actually was, because two earlier write-ups in this file got the cause wrong
and the wrong cause was propagated into SPEC.md and into the parity test's bars.

| model | max abs Δ | verdict |
|---|---|---|
| `Qdrant/clip-ViT-B-32-text` | 1.8e-7 | ✅ pass at `1e-6` |
| `Xenova/bge-base-en-v1.5-int8` | 5e-7 | ✅ pass at `1e-6` |
| `Qdrant/clip-ViT-B-32-vision` | 2.1e-4 – 5.3e-3 (content-dependent) | ✅ pass at the two-tier bar below (does not meet `1e-6`) |

#### The real root cause: `resize_exact` ignores `shortest_edge`'s semantics

`preprocessor_config.json` for the vision tower says `size: {"shortest_edge": 224}` +
`crop_size: 224x224`. HuggingFace `CLIPImageProcessor` semantics for `shortest_edge`:
scale so the SHORTEST edge is 224 **preserving aspect ratio**, then center-crop 224x224.
Python `fastembed` does exactly that (`fastembed/image/transform/functional.py::resize`,
`new_long = int(size * long / short)`).

`fastembed-rs` 5.13.3 does not:

```rust
// src/image_embedding/utils.rs:215  — shortest_edge 224 becomes a SQUARE target
let size = (shortest_edge as u32, shortest_edge as u32);
// src/image_embedding/utils.rs:51   — applied with a non-aspect-preserving resize
let image = image.resize_exact(self.size.0, self.size.1, self.resample);
```

`resize_exact` squashes any image to 224x224, which also makes the following center-crop a
no-op. The two implementations therefore see a **different field of view** — not a different
resampling of the same view.

**It is aspect-ratio-dependent, NOT downscale-dependent.** For a square image
`resize_exact(224,224)` happens to *be* the aspect-preserving answer, so square images were
always correct in either direction. Measured through the real ONNX vision tower, same
`FilterType::CatmullRom` on both sides, only the geometry differing:

| image | source | shape | squash (buggy) | aspect-correct (fixed) |
|---|---|---|---|---|
| `sprite_knight.png` | 128x128 | square, upscaled | 0.999989313 (0.265°) | 0.999989313 (0.265°) |
| `chart_revenue.png` | 320x220 | non-square | 0.973478593 (13.225°) | 0.999991164 (0.241°) |
| `screenshot_settings.png` | 360x260 | non-square | 0.897173546 (26.211°) | 0.999991266 (0.239°) |

The square fixture is **unchanged** by the fix; the two non-square ones improve ~55x in
angle. Synthetic crops of `screenshot_settings.png` put the squash at **7.1°** at an aspect
ratio as mild as **1.04**, so this was never an extreme-shape-only problem.

#### What was NOT the cause: the filter

Two earlier passes recorded the cause as "`load_preprocessor` hardcodes
`FilterType::CatmullRom`, ignoring `preprocessor_config.json`'s `"resample": 3` = PIL
`BICUBIC`". **That is wrong.** `CatmullRom` *is* PIL's `BICUBIC` — the same Keys cubic with
a = −0.5. At matched dimensions the two agree bit-for-bit on 96.6–98.8% of pixels, mean abs
error 0.013–0.044 out of 255. The hardcoding is a latent robustness issue for a model whose
config asks for a different filter, but it was never what moved these vectors.
**Do not change the filter.**

The second earlier pass then read the same three numbers as "upscale is fine, downscale is
broken". Also wrong — a coincidence of this fixture set, in which the square image is also
the small one.

#### The fix chunkshop applied

`rust/chunkshop/src/sources/derivers/clip_preprocess.rs` does CLIP's geometry itself —
RGB conversion, aspect-preserving resize to a 224 shortest edge (with Python's `int()`
**truncation**, not rounding), 224x224 center crop — and `ClipTagsDeriver::score_image`
hands the already-224x224 image to `ImageEmbedding::embed_images` instead of handing a path
to `ImageEmbedding::embed`. `fastembed-rs`'s own resize and crop then run on a 224x224
input, where both are provable identities: `image` 0.25.10's `imageops::resize`
short-circuits to a buffer copy when the target dimensions equal the source dimensions
(`src/imageops/sample.rs:985`), so the resampler never runs a second time. Everything after
geometry (`PILToNDarray`, `Rescale`, `Normalize`) is left to `fastembed-rs`, which already
matched Python componentwise.

Truncation is load-bearing: the sibling tool `ross` rounds the long edge instead
(326 vs 325 on `chart_revenue.png`), and that one pixel costs **4.06°**.

#### What remains, and why it is a floor

**Re-measured 2026-09-01 (later the same day) over a 28-image corpus.** The "6.8e-4–9.3e-4
max abs, 0.24–0.27°, therefore `cosine ≥ 0.99998` uniformly" figures that stood here were
measured on three flat-shaded Pillow drawings, and they are NOT representative: the residual
is **content-dependent**, not shape-dependent. Against a corpus spanning photographs
(aspect 1:4→4:1), greyscale, text, texture and flat content, it runs:

| | cosine | angle | max abs |
|---|---|---|---|
| best (checkerboard) | 0.999999140 | 0.075° | 2.1e-4 |
| median | 0.999908283 | 0.775° | 2.1e-3 |
| worst (400x1600 photo) | 0.999775132 | 1.215° | 4.5e-3 (worst max abs 5.3e-3, an upscaled photo) |

Median by content class: flat 0.241°, texture 0.526°, text 0.599°, photo 0.824°, greyscale
1.072°. The three original fixtures ARE the flat class — the three easiest resampled rows in
the corpus. `cosine ≥ 0.99998` fails **21 of 28 rows of correct output**.

Cause is unchanged and now positively pinned: **sub-LSB rounding inside PIL's fixed-point
8-bit resampler** versus `image`'s f32 one. Not a filter difference and not an inference
difference — images that need NO resize (224x224, 225x224) agree at `1 - cosine ≈ 4e-13`
and max abs 2.7e-7, i.e. bypassing the resampler removes 100% of the residual. Closing it
would mean reimplementing Pillow's integer kernel — not worth it.

SC-12's vision clause is therefore **two-tier and two-axis**: `1 - cosine ≤ 1e-9` and
`max abs ≤ 1e-5` for no-resize images; `cosine ≥ 0.9995` and `max abs ≤ 1.2e-2` for
resampled ones. Both carry ~2.2x headroom over the worst measurement. The tight tier is what
still catches a weight/runtime change; the loose tier is what is actually true of the
resampler.

Consequence for the "vectors from any implementation are interchangeable" promise: for CLIP
vision it is true to within ~1.2°, in both languages, for any image shape and content —
0.24° was only ever true for flat-shaded drawings. Candidate pairs separated by less than
that could order differently between implementations — rare, real, and worth one line in
the docs rather than a claim that overreaches. (The pre-fix 13–26° version of this caveat is
retracted; it was measuring the bug, not the floor.)

#### ⚠️ Upstream: this is a `fastembed-rs` bug worth reporting

`resize_exact` at `utils.rs:51` combined with the square target built at `:215` mis-implements
`CLIPImageProcessor`'s `shortest_edge` for **every** `fastembed-rs` user embedding a
non-square image with any CLIP-family model — they are silently getting a different field of
view from the Python reference implementation and from every HF-based pipeline. Worth an
upstream issue/PR. Noted here only; **nothing has been filed.**

#### ⚠️ Incidental finding: int8 vision models amplify sub-LSB pixel noise ~300x

Measured during the same investigation, and it matters for future rungs: through an **int8**
vision model, byte-identical input tensors still diverged by **6.89°**. Quantization
amplifies sub-LSB pixel differences by roughly **300x** relative to the fp32 tower's 0.24°.
Any parity tolerance for a quantized vision tower — ColPali, an int8 CLIP, a quantized
late-interaction arm — must budget for that **separately from preprocessing**. A tolerance
derived from the fp32 CLIP numbers above will not transfer.

#### Regression coverage

`rust/chunkshop/tests/clip_embedding_parity.rs` applies the two-tier bar above to a
28-image corpus (`tests/parity-fixtures/clip-vision-corpus/`, built by
`tests/parity/generate_clip_vision_corpus.py`), replacing the three fixtures it used to
run on; the earlier per-image floors (0.9734, 0.8971) are gone, and so is the
three-fixture `0.99998`, which encoded a fixture-selection artifact rather than a bug.
Which rows get the tight tier is DERIVED from the shipped `target_size`, not listed by
hand. The test drives the shipped `open_for_clip` path, not a test-only reimplementation,
and its failure modes were injected and re-measured across all 28 rows rather than
reasoned about: reverting to `embed(path)` → 41.20° worst, 17 rows failing; `.round()`
instead of truncation → 4.17°, 6 rows; a one-pixel crop offset → 9.20°, 17 rows;
`Triangle` → 8.90°, 25 rows; `Lanczos3` → 5.87°, 19 rows; `Nearest` → 27.92°, 26 rows.
The weakest of those still misses by 5.3x in cosine distance. The text fixture comes from
`tests/parity/generate_clip_full_vectors.py` (Python `fastembed`) into
`rust/chunkshop/tests/parity-fixtures/clip_{text,vision}_full_reference.bin` and are never
regenerated from the Rust side.

Probe: `rust/chunkshop/examples/parity_probe.rs` (set `PARITY_FULL=1` to dump full vectors).

#### Re-measured text-tower delta, 2026-09-01 (Plan D Task 13)

Full 512-dim comparison, Python `fastembed` 0.8.0 vs Rust `fastembed` 5.13.3, threads pinned
to 1 on both sides:

```
clip-ViT-B-32-text  "a game sprite or pixel art"          max abs Δ 1.788139e-7   cosine 1.000000000000
clip-ViT-B-32-text  "quarterly revenue chart, bar graph"  max abs Δ 2.384186e-7   cosine 1.000000000000
```

Comfortably inside SC-12's `1e-6`, but ~4.5x larger than the `~4e-8` recorded above. The
earlier figure came from the `first8`/`sum` JSON summary; these are full-vector maxima over
all 512 components, so the two numbers are measuring different things — not a regression.

⚠️ **Related caveat found in `embedder.rs:551-561`, NOT a blocker but worth knowing:** the
semantic chunker's int8 MiniLM boundary model has *no* bit-exact Rust path — it is mapped to
fastembed-rs's stock `AllMiniLML6V2Q`, a different model, deliberately ("close enough for
boundary detection"). So Rust already carries one documented parity compromise. It does not
affect the media path, but SC-12 claims should be worded to cover the models media actually
uses rather than "all embedders".

⚠️ **Porting detail the scoping missed:** `Xenova/bge-base-en-v1.5-int8` — chunkshop's own
default embedder — is **not** in fastembed's stock registry on either side. Python registers
it at import (`embedders/_registry.py`); Rust must go through `resolve_model_name` +
`try_new_from_user_defined` (`embedder.rs`). A parity test using `EmbeddingModel::` directly
would not exercise chunkshop's actual path.


Bigger than it looks. Rust has **no parser layer at all** today
(`rust/chunkshop/src/sources/files.rs:36` reads every match with
`std::fs::read_to_string`, no extension dispatch), so the SP-3 parser layer
(pdf/docx/pptx/xlsx/html) must land before any media rung.

- [x] Rust parser/extension-dispatch layer (the missing SP-3 foundation) — `rust/chunkshop/src/sources/parsers/{registry,pdf,docx,xlsx,html,text}.rs`
- [x] `Deriver` equivalent + `ParseResult` (text + metadata) — `sources/parsers/mod.rs`
- [x] `derive` / `on_empty` / `cache` modelled in `FilesSourceConfig` — `config.rs:442-470`
- [x] Provenance + derivation cache (mirror the `(content_hash, derivation_hash)` key) — `sources/derivers/{provenance,cache}.rs`
- [x] CLIP via `fastembed-rs` — `sources/derivers/clip_tags.rs`
- [x] SC-12 parity tests: bit-level for embedding, contract-level for derivation — `tests/clip_embedding_parity.rs` (Task 13), `tests/media_derivation_parity.rs` (Tasks 9 + 14)
- [x] *Interim:* `deny_unknown_fields` on `FilesSourceConfig` so Rust fails loudly meanwhile

**M2 closed 2026-09-01 (Plan D Tasks 13 + 14).** The vision-tower blocker Task 13 surfaced
was **root-caused and fixed** the same day: it was an upstream `fastembed-rs` aspect-ratio
bug, not a filter difference and not a downscale regime — see the CLIP vision section above.
chunkshop now preprocesses around it, and the release gate in `DECISIONS.md` is clear on
this axis. SC-12's vision clause was restated the same day as a single
`cosine ≥ 0.99998`, and then **again** as the two-tier corpus bar above once that single
number turned out to be fitted to three flat-shaded fixtures — see "What remains, and why
it is a floor".

**Also surfaced, own follow-up:** Rust implements `IncrementalSource` for s3 /
pg_table / http but **not files** — a `files` + `incremental:` YAML has been
silently full-resyncing on Rust since v0.8.3.

## Plan B — the paid/opt-in rungs (M4-M6)

- [ ] **F4** OCR rung — RapidOCR (ONNX, rides the runtime chunkshop already ships)
- [ ] **F6** Remote VLM captioning — structured `json_schema` output → `promote_metadata` columns.
      **Endpoint confirmed working for VISION** (audio is NOT — see below):
      `192.168.1.189:8082`, models `mlx-community/gemma-4-e4b-it-4bit` and
      `gemma-4-E2B-it-qat-4bit`, 2-3s per image.
      Verified 2026-09-01 with a **discrimination control**, not a single sample —
      four visually distinct images, four correct and different answers:

          blue/white checkerboard -> "a vibrant blue and white checkered pattern"
          solid red               -> "a solid, vibrant red background with no discernible pattern"
          6 rainbow stripes       -> "the Pride flag with horizontal stripes of various colors"
          black diagonal on white -> "a simple black diagonal line on a white background"

      The control matters: the same endpoint's *audio* path passes a naive single-sample
      test and fails the control (below). Run the control before trusting any new endpoint.
      ⚠️ `lmstudio-community/gemma-4-E4B-it-MLX-4bit` fails to load server-side.
      ⚠️ Reasoning models return `content: None` at default settings — must raise, never
      write an empty caption (see SPEC D4).
- [ ] **F9** Subprocess deriver (rung 5, the never-a-dead-end escape hatch)
- [ ] **F24** Cost governance — budget ceiling, pre-run estimate, cost in provenance
- [ ] **F3/API-2** CLIP visual plane (`embedder.type: clip`, own table, `--similar-to`)
- [ ] Re-add `ocr` / `audio` / `media` extras **with** their rungs (removed from v1-A
      deliberately — they installed weight and delivered nothing)

## Plan C — prove it (M7)

- [ ] **F13** media arms in the bakeoff: clip_tags / OCR / caption / caption+tags / CLIP-vector
- [ ] **SC-3** whole-system vs gamelib's fused RRF on a shared query set — publish
      the result **whichever way it goes** (NN-1)
- [ ] **SC-11** integration test: mixed corpus → one table → pg-raggraph and stele read paths
- [ ] Settle the open NN-1 question: `path_segments: true` injects arbitrary directory
      names (incl. UUIDs) into embedded content — measure whether it helps or dilutes

## v1.1 — audio

- [ ] **F7** ASR deriver — faster-whisper (MIT, no torch, no system ffmpeg, native timestamps)
- [ ] **F8** segmentation before transcription (largely free via faster-whisper's Silero VAD)
- [ ] Decide `symphonia` (MPL-2.0) vs sherpa-onnx for the Rust audio path
- [ ] ⛔ **`192.168.1.189:8082` CANNOT be used for audio. Tested and disproven 2026-09-01.**
      There is no `/v1/audio/transcriptions` endpoint, and `gemma-4-e4b-it-4bit` does not
      usefully hear audio sent via `input_audio`. It *allocates* audio tokens by duration
      (109 prompt_tokens for any 3.0s clip), but the audio does not inform the answer.
      Decisive control — three acoustically unrelated 3.0s clips, one prompt:

          silence (all-zero samples)  -> "It is a low-pitched, steady sound."
          60 Hz steady sine           -> "The sound is low-pitched and steady."
          2 kHz chirp, pulsing 6/sec  -> "The sound is low-pitched and steady."

      Digital silence and a high-pitched pulsing chirp produce the same description.
      An earlier probe returning "The sound is of a mosquito" for a warbling tone was a
      lucky guess, not perception — do not read it as evidence.

      **Why this matters more than a missing feature:** it fails by returning a fluent,
      confident, *wrong* answer with HTTP 200. An ASR deriver pointed here would fill a
      knowledge base with hallucinated transcripts of client meetings and never error.
      This is exactly the failure class NN-3/SC-8 provenance and the D4 `content: None`
      rule exist to catch — and it defeats both, because the response looks valid.

      **Consequence:** v1.1 audio must use **faster-whisper locally** (already the spec's
      Tech Stack choice — MIT, no torch, no system ffmpeg, native word/segment timestamps
      and Silero VAD). Do not substitute a multimodal chat endpoint for ASR without
      running this same silence-vs-signal control against it first.

## Deferred minors (from the v1-A execution ledger)

Full list with reasoning: `spec/chunkshop-media-ingest/evidence/sdd-execution-ledger.md`.

- [ ] sqlite search without `--include-facts` fails with `unsupported where keys:
      ['metadata_not']` — name the remedy in a `UsageError` (~4 lines in `cli.py`)
- [ ] `--vector-metric` / `--ef-search` silently discarded on sqlite targets
- [ ] `fact-search` still on the Postgres-only path (`cli.py:1357`)
- [ ] `ClipTagsDeriver._ensure_models` sets `_vision` before `_label_vectors`; a shared
      instance could be observed half-built (no threaded call site today)
- [ ] Derivation cache has no eviction/TTL and no CLI surface — add `chunkshop admin cache purge`
- [ ] `docs/reference/parsers.md` still documents only `FileParser.parse -> str`
- [ ] `hybrid_search_fn` is an untyped escape hatch on a public entry point — mark provisional
- [ ] `id_from: stem` risks basename collisions across image directories
- [ ] SPEC.md still references the not-yet-shipped `chunkshop[ocr]` / `chunkshop[audio]`
      extras as future design (F4, the rung ladder, the deriver diagram). Forward-looking
      by intent; worth one line saying extras ship WITH their rungs. PLAN.md does not
      reference them, and no doc references `chunkshop[media]` — both halves of the
      original wording were wrong and are corrected here (2026-09-01).

---

## ⚠️ Systemic finding — Rust config permissiveness is not a media problem

Surfaced by the change-A survey (2026-09-01). **Only 6 of 49 Rust config structs
have `deny_unknown_fields`** — all six from the SP-A memory work. Every other
config silently ignores fields it does not model. Two of these affect *shipped*
functionality, independent of media:

- [ ] **`dsn` is silently dropped by every table source config and every target
      config.** Rust models only `dsn_env`. Python has supported a direct `dsn`
      since v0.4.3 (a "batteries-included" feature added for stele). A YAML using
      `dsn:` therefore behaves differently on the two implementations.
- [ ] **The `fts` block is silently dropped by every target config** — Rust has no
      `FtsConfig` at all. So a cell that asks for full-text search gets a GIN
      tsvector index under Python and **no index** under Rust, from the same YAML.
      Hybrid search then differs between implementations while both report success.
- [ ] `comment_extracts` and `connector` source types are unmodeled in Rust, but
      those fail loudly at the enum tag — correct behaviour, not silent.

**Recommendation:** audit `deny_unknown_fields` across all 49 structs as its own
task, ahead of or alongside Plan D. The pattern this branch hit twice — silently
accepting config you cannot honour — is a cross-implementation contract bug, and
the media fields were only the instance that happened to get noticed.
Full struct-by-struct list: `.superpowers/rust-deny-unknown-fields-report.md`.

---

## `ross` as a rung-5 subprocess deriver — SHIPPED 2026-09-01, both ports

`../ross` (yonk-labs/ross, Apache-2.0, v0.1.0, bin-only) does media → metadata +
tags + description + summary as a single Rust binary. Decision recorded in
`DECISIONS.md`: it becomes an **opt-in rung-5 subprocess deriver**; Plan D's
Tasks 9/11/12 proceed unchanged with in-process `fastembed-rs`.

**Status of the five open items this created:**

1. ✅ **A `ross` deriver (rung 5) — SHIPPED, Python and Rust.**
   `rung: ross` is a third variant in `DeriverConfig` in both ports, accepting
   byte-identical YAML (`binary`, `labels_file`, `llm`, `timeout_s`). It shells
   out with `--json --quiet --clip --clap` (plus `--no-llm` unless `llm: true`),
   parses ross's JSON and maps it onto `ParseResult { text, metadata }` with a
   **fixed** nine-key `ross_*` metadata set. `engine` is `"ross"` — not
   `"python"`/`"rust"` — because both ports drive the SAME binary, which means
   a Python-derived and a Rust-derived value legitimately share ONE cache entry
   rather than each masquerading as the other. Verified end to end: a Rust
   `chunkshop ingest` served all four documents from the entries a prior Python
   run wrote, byte-identical.

   **No compile-time dependency in either language** — no Cargo feature, no
   Python extra. `cargo build --no-default-features` builds rung 5. NN-6 holds
   exactly.

   Sample config: `docs/samples/media-ross.yaml`. Code:
   `python/src/chunkshop/sources/derivers/ross.py`,
   `rust/chunkshop/src/sources/derivers/ross.rs`.

2. ✅ **Audio — CLOSED, and it came free.** ross sniffs by magic bytes, so
   routing `.wav`/`.mp3`/`.flac`/`.ogg` through the same `derive:` block needs
   no extension registry work, no MIME handling and no second rung. A one-second
   440Hz tone derives to CLAP tags with the SAME metadata key set as an image,
   asserted in both ports. This remains the only audio tagger anywhere in
   chunkshop; NN-4's ladder still has no in-process audio rung.

3. ✅ **NN-2 / NN-5 boundary — held.** Rung 5 is opt-in and unreachable from
   any default path. A missing, non-executable, non-ross or failing binary, and
   a timeout, each produce a named error citing the binary, the exit code's
   documented meaning and **ross's own stderr verbatim** — which is what names
   a missing `ffprobe`/`exiftool`, since chunkshop cannot. Both ports fail at
   deriver CONSTRUCTION (before the sink's DDL), so a `mode: overwrite` cell
   with a bad `binary:` does not drop the user's table first.

   ⚠️ **Known limitation — vendored-vocabulary identity in the cache key.**
   ross has a third input beyond content and user config: a 50-label vocabulary
   vendored INSIDE the binary. Nothing on ross's CLI reports it — `--json` does
   not echo the label set and `--doctor` prints only external binaries,
   endpoint config and model cache paths. The deriver therefore identifies it
   by `ross --version`, probed at construction and recorded as `model_version`.
   **A ross build that edits `src/clip_labels.txt` without bumping its package
   version will silently serve stale cached derivations.** A user-supplied
   `labels_file` has no such gap: its CONTENT is hashed into
   `params["labels_sha256"]`. Mitigation for the vendored case is
   `cache: {enabled: false}` or purging the cache after upgrading a
   source-built ross. Same shape, smaller: ross's `metadata` block depends on
   whether `exiftool`/`ffprobe` are installed, which is likewise not in the key.

   The `cache_key_extra` this rung declares is the file's **basename**, not
   `None` and not the full path: when the local tagger is gated ross falls back
   to a description humanised from the basename (`clap_tag::humanize` rsplits
   on `/` first), so the deriver is not a pure content function — but the parent
   directories genuinely are not consumed. Both ports have a test proving the
   pairing: two byte-identical images under different names MISS rather than
   being served each other's text, while the same name still hits.

   ✅ **Same defect class, `llm: true` — FIXED 2026-09-02, both ports.** When
   the LLM pass was enabled the endpoint and model were resolved from the
   ENVIRONMENT (`ROSS_VISION_URL` > `ROSS_URL`, `ROSS_VISION_MODEL` >
   `ROSS_MODEL`) and appeared nowhere in `params()`, so they were not in the
   derivation hash. Measured, same cell, same images, only `ROSS_MODEL` changed
   between runs:

   ```
   run 1, gemma-4-E2B-it-qat-4bit    7.04s   2 cache entries written
   run 2, Ornith-1.0-9B              0.61s   served entirely from run 1's cache
                                             text identical (gemma's words)
                                             cache still 2 entries
   ```

   Worse than the vendored-vocabulary gap it resembles: the model is remote and
   can change server-side with no local signal at all. The fix runs
   `ross --doctor` at deriver construction (only when `llm: true`) and folds the
   per-modality **url + model + whether a key is set** into
   `params["llm_endpoints"]`. Resolution is delegated to ross rather than
   reimplemented — its precedence is its own, and duplicating it in two host
   languages is the drift class this branch already has enough of. Same
   scenario after the fix:

   ```
   run 1, gemma-4-E2B-it-qat-4bit    5.31s   2 cache entries
   run 2, Ornith-1.0-9B             25.79s   REAL re-derivation, cache 2 -> 4
                                             different text, ross_model=Ornith
   run 3, gemma again                0.73s   hits run 1's entries, cache still 4
   ```

   The API key's **value** is never read, hashed, stamped or logged; only the
   boolean ross itself prints (`key=set` / `key=none`) enters the params, and it
   earns its place because adding a key to a keyless endpoint turns a
   401-and-`llm_error` degraded derivation into a real one. Both ports have a
   leak test driven by a stub that reports `key=set`, and `--doctor` output is
   redacted at ` key=` before it can reach an error message.

   `llm: false` params are byte-identical to what they were before the fix
   (`{"labels_sha256": null, "llm": false}`, hash
   `314091bc293d054ae41970523db6b580df4c3acf2e6af04a36293c526531d829` on a
   `ross 9.9.9` stub), pinned as a literal in both ports so a future edit cannot
   drift it silently: `llm_endpoints` is ADDED when the LLM is on, never
   present-and-null when it is off, so no existing keyless cache entry was
   invalidated to fix a bug that could not affect it.

   ⚠️ **The honest limitation that remains: LLM output is not deterministic.**
   With `llm: true` the derived text comes from a remote model doing sampling.
   Two fresh derivations of the same image can differ even with the endpoint,
   the model and the prompt all identical — the hash pins the INPUTS, not the
   output. **On this rung the cache is therefore not an optimisation, it is
   what makes a corpus stable**: every row derived once and thereafter served
   from that one entry. The corollary is materially different from rungs 0 and
   1, which are pure functions of the bytes: `chunkshop cache purge` followed by
   a re-ingest can legitimately change derived text, and therefore the
   embeddings, with no config change at all. Purge this cache only when you mean
   to re-derive the corpus. Recorded in the module docstring of both ports too,
   which is where a reader hits it.

   📌 **Upstream ask: `ross --doctor --json`.** The weak point of the fix above
   is that it parses human-readable output — `--doctor --json` is accepted but
   ignored as of ross 0.1.0 (verified; the flag prints the human format). Both
   ports therefore hand-parse `vision <url> model=<m> key=<none|set>` and treat
   any shape they cannot read as a hard construction-time error. ross is a
   sibling repo we control, so the right fix is upstream: emit a stable JSON
   document from `--doctor --json` (per-modality `url`, `model`, `key_set`, plus
   the external-binary and model-cache checks), and let both ports consume that
   instead. Until then, a ross release that changes `--doctor`'s wording breaks
   this rung loudly at construction rather than silently in the cache — which is
   the correct failure mode, but still a failure mode worth removing.
4. **SC-12 scoping caveat.** ross drives `ort` directly, not `fastembed-rs`, and
   does its own preprocessing — and it **rounds** the CLIP long edge where
   Python truncates (326 vs 325 on `chart_revenue.png`; re-measured across the
   28-image corpus this costs up to 4.17° and fails 6 rows). So ross does NOT
   currently produce vectors interchangeable with chunkshop's or Python's.
   SC-12's two-tier vision bar is scoped to the shipped fastembed path plus
   `clip_preprocess`'s geometry. If ross ever becomes that
   path, its preprocessing must be measured against Python directly — not
   assumed to inherit this bar.
5. **Maturity gate.** v0.1.0. Note the README now documents a `lib.rs` and a
   public `ross::clip_tag::ClipTagger` surface, so "bin-only" is out of date as
   a FACT — but it does not reopen the decision: DECISIONS.md's other four
   reasons (the user-wired-siblings rule, the unpublished v0.1.0 dependency,
   NN-4's rung-5 shape, and ross's own note that ONNX's process-global state
   makes subprocess isolation the safer boundary) all stand on their own, and
   the zero-compile-time-dependency property is worth more than in-process
   speed for an opt-in rung.
