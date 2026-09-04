# PLAN — chunkshop media ingest (v1: images + spine)

Derived from `SPEC.md` (all 12 phases confirmed). Milestones are ordered so the tree stays
green and each slice is independently verifiable. Feature IDs and SC IDs refer to `SPEC.md`.

**Exit condition for v1:** SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7, SC-8, SC-9, SC-10, SC-11,
SC-13 all have evidence. (SC-12 Rust parity lands with M8; audio SCs are v1.1.)

---

## M0 — Fix the shipping defect (independent of everything else)
**F5.** `sources/parsers/pdf.py` silently returns `""` for scanned PDFs (`or ""`), producing a
0-chunk document that vanishes from the index.
- [ ] Empty parse result becomes an explicit, recorded failure — never a silent success
- [ ] Reconciliation counter: files-in == indexed + recorded-failures
- **Verify:** ingest a scanned PDF with no OCR configured → explicit failure with a reason, and
  the reconciliation count is correct. **Ships independently of media work.** (SC-5)

## M1 — The `Deriver` seam, backward-compatible
**F1.** Widen `FileParser` to `Deriver`.
- [ ] `ParseResult(text, metadata)`; `derive()` may return `str | ParseResult`; bare `str`
      auto-wraps to `ParseResult(text, {})`
- [ ] `FileParser` stays exported as a deprecated alias emitting `DeprecationWarning`
- [ ] Six existing parsers become rung-0 derivers; metadata reaches `promote_metadata`
- **Verify:** a parser written against the OLD `parse(path) -> str`, injected via
  `FilesSource(parsers=...)`, still works AND warns. No breaking change. (SC-8, SC-10, SC-13)

## M2 — The spine: cache + provenance + origin *(the differentiating layer)*
**F10, F11, F12, F23, F24.** Build this before any model call, so every deriver inherits it.
- [ ] `key = H(cache_schema_version, content_hash, derivation_hash)`;
      `derivation_hash = H(rung, model_id, model_version, params, prompt, argv)`
- [ ] Cache lives inside the deriver wrapper (not `FilesSource`); configurable root; TTL;
      `chunkshop cache purge`; `cache: {enabled: false}` opt-out (a nested
      `DerivationCacheSettings`, not a bare bool — `cache: false` fails validation)
- [ ] Provenance stamped into `derived.<tier>.{model,version,ts,params_hash,cost}`
- [ ] `content_origin` (`authored`|`derived`) + `derived_rung` as promoted columns, ON BY DEFAULT
- [ ] Budget ceiling + pre-run cost estimate
- **Verify:** unchanged re-run → **0** derivation calls. Change the prompt → full re-derive.
  Change only the target table → 0 re-derives. Two models on one asset → both recoverable,
  distinguishable provenance. `kill -9` at 50%, restart → no re-derivation of completed work.
  (SC-6, SC-7, SC-8, SC-9)

## M3 — Keyless image path *(this is the wedge — SC-1)*
**F2, F22, F14.**
- [ ] Rung 0: Pillow metadata (dimensions, format, mode, EXIF, palette) + path-convention segments
- [ ] Rung 1: CLIP zero-shot tagging via `fastembed.TextEmbedding` label vocabulary
      (user-configurable), output is TEXT into the unified plane
- [ ] Extras layout: `vision = []` (nothing needed), `ocr`, `audio`; lazy model download on first
      USE, never at import or install
- **Verify:** fresh container, `pip install chunkshop` (NO extras), run **the exact YAML printed in
  the README**, text query returns the correct image, **under 5 minutes**, no key, no GPU.
  Separately: base install runs existing text sample cells with byte-identical vectors.
  (SC-1, SC-2, SC-13)

## M4 — OCR rung
**F4.** RapidOCR behind `chunkshop[ocr]` (ONNX Runtime — already shipped; no torch, no apt).
- [ ] Standalone image files; PDF fallback when a page yields no embedded text
- [ ] Missing-extra raises `RuntimeError` naming the exact extra
- **Verify:** a scanned PDF of known text ingests with that text in `original_content`.

## M5 — Remote VLM rung *(structured, not prose)*
**F6.** OpenAI-compatible `/v1/chat/completions` over stdlib urllib.
- [ ] Copy `embedders/openai_provider.py`'s retry/backoff/`api_key_env` shape
- [ ] `response_format: json_schema` → structured fields (art_style, dominant_colors, composition)
      that `promote_metadata` lifts to indexed columns
- [ ] **`chat_template_kwargs: {enable_thinking: false}`** support; **`content: None` MUST raise**
- [ ] Concurrency default ~6 + circuit breaker; downscale to 512px before send
- **Verify:** against `192.168.1.133:8000` (`qwen36-nvfp4`) → schema-valid JSON, promoted columns
  populated, and a reasoning-model `content: None` fails loudly. (SC-4 arm, SC-10)

## M6 — Escape hatch + the two APIs made explicit
**F9, F21, F3.**
- [ ] `SubprocessDeriver`: argv template, extension list, timeout, stdout→JSON `{text, metadata}`
- [ ] `MediaEncoder` protocol with `encode_media` + `encode_query` (same-space guarantee)
- [ ] `ClipEmbedder` (`embedder.type: clip`, dim 512) + `whole_document` chunker if absent
- [ ] Search verbs: `search` (text plane), `search --visual`, `search --similar-to <file>`
- **Verify:** a user cannot get vector-search when they configured text derivation, or vice versa;
  `--similar-to` works only against an API-2 table and says so. (NN-5, F21)

## M7 — Prove it *(the phase-4 gate)*
**F13** + SC-3/SC-4/SC-11.
- [ ] Media arms in the bakeoff: clip_tags / OCR / caption / caption+clip_tags / CLIP-vector
- [ ] NDCG@10 + recall@10 per arm on a **real labelled corpus**, reproducible
- [ ] Whole-system run vs **gamelib's full fused RRF** on a shared query set
- [ ] Integration test: mixed corpus → one table → `pg-raggraph` and `stele` read paths unmodified
- **Verify:** bakeoff table published **whichever way it goes** (NN-1 — a loss redirects v1.1, it
  is not quietly dropped). Downstream tools read media rows with no changes and can filter
  `content_origin` without a join. (SC-3, SC-4, SC-11)

## M8 — Rust parity
**F17.** `fastembed`'s `image-models` feature is already enabled (`rust/chunkshop/Cargo.toml:104`).
- [ ] Same YAML, same schema, same metadata keys and provenance fields
- **Verify:** bit-level — same input text + model → vectors within float tolerance.
  Contract-level — schema/metadata-key equality on derived rows, engine named in provenance. (SC-12)

---

## v1.1 (decided, not vague)
- **F7 ASR** — faster-whisper (MIT, no torch, no system ffmpeg, native word+segment timestamps)
- **F8 audio segmentation** — largely free via faster-whisper's built-in Silero VAD
- **F14** extras: `audio = ["faster-whisper", "av", "soundfile"]` (PyAV is the only pip-only
  decoder covering m4a/AAC — real meeting audio)
- Decide `symphonia` (MPL-2.0) vs sherpa-onnx for the Rust audio path

## Later
Local on-box captioning rung (BLIP-class, torch ~2GB) · `chunkshop bakeoff init` to bootstrap a
labelled query set · F15 cross-plane RRF fusion · F16 · video · 3D-mesh proxy derivation

## Known limitations to state in docs, not hide
- Derivation cache holds raw derived text on local disk: **no redaction, no encryption-at-rest** in v1
- Adopters need their own labelled query set to use the bakeoff
- CLIP is weakest on the images most common in document corpora (text-in-image, diagrams,
  screenshots) — which is *why* the ladder and the bakeoff exist
