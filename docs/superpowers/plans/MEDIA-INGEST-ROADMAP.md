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

**Consequences:** SC-12 bit-level parity is achievable for both text models chunkshop ships;
use a `1e-6` tolerance, not exact equality. Only the CLIP **vision** tower remains unchecked
(needs an image fixture — `docs/samples/images/sprite_knight.png` is committed and suitable).

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

- [ ] Rust parser/extension-dispatch layer (the missing SP-3 foundation)
- [ ] `Deriver` equivalent + `ParseResult` (text + metadata)
- [ ] `derive` / `on_empty` / `cache` modelled in `FilesSourceConfig`
- [ ] Provenance + derivation cache (mirror the `(content_hash, derivation_hash)` key)
- [ ] CLIP via `fastembed-rs` — **`image-models` is already enabled**, `rust/chunkshop/Cargo.toml:76`
- [ ] SC-12 parity tests: bit-level for embedding, contract-level for derivation
- [x] *Interim:* `deny_unknown_fields` on `FilesSourceConfig` so Rust fails loudly meanwhile

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
- [ ] `CLAUDE.md` wrongly says Postgres `database_name` is "ignored at runtime" (it is the schema)
- [ ] SPEC/PLAN still reference the retracted `ocr`/`audio`/`media` extras as future design

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
