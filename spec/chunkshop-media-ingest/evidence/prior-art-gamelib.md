# Prior art — gamelib (/home/yonk/yonk-apps/game-lib)

Read-only inventory, 2026-09-01. The user's "hacked in" media handling. In fact
substantially well-designed; several patterns are directly reusable.

## Ingest
- `ingest/walker.py:25-31` walks all regular files (excl. `.zip`; zips auto-extract `:6-22`).
- `ASSET_FORMATS` at `metadata/path_extract.py:6-10` — images .png/.jpg/.jpeg/.webp/.tga/.gif,
  mesh .glb/.gltf/.fbx/.obj, audio .wav/.ogg/.mp3/.flac/.m4a, LUT .cube. No video. No sprite-sheet type.
- **TWO-PASS by design** (`ingest/pipeline.py:1-10`): `run_ingest_fast` (hash+metadata+embed)
  then a separate `run_caption_backfill`. Rationale: captioning is **87% of per-file time**.

## Derivation per media type
| Type | Method | Model / mechanism |
|---|---|---|
| Image | path convention + caption | `<type>/<style>/<subject>/` → metadata (`path_extract.py:22-42`); BLIP `Salesforce/blip-image-captioning-base` via HF transformers pipeline, CPU (`tag_describe.py:25,38`) |
| 3D mesh | proxy image, then image path | texture extraction or FBX→GLB + silhouette render (`pipeline.py:49-92`) |
| PBR map | deterministic, NO ML | role-based description (`pipeline.py:210-217`) |
| LUT | deterministic `.cube` header parse | `metadata/lut_describe.py` |
| Audio | zero-shot classify | CLAP `laion/larger_clap_music` vs 40-term vocabulary (`audio_embed.py:18,27-43,80-88`) |
| Audio <2s | filename-derived | hallucination gate (`audio_embed.py:25,96-101`) |

## Two-tier model strategy (KEY PATTERN)
Cheap always-on baseline + **opt-in "strong" tier**, results in SEPARATE COLUMNS that
never overwrite each other (`db.py:64-67,98-102`).
- Strong image: any OpenAI-compatible vision endpoint, env `GAMELIB_STRONG_VLM_URL/_MODEL/_KEY` (`strong_vlm.py:5-14`)
- Strong audio: env `GAMELIB_STRONG_AUDIO_URL/_MODEL/_KEY` (`strong_audio.py:10-14`); verified vs `google/gemma-4-E4B-it` on vLLM
- `reidentify`/`audio_reidentify` (`mcp/server.py:87-140`): manual, per-asset, opt-in, NEVER automatic.
  Raise if env unset — no silent fallback, no accidental cloud spend (`strong_vlm.py:110-121`, `strong_audio.py:77-96`).

## Schema (Postgres, `db.py:10-180`)
- Derived CONTENT: `description`, `render_description`, `audio_strong_description`
  → all folded into one generated FTS column `description_tsv` (`:25,86-88,119-121`)
- Structural METADATA: `path, hash, type, style, subject, format, dimensions, rig_status,
  source_games[], variation_group, collection, needs_tag`
- Side tables: `tags`, `dedup_groups`/`dedup_group_members`, `manifests`/`manifest_slots`, `asset_path_history`

## Embeddings — NOT text-derived
**Two vector columns, deliberately not comparable** (`db.py:93-96`):
- `embedding VECTOR(512)` — CLIP `sentence-transformers/clip-ViT-B-32`, CPU (`embed.py:13,23-24`). Embeds the IMAGE.
- `audio_embedding VECTOR(512)` — CLAP (`audio_embed.py:18,61-68`). Embeds the AUDIO.
pgvector in the same Postgres DB; no separate vector store.

## Search
Hybrid, weighted **reciprocal rank fusion** RRF_K=60 (`query/engine.py:276-292`, weights `:442-470`):
predicate hard-filters (`type:/style:/format:/rig:/tag:/collection:/added:>`) AND-ed with a
free-text leg fused across filename ILIKE/trigram + tag match + FTS + CLIP similarity.
Auto-broadens on zero results (`mcp/server.py:37-49`).

## The actual pain (evidence, not vibes)
- No TODO/FIXME/HACK in `src/` — the core is deliberate.
- `scripts/run_blip_backfill.py` vs `scripts/run_caption_backfill.py` — ~90% duplicate copy-paste.
- `scripts/redescribe_sfx_batch.py` hardcodes 9 `STYLES` as a targeted re-run after a vocabulary change.
- **Two reactive drop-and-recreate migrations** (`db.py:78-91`, `:111-125`), each needed when a new
  "stronger model" text field was bolted on — Postgres cannot ALTER a generated column's expression
  in place. Schema was not designed for that extensibility up front. ← THE REGRET
- `.remember/today-2026-08-07.done.md:2` records original motivation: token waste + visual
  inconsistency at scale.

## ADDENDUM — deep read (2026-09-01), the numbers that matter

### Scale + measured timings (from AGENTS.md / SESSION-STATUS.md / scripts/profile_pipeline.py)
- **113,973 assets** in Postgres. 3,571 dupes collapsed. **Only ~70% query-ready.**
- **~30% (33,846 assets) sit at `needs_tag=true` indefinitely** — because the fast-ingest /
  caption-backfill split requires a human to REMEMBER to run the backfill. ← the real cost of
  two-pass without an orchestrator.
- 415,887 files hashed across 38 game-project dirs; 39,917 matched by content hash to shipped code.
- 74,639 assets → 12,489 variation-group families.
- BLIP batched: **450ms/file** @ batch_size=16 (vs 1048ms unbatched) = **87% of per-file time**.
- Embedding alone: **201ms/file**.
- Strong VLM: 2.81s → **2.01s/request** after resizing images to 512px.
- Concurrency: measured 8.85x speedup at n=32, BUT sustained 16-way over 2.7h degraded the
  endpoint 1.58→0.54 req/s with **38.7% errors** and eventual unresponsiveness. Settled on
  **concurrency=6 + circuit breaker**, clean at 0.87 req/s for a 6,354-item run.
- A **9-hour BLIP backfill crashed at 52%** (36,400/70,310) on an uncaught IndexError in
  `_extract_obj_texture` — nobody noticed until the next session.

### Verbatim prompts (reusable)
Image (`strong_vlm.py:30-35`):
> "Identify this game asset precisely: specific subject/model if recognizable, art style/medium
> (realistic photo, painted, cel-shaded, pixel art, etc.), dominant colors, and any notable
> markings or emblems. Respond as JSON only: {"tags": ["..."], "description": "..."}"

Audio (`strong_audio.py:25-29`):
> "Listen to this audio and describe it precisely: mood, genre, instrumentation, and what kind
> of game scene or moment it would fit. Respond as JSON only: {"tags": ["..."], "description": "..."}"

### Endpoints actually used
- Strong audio: `google/gemma-4-E4B-it` via vLLM at **192.168.1.133:8010**
- (this session) **192.168.1.133:8000** serves `qwen36-nvfp4` via vLLM, max_model_len 131072, /health 200
- Transport quirk: strong_audio uses `audio_url` + `data:` base64, NOT OpenAI's `input_audio`
  type — the real decoder rejected the standard shape.

### PROVENANCE — essentially absent (the crux)
- `render_source` / `audio_strong_source` hold the CALL SITE only: `'mcp_reidentify'`,
  `'cli_reidentify'`, `'ui_regenerate'`. Never the model.
- **No model name, no model version, no generation timestamp, no confidence, no staleness
  concept.** `GAMELIB_STRONG_VLM_MODEL` is read at call time and never persisted.
- Point the env var at a new model, rerun reidentify → silently overwrites with no record.
- `needs_tag` bool is the only staleness-ish signal, and means "never captioned", not "captioned
  by a superseded model".
- Each reidentify OVERWRITES the previous strong result — no history.

### Quality failures worth designing against
- **GLB texture-atlas bug**: `_proxy_image_for` prefers a GLB's embedded texture atlas over a real
  render, so the captioner describes UV-unwrapped noise, not the model ("a large pile of green and
  yellow trash" for 3 storage tanks). Fixed by hand for one 55-asset batch; priority order in code
  UNCHANGED → still silently corrupting captions corpus-wide, blast radius unmeasured.
- Sprite sheets captioned as one blob; a regex strips BLIP's "sprite sheet"/"frames of" boilerplate.
- Sub-2s audio: both CLAP and the strong model were confidently wrong (a chest-open one-shot →
  "sci-fi boss battle music"). Gate falls back to humanized filename.
- PBR maps skip captioning — BLIP hallucinates on noise textures.

### Derivation stack (heavier than expected)
3D mesh → proxy image chain: extract embedded texture atlas, else render via **headless Chromium
(Playwright) + Google `model-viewer` + a local ThreadingHTTPServer**; FBX/OBJ first shelled to
`assimp export` (subprocess) with a hand-written GLB texture re-embedding fix for an assimp bug.
Three separate local ML loads (BLIP/transformers, CLAP/transformers, CLIP/sentence-transformers),
each its own lru_cache singleton, no shared abstraction.

### Other drift/pain
- 13 one-off scripts in `scripts/`; at least one (`reidentify_wired_assets_v2.py`) never left a
  session scratchpad and is now lost.
- Config split across env vars AND `runtime_config.json` + a Web Settings UI — two places per knob.
- `AGENTS.md` vs `mcp/server.py` disagree on agent access scope (docs not updated in lockstep).
- Code is NOT sloppy: zero TODO/FIXME/HACK, and 5 deliberate `ponytail:` debt markers.
