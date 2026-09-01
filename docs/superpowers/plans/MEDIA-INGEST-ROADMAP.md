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
| `Qdrant/clip-ViT-B-32-vision` | 9.3e-4 | ✅ pass at `cosine ≥ 0.99998` (does not meet `1e-6`) |

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

6.8e-4–9.3e-4 max abs per component, 0.24–0.27°. Cause: **sub-LSB rounding inside PIL's
fixed-point 8-bit resampler** versus `image`'s f32 one. Not a filter difference and not an
inference difference (the text towers agree at 1.8e-7 on the same runtime). Closing it would
mean reimplementing Pillow's integer kernel — not worth it. SC-12's vision clause is
therefore `cosine ≥ 0.99998`, **uniformly for every image**, which sits 1.37x above that
floor.

Consequence for the "vectors from any implementation are interchangeable" promise: for CLIP
vision it is true to within 0.27°, in both languages, for any image shape. Candidate pairs
separated by less than that could still order differently between implementations — rare,
real, and worth one line in the docs rather than a claim that overreaches. (The pre-fix
13–26° version of this caveat is retracted; it was measuring the bug, not the floor.)

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

`rust/chunkshop/tests/clip_embedding_parity.rs` applies ONE bar (`cosine ≥ 0.99998`,
max abs ≤ 2e-3) to all three fixtures — the earlier per-image floors (0.9734, 0.8971) are
gone; they encoded the bug. The test drives the shipped `open_for_clip` path, not a
test-only reimplementation, and its failure modes were injected and measured rather than
reasoned about: reverting to `embed(path)` → 13.23°, `.round()` instead of truncation →
4.06°, a one-pixel crop offset → 4.04°, `Triangle` → 5.86°, `Lanczos3` → 5.37°, `Nearest` →
13.51°. All fail the bar by 11–37x. Fixtures come from
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
chunkshop now preprocesses around it, SC-12's vision clause is a single
`cosine ≥ 0.99998` that holds for every image shape, and the release gate in `DECISIONS.md`
is clear on this axis.

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

## `ross` as a rung-5 subprocess deriver — DECIDED 2026-09-01, not scheduled

`../ross` (yonk-labs/ross, Apache-2.0, v0.1.0, bin-only) does media → metadata +
tags + description + summary as a single Rust binary. Decision recorded in
`DECISIONS.md`: it becomes an **opt-in rung-5 subprocess deriver**; Plan D's
Tasks 9/11/12 proceed unchanged with in-process `fastembed-rs`.

**Open items this creates:**

1. **A `ross` deriver (rung 5).** Shell out, parse its JSON, map its fields onto
   `ParseResult { text, metadata }` + provenance. `engine` would be `ross` plus
   its version — which is exactly what Task 6's `engine`-in-the-derivation-hash
   work makes safe: a ross-derived value can share a cache directory with a
   fastembed-derived one without either masquerading as the other.
2. **Audio is a genuine gap.** ross ships CLAP zero-shot audio tagging.
   chunkshop has **no** audio tagger in Plan D, Plan B, Plan C, or v1.1. The
   spec's escalation ladder (NN-4) has no audio rung below the remote-VLM tier.
   Worth its own scoping pass — this is the clearest thing ross has that no
   chunkshop plan covers.
3. **NN-2 boundary.** ross needs `ffprobe`/`exiftool` for a/v and image metadata
   (native PNG/WebP dimension fallback aside). A rung-5 deriver must degrade
   per NN-5 (never a dead end) when they are absent, and must not be reachable
   by a zero-config default path.
4. **SC-12 scoping caveat.** ross drives `ort` directly, not `fastembed-rs`, and
   does its own preprocessing — and it **rounds** the CLIP long edge where
   Python truncates (326 vs 325 on `chart_revenue.png`, worth 4.06°). So ross
   does NOT currently produce vectors interchangeable with chunkshop's or
   Python's. SC-12's `cosine ≥ 0.99998` vision bar is scoped to the shipped
   fastembed path plus `clip_preprocess`'s geometry. If ross ever becomes that
   path, its preprocessing must be measured against Python directly — not
   assumed to inherit this bar.
5. **Maturity gate.** v0.1.0, 3 commits, no `lib.rs`. Revisit linking-vs-shelling
   only if a library surface appears; until then subprocess is the only option
   and also the one CLAUDE.md's "siblings are user-wired via config" rule wants.
