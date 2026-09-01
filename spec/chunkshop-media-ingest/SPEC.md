---
slug: chunkshop-media-ingest
input: "chunkshop media ingest: vision/audio/video -> text -> chunk/embed, with first-class metadata-vs-content tracking (code mode: /home/yonk/yonk-tools/chunkshop)"
intensity: standard
updated: 2026-09-01
phases:
  frame: confirmed
  why: confirmed
  users: confirmed
  success: confirmed
  compete: confirmed
  vision: confirmed
  features: confirmed
  design: confirmed
  stack: confirmed
  architecture: confirmed
  personas: confirmed
  synthesize: confirmed
---

# SPEC — <project>

> The authoritative spec. A coding agent should be able to build from this file
> alone; `evidence/` holds the supporting research and consults.

## Overview / Current State
<!-- phase: frame -->

**Premise.** chunkshop becomes the universal library/tool for one shape:
**select a file → convert to text → create embeddings → extract metadata → search enabled.**
It already owns four of those five steps for text formats. This spec adds the missing one:
converting *non-text* bytes — images, audio, video — into text, with the derived text and the
hard technical metadata tracked as separate first-class things.

This is **not** a port of gamelib. gamelib keeps its 113,973-asset index and its specialist
CLIP/CLAP + RRF search. What chunkshop takes is the *shape*, generalized, so any corpus —
game assets, recorded client meetings, anything else — gets it from one tool instead of a
bespoke reimplementation per project.

### What exists today (code mode, verified 2026-09-01)

| Shape step | chunkshop stage | Status |
|---|---|---|
| select a file | `Source` | ✅ `files`, `s3`, `http`, `pg_table`, `sqlite_table`, `mariadb_table`, `clickhouse_table`, `session_staging`, `json_corpus`, `comment_extracts`, `inline`, `connector` |
| convert to text | `FileParser` registry | ⚠️ **text/pdf/docx/pptx/xlsx/html only — ZERO media** |
| create embeddings | `Embedder` | ✅ `fastembed` (local ONNX int8) \| `openai` (HTTP) |
| extract metadata | `Extractor` | ✅ 11 types, `ExtractResult(tags, metadata)`, chunker-wins merge |
| search enabled | `Sink` + `chunkshop search` | ✅ pgvector + FTS hybrid, `promote_metadata` → indexed columns |

**The gap, precisely:**
- `sources/files.py:130` dispatches `get_parser(ext)`; `sources/parsers/__init__.py` has no media entries.
- `FileParser.parse(path) -> str` (`sources/parsers/base.py:16`) — **a parser has no channel to emit
  metadata**. This is the single blocking defect for the "metadata AND content" requirement.
- `Document.content: str`; there is **no bytes field anywhere** in the model (`sources/base.py:23`).
- Nothing caches derived text — every changed file re-parses from scratch. Fine for a PDF,
  fatal for a 90-minute recording or 40k sprites.
- Zero media handling exists: no PIL, ffmpeg, whisper, vision, or mimetype code (grep-verified).

### The two driving corpora
1. **Game assets** — sprites, meshes, audio, textures scattered across ~10 active game-dev
   projects. Stated pain: cannot find the right asset when needed.
2. **Client meeting recordings** — consulting audio that must land in a searchable knowledge base.

### Verified environment
- Test endpoint **`192.168.1.133:8000`** = `qwen36-nvfp4` on vLLM 0.27.2, 131k context, `/health` 200.
  **Vision-capable**, honors `response_format: json_schema`.
  ⚠️ It is a *reasoning* model: at defaults it burns the whole budget thinking and returns
  `content: None` (7.6s, `finish_reason: length`). With
  `chat_template_kwargs: {enable_thinking: false}` → **1.17s, clean schema-valid JSON, 105 tokens.**
  A naive client reading `choices[0].message.content` silently writes empty captions.
- No local vision/ASR server otherwise (no ollama, whisper, or llama-server).
- gamelib historically used `192.168.1.133:8010` (`google/gemma-4-E4B-it`) for audio.

## Problem & Why
<!-- phase: why -->

### The motivation chain (dug to bedrock)

1. **Why build this?** chunkshop should be the best open-source library for RAG / embed / search —
   adoptable by hundreds or thousands of people, not a personal tool.
2. **Why does that require media?** Everyone in the owner's field is working with multi-modal
   files, then feeding them into graphs and agentic operations. A text-only ingest library is
   not a credible general-purpose RAG library in that landscape.
3. **Why does that matter enough to build?** Two returns, one external and one internal:
   - **External:** media is the **wedge that earns evaluation**. See the table-stakes test below.
   - **Internal:** it unblocks the owner's own downstream projects — `pg-raggraph` (graph) and
     agentic ops (`stele`) — which cannot consume what chunkshop cannot ingest.
4. **Bedrock:** this is a strategic bet on positioning, not a bug fix. It is falsifiable
   (phase 5 tests it against real comparables) and it is not a deferral to another "because".

### Table-stakes test — ANSWERED: (b), a differentiator

The owner was asked whether people evaluating chunkshop today walk away because:
- **(a) it failed a checklist** — "text only? next." Media is the price of being considered.
  → would demand a BROAD v1: images + audio + video, adequate at all three.
- **(b) they never got far enough to care** — nobody rejects chunkshop over media, because
  chunkshop isn't in the evaluation set yet. Media is how it *earns* attention.
  → demands a NARROW, EXCELLENT v1: one modality, visibly better than the alternatives.

**Answer: (b).** v1 is scoped narrow and deep, not broad and adequate. The specific modality
is chosen in phase 5 (Compete) / phase 7 (Features), on evidence of where chunkshop can be
demonstrably better — not by covering the most formats.

### The secondary (real, but not primary) pain

Rebuild cost is genuine and evidenced, it is simply not the driver:
- gamelib accumulated **13 one-off backfill scripts**; at least one (`reidentify_wired_assets_v2.py`)
  never left a session scratchpad and is now lost.
- **~30% of 113,973 assets (33,846) sit permanently at `needs_tag=true`**, because the
  fast-ingest / caption-backfill split relies on a human remembering to run the backfill.
- A **9-hour captioning run crashed at 52%** on an uncaught `IndexError` and nobody noticed
  until the next session.
- Two reactive drop-and-recreate migrations, because a Postgres generated column cannot be
  ALTERed in place when a new model tier is bolted on.

These are the failure modes v1 must not reproduce — they are acceptance-check material
(phase 7), not the reason to build.

### Consequence of the "why" that binds later phases

If **adoption** is the driver, then **"point it at your own vLLM box" is not a viable default.**
The owner's `192.168.1.133:8000` is a luxury most adopters lack. This inverts a conclusion both
phase-1 consults reached (they reasoned from the owner's model-free environment and concluded a
cheap local tier was moot): for an OSS audience the zero-key, zero-GPU path is the **most**
important one, because it governs install-to-first-success. It also raises the weight of the
Rust/Go parity constraint — an HTTP boundary ports across implementations; a Python-only
`transformers` dependency does not.

## Users & Use Cases
<!-- phase: users -->

chunkshop is a **library/tool for builders**, not an end-user product. That boundary is the
decision here: two primary users build ON chunkshop; two other populations are served by
*products* that in turn depend on chunkshop, and are therefore explicitly NOT chunkshop's users.

### Primary — Job-A: the RAG builder
Building retrieval over a corpus that is not clean text — support tickets with screenshots,
product photos, PDFs that are 40% diagrams, recorded calls.
- **Job hired for:** "ingest my mixed corpus without me writing a preprocessing script per file type."
- **Today instead:** Unstructured.io, LlamaIndex readers, or a bespoke per-type script; images are
  usually dropped on the floor.
- **Wedge:** images become searchable with **no API key and no GPU** (see `evidence/clip-already-installed.md`).

### Primary — Job-B: the agentic-ops builder
Building an agent that must remember and retrieve across everything it has seen.
- **Job hired for:** "be the ingest layer under my agent's memory / graph."
- **Today instead:** rolls their own, or base64s images into a context window and prays.
- **Downstream consumers in this repo's own orbit:** `pg-raggraph` (graph), `stele` (agent memory) —
  neither can consume what chunkshop cannot ingest.

### Canonical scenario — the heterogeneous-corpus search (owner's words, verbatim intent)

> "I am an insurance company. I have claims, I have call transcripts, I have emails, I have PDF
> bills, I have images. When I am searching for something about previous claims or common issues,
> how do I **search across all of them**? Then use that in my ops."

Five modalities, five different derivation paths, **one ranked result set**, feeding downstream
operations. This is the shape v1 is designed against. Note what is hard here: not any single
modality, but that one query must fuse results derived five different ways.

**Demand signal:** few in the Postgres space are doing "RAG-graph" over a multi-modal mix; several
are asking for it. This is an emerging field, not a crowded one.

### NOT chunkshop's users (they use products BUILT ON chunkshop)
- **Knowledge worker with meeting recordings** — served by a product that embeds chunkshop.
- **Creative-asset owner (game dev, video producer)** — served by a product that embeds chunkshop;
  `gamelib` is exactly such a product and stays as-is.

**Consequence for scope:** v1 optimises the *library/config* surface (YAML, protocols, CLI), not an
end-user UI, curation workflow, or manifest system. gamelib's `curate`/`manifest`/`where_is`
features are product concerns and belong in the Won't column.

## Success Criteria
<!-- phase: success -->

All four outcome families are **critical**. **A is the headline** (it earns evaluation);
**B is the gate** (it stops A from being a lie). Every SC below names an observable signal.

### A — Time-to-first-success (headline)
| ID | Criterion | Observable signal |
|---|---|---|
| **SC-1** | On a clean machine with **no API key, no GPU, and NO MEDIA EXTRA** (`pip install chunkshop[sqlite]` — a storage backend only; `sqlite-vec` is required by any sqlite target and is not a media dependency), point the *documented quickstart YAML* at a folder of images → a text query returns the correct image in **under 5 min** including lazy model download. **The tested path is F22 CLIP zero-shot tags (keyless, base install).** OCR/ASR/VLM are documented upgrades and are explicitly NOT part of this claim. | Scripted timing run in a fresh container running **the exact YAML printed in the README**, asserted in CI. Fails if it needs a key, a GPU, ANY media extra (rapidocr/faster-whisper/torch/av), or >5 min. |
| **SC-2** | The zero-config path requires **no model/endpoint choice** from the user — media works by default. | The quickstart YAML contains no `base_url`, no `api_key_env`, no model name. |

### B — Retrieval quality (gate)
| ID | Criterion | Observable signal |
|---|---|---|
| **SC-3** | On a labelled real-image corpus, cross-modal retrieval **matches or beats gamelib's own search** on a shared query set. gamelib is the baseline because it exists, is tuned, and has `scripts/search_eval.py` already. | Bakeoff report: NDCG@10 + recall@10, chunkshop ≥ gamelib. Uses the existing factorial-bakeoff/`llm-judge` harness. |
| **SC-4** | chunkshop can **bake off media derivation strategies on the user's own corpus** and report a ranked winner with numbers: CLIP-only vs caption-only vs caption+CLIP vs OCR vs late-interaction, across models. The answer is corpus-dependent by design; producing it is the feature, not a one-time validation. | `chunkshop bakeoff` emits a comparison table (NDCG@10/recall@10 per arm) over a labelled query set, on any corpus, reproducibly. |

### C — Reliability at scale (must not reproduce gamelib's failures)
| ID | Criterion | Observable signal |
|---|---|---|
| **SC-5** | **100% reconciliation.** Every input file is *accounted for*: either indexed, or recorded with an explicit failure reason. No file may be silently absent. (gamelib: ~30% of 113,973 stuck at `needs_tag=true`.) | `count(source files) == count(indexed docs) + count(recorded failures)`, and every failure row names a reason and a rung. ⚠️ *"0 rows lacking an embedding" is NOT an acceptable signal — it is trivially true when failed items never become rows.* |
| **SC-6** | **Crash-resume loses at most the in-flight document.** `kill -9` mid-run, restart the same command → zero re-derivation of already-derived items, zero data loss. (gamelib: a 9-hour run crashed at 52% and was silently lost.) | Integration test: kill at N%, restart, assert derivation-call count ≈ remaining items and final count is complete. |
| **SC-7** | **Derivation is cached on `(content-hash, derivation-hash)`.** Re-chunking, re-embedding, or changing target config re-derives **nothing**; changing the model/prompt/params re-derives **everything**. | Test asserts 0 derivation calls on unchanged re-run, and full re-derive when any derivation input changes. |

### D — Provenance & downstream (the "metadata vs content" requirement)
| ID | Criterion | Observable signal |
|---|---|---|
| **SC-8** | Every derived field carries **model id, model version, timestamp, and params/prompt hash**. Nothing is written that cannot be attributed. (gamelib stores only a call-site string — `'mcp_reidentify'` — never the model.) | Schema assertion + a test that two different models produce distinguishable provenance on the same asset. |
| **SC-9** | A stronger/newer model's output **never silently overwrites** a prior derivation; superseding is explicit and detectable. | Test: re-derive with a different model → prior value still recoverable or explicitly versioned; staleness is queryable. |
| **SC-10** | Derived **content** and derived **metadata** are separately addressable: content flows to `original_content`/embedding; metadata is promotable to indexed SQL columns without a destructive migration. | `promote_metadata` lifts a media field (e.g. `duration_s`, `art_style`) to a real column via `ADD COLUMN IF NOT EXISTS` in append mode — no drop-and-recreate. |
| **SC-11** | `pg-raggraph` and `stele` consume media-derived rows through the **same interface as text**, with no changes on their side — **and can distinguish authored from derived content in the query they already run** (F23). | A concrete integration test, written in this project, ingests a mixed corpus (email + scanned PDF + image; **+ recording once F7 lands in v1.1**) into one table, then runs each downstream tool's actual read path against it unmodified, asserting: rows return, `content_origin` is queryable without a join, and provenance is reachable in the same SELECT. **Not satisfied by design assertion.** |
| **SC-13** | **Text-only users are unaffected.** No new required dependency, no model download at install, no startup cost, and existing text YAMLs produce byte-identical output after this ships. | CI job installs base `chunkshop` (no extras), runs the existing sample cells, asserts identical vectors/rows vs the pre-feature baseline, asserts no media package is importable, **and asserts a third-party parser written against the old `parse(path) -> str` signature still works** (source compatibility, not just output identity). |
| **SC-12** | **Rust parity, two levels.** *Bit-level:* same input text + same model → vectors match within float tolerance. *Contract-level:* same YAML, schema, metadata keys and provenance fields for derivation — engines may differ (Python CTranslate2 vs Rust whisper-rs/sherpa-onnx) and the engine is recorded in provenance. | Cross-impl test asserts vector tolerance on shared text, and schema/metadata-key equality on derived rows; provenance names the engine. |

### Testing requirements
- CI must include the SC-1 clean-container timing run (the headline claim rots silently otherwise).
- The SC-3/SC-4 bakeoff must run on a **real** labelled corpus, not synthetic images.
  The phase-1 CLIP probe used flat colour squares and proved plumbing only, **not quality** —
  see `evidence/clip-already-installed.md`.
- Crash-resume (SC-6) needs a real `kill -9`, not a mocked exception.

### Explicitly out of scope for v1
Curation UI, manifests, `where_is`-style path-history resolution, dedup groups, variation grouping —
all product concerns (phase 3), not library concerns. gamelib keeps them.

## Competitive Landscape
<!-- phase: compete -->

Full detail + citations in `evidence/market-intel.md`. Researched 2026-09-01.

**What the owner observes in the field:** most companies are **rolling their own**, or using
**whatever the agent SDKs give them by default**. No dominant incumbent for this job. Few in the
Postgres space combine RAG-graph with a multi-modal mix; **several are asking for it**.

**What the research confirms:**

| Tool | Images | Free/local image path? | Assembled "folder → search" quickstart? |
|---|---|---|---|
| Haystack | CLIP embedder, first-class | ✅ (extra install) | ✗ headline tutorial leads with GPT-4o + OpenAI key |
| txtai | CLIP index | ✅ | ⭐ **closest existing** — real short tutorial |
| LlamaIndex | CLIP *and* GPT-4V, disconnected | ✅ CLIP path | ✗ wire 2 stores + MultiModalVectorIndex yourself |
| LangChain | OCR loader *or* OpenCLIP embeddings | ✅ OpenCLIP | ✗ hand-assemble |
| Unstructured | OCR default, VLM opt-in. **No CLIP at all** | OCR only | ✗ ETL layer, hands off downstream |
| Docling | VLM captioning only. **No CLIP** | ✅ local Granite/SmolVLM | ✗ converter only. **Best local Whisper story in the field** |
| ColPali / byaldi | late-interaction page images | ✅ no key | needs GPU for practical throughput |
| Ragie / Vectara / Morphik | multimodal | SaaS-first | n/a |

**Three findings that shape the spec:**
1. **The gap is packaging, not capability.** Local keyless CLIP is documented in ≥4 libraries.
   Nobody markets "point at a folder → 5-minute cross-modal search, zero config" as the pitch.
2. **No published head-to-head exists** of CLIP-embed-raw-image vs VLM-caption-then-embed in a
   RAG setting. chunkshop's bakeoff would produce **novel, citable data** — the same move that
   produced its `hierarchy` chunker result.
3. **Provenance is a confirmed field-wide gap.** No Tier-1 or Tier-2 tool visibly records which
   model/version produced a derived caption or embedding, nor has a staleness concept.

### ⚠️ The strongest objection, and the answer

> *"CLIP is bad at exactly the images in real corpora — dense text-in-image, screenshots,
> diagrams. That is why late-interaction and VLM-captioning exist as separate lineages."*

This is **correct, and it directly threatens the canonical scenario**: insurance PDF bills are
scanned documents — CLIP's single whole-image vector cannot read their words, and ViDoRe V3 shows
even the best late-interaction model manages only 63.42 NDCG@10 on document images. A spec whose
claim were "CLIP solves media" would fail its own canonical corpus.

**The answer is that CLIP is the ONBOARDING wedge, not the quality claim, and the two are
sequential stages of adoption rather than competing assertions:**

| Stage | Serves | Mechanism | Claim |
|---|---|---|---|
| **Minute 0-5** | SC-1, SC-2 | CLIP via fastembed, already installed, no key, no GPU | "your images are searchable right now, with zero config" |
| **Day 1+** | SC-3, SC-4 | bakeoff across arms: CLIP / caption / caption+CLIP / OCR / late-interaction | "here is what actually wins **on your corpus**, with numbers" |

Objection E does not kill the wedge — **it is the strongest available argument FOR the bakeoff.**
The field maintains three separate lineages precisely because none wins everywhere. Any library
asserting a single winner is wrong for most corpora. chunkshop's position is to not guess, and it
is the only project positioned to say that credibly because the bakeoff apparatus already exists
and already produced a non-obvious result once.

## Differentiation
<!-- phase: compete -->

### The thesis (owner's, restated)

> **Anyone can embed. chunkshop's job is not embedding.**
> chunkshop is the tool for choosing the most accurate, contextually solid **hybrid search** —
> by metadata extraction, by trying different chunking strategies, by baking off models and
> measuring which actually wins on *your* corpus.

Media is not a new product. It is **the newest axis of the same bakeoff**.

### What this means for media specifically

The differentiating claim is NOT "we embed images with CLIP and no API key." That is a feature,
it is genuinely cheap (see `evidence/clip-already-installed.md`), and a large competitor could
copy it in an afternoon.

The differentiating claim is: **chunkshop tells you HOW to make your media retrievable, with
evidence.** Given a corpus, it can run the arms — CLIP-only, caption-only, caption+CLIP, OCR,
late-interaction page-image — across models, and report which wins on that corpus, with numbers.

This matters because **the right answer is corpus-dependent**:
| Corpus | Likely winner |
|---|---|
| Scanned PDF bills, forms | OCR and/or late-interaction (ColPali-style) |
| Game sprite library | CLIP direct — captions are lossy and slow |
| Meeting recordings | Transcript; audio embeddings add little for knowledge retrieval |
| Product photos + support tickets | Caption + CLIP fused |
A library that hard-codes one winner is wrong for most users. chunkshop's position is to
**not guess**.

### Why this is not copied in a weekend

The CLIP call is trivial. What is not trivial, and is what chunkshop already has:
1. **The bakeoff harness and the discipline of using it** — chunkshop's own factorial bakeoff
   (772-doc corpus, 30 gold questions) is how `hierarchy + int8 bge-base` became the default.
   That is accumulated apparatus plus a culture of not asserting winners.
2. **Hybrid search that fuses heterogeneous legs** — the insurance scenario needs one ranked set
   across five derivation paths.
3. **The boring reliability layer** — provenance-keyed caching, crash-resume, `promote_metadata`
   without destructive migrations, cross-language parity. Nobody is excited to build this, which
   is exactly why it is defensible. gamelib's real scars (30% never processed, a 9-hour run lost
   at 52%, two drop-and-recreate migrations) are the proof that it is load-bearing.
4. **Same table shape for every modality**, so downstream graph/agent tooling (`pg-raggraph`,
   `stele`) does not care where a row came from.


**Counter-argument status: ANSWERED.** See "The strongest objection, and the answer" in
`## Competitive Landscape`. Late-interaction is included as a **bakeoff arm**, not a competitor.

## Product Vision & Principles
<!-- phase: vision -->

### The core principle (owner's words)

> **chunkshop needs to quickly and efficiently consume, process, and store embeddings, summaries,
> and metadata about a multi-modal corpus — doing so with the customer's/user's own setup, knobs,
> and configs.**

Everything below serves that sentence.

### Non-negotiables

**NN-1 — chunkshop never asserts a winner it has not measured.**
No hard-coded "captions beat CLIP," no default that claims to be best. The bakeoff decides,
per corpus. This is the thesis; violating it makes chunkshop just another embedding wrapper.

**NN-2 — the zero-config path never requires a key, a GPU, or a model choice.**
If a capability cannot degrade to keyless-local, it is opt-in and never on the default path.
Serves SC-1/SC-2 and the whole adoption argument.

**NN-3 — nothing derived is written without provenance.**
Model, version, timestamp, params/prompt hash. No exceptions. An unattributed caption is worse
than no caption, because it cannot be invalidated. Also a confirmed field-wide gap
(`evidence/market-intel.md` section D) and therefore free differentiation.

**NN-6 — media is strictly additive; text-only users pay nothing.**
Many adopters want no image, vision, or audio capability at all. For them, nothing may change:
- **No new required dependency.** Media libs live in extras (`chunkshop[vision]`, `[ocr]`, `[audio]`),
  never in `dependencies`. (`fastembed` is already core, so CLIP adds no install weight — but
  Pillow, OCR engines, ffmpeg bindings must be extras.)
- **No model download at install time, ever.** CLIP weights (~600 MB for both towers) download
  **lazily on first use of a media config**, never on `pip install` and never for a text-only run.
- **No import cost, no startup cost, no behaviour change** for an existing text cell. Existing
  YAMLs must produce byte-identical results after this feature ships.
- **A text-only user must never see a media concept** in an error message, a required config key,
  or a log line.

*Tension with NN-2, resolved explicitly:* NN-2's "zero config" means **zero config once you have
opted into media** — point at an image folder and it works, no key, no model choice. It does not
mean media is on by default. Opt-in at the config level, zero-config within that choice.

**NN-4 — defence/offence in depth: the escalation ladder.**
Do the cheapest, easiest thing by default — and be **open and honest that cheapest is often not
most accurate**. Every step up in accuracy is a documented, user-chosen config rung, never a
silent default. **Precedent in this codebase:** `lede` ships a deterministic, spaCy-free gazetteer
path; `lede-spacy` is an opt-in extra buying better NER for more weight
(`pyproject.toml:34-35`). Media follows the identical pattern.

| Rung | Mechanism | Cost | Default? |
|---|---|---|---|
| 0 | Deterministic — path convention, ffprobe, dimensions, EXIF, filename heuristics | free, exact, hallucination-proof | **always on** |
| 1 | Local ONNX embedding (CLIP via fastembed — already a core dep) | ~30 ms/img, no key, no GPU | **on** |
| 2 | Local OCR — text-in-image, scanned PDFs | ~100s ms, extra install | opt-in |
| 3 | Remote VLM caption / ASR over an OpenAI-compatible endpoint | ~1.2 s + $, needs endpoint | opt-in |
| 4 | Late-interaction page-image retrieval (ColPali-style) | GPU, multi-vector storage | opt-in |
| 5 | **Arbitrary CLI shell-out** | whatever the user wants | escape hatch |

**NN-5 — never a dead end.**
Rung 5 is a principle, not a feature. chunkshop cannot be the best at every derivation for every
corpus, so a user who says *"I get better results from this CLI tool than from your CLIP model"*
must be able to plug it in **without forking chunkshop**. This also resolves the phase-1 consult
split (Fable: subprocess; Codex: in-process HTTP) — they are different rungs of one ladder, not
competing designs.

### What this is explicitly NOT

- **NOT a hosting platform.** chunkshop does not serve models, bundle an inference server, ship a
  managed cloud, or become responsible for anyone's GPU. It *calls* endpoints the user configures.
- **NOT an end-user product.** No curation UI, no manifests, no `where_is` path-history
  resolution, no dedup groups, no variation grouping. Those are product concerns (phase 3);
  `gamelib` keeps them.
- **NOT a cloud-by-default tool.** chunkshop's own docs, README, and quickstarts **never demo a
  public cloud VLM/ASR API key.** Every shipped example points at a local model or a
  user-configured endpoint. Rung 3 is bring-your-own-endpoint in the documentation as well as in
  the code — because every comparable tool's headline tutorial defaults to GPT-4o, and that is
  precisely the habit this project declines to copy.
- **NOT a multi-tenancy or access-control layer.** chunkshop writes to a table the user owns;
  row-level security and tenant isolation belong to the database and the embedding application.
- **NOT a model vendor.** chunkshop ships no model of its own and takes no position on whose is
  best beyond what a bakeoff measures on the user's data.
- **NOT a scheduler.** Per the SP-A design spec, orchestration beyond `chunkshop orchestrate`'s
  subprocess fan-out is an external scheduler's job.

### Known gap surfaced during this phase — silent failure in the canonical corpus

`sources/parsers/pdf.py` is pypdf `extract_text()` only — **no OCR anywhere in chunkshop**:
```python
return "
".join((page.extract_text() or "") for page in reader.pages)
```
A scanned PDF (an insurance bill — the canonical scenario) yields `""`. It does **not** raise:
the `or ""` swallows it, the document chunks into nothing, and it silently disappears from the
index. This is both a v1 feature (rung 2, OCR) and a defect (a silent empty-document "success"
must become a detectable condition). Carried into phase 7.

## Features & Requirements
<!-- phase: features -->

**Criticality test:** a feature is Critical iff at least one Success Criterion or core Job
fails without it. **Scope for v1: IMAGES + the full spine.** Audio (F7, F8) is **v1.1**; video/3D are deferred. See Scope.

| ID | Feature | How (sketch) | Critical? | Serves | Acceptance check | Status |
|----|---------|--------------|-----------|--------|------------------|--------|
| **F1** | `FileParser` → `Deriver`, **backward-compatible** | Return type widens to `str \| ParseResult`; a `str` return is auto-wrapped as `ParseResult(text, {})`. **Third-party parsers written against `parse(path) -> str` keep working.** `FileParser` stays exported as a deprecated alias for ≥1 minor version with a `DeprecationWarning` | ✅ | SC-8, SC-10, SC-13, Job-A | A test defines a parser with the **old** signature, injects it via `FilesSource(parsers=...)`, and it works unchanged **and** emits a `DeprecationWarning`; new-style parsers reach `promote_metadata` | missing |
| **F2** | Image parser — rung 0 + 1 | Registers image extensions; emits deterministic metadata (dimensions, format, mode, EXIF, path-convention segments) with **no model** | ✅ | SC-1, SC-10, NN-4 | Ingest a PNG → row has width/height/format columns, zero network calls, zero model load | missing |
| **F3** | CLIP image embedding (**visual plane, optional**) | `fastembed.ImageEmbedding` (`Qdrant/clip-ViT-B-32-vision`) at ingest; `Qdrant/clip-ViT-B-32-text` for the query side → **`→ Design`** (chunkshop's Embedder embeds chunk *text*; this embeds a *file*) | ✅ | SC-1, SC-3, NN-2 | Text query "a red square" ranks the red image above the blue one on a real labelled set; no API key, no GPU | missing (dep present) |
| **F23** | `content_origin` as a first-class column | every row carries `content_origin` ∈ {`authored`, `derived`} plus `derived_rung`, promoted to real columns **by default** — not buried in jsonb. A VLM hallucination and a verbatim email must not be indistinguishable in shape | ✅ | SC-8, SC-11, NN-3 | A downstream consumer can filter/rank on ground-truth-vs-derived **in the query it already runs**, with no extra join and no opt-in |  missing |
| **F24** | Cost governance for derivation | per-run budget ceiling (`max_spend`/`max_calls`), a cost estimate before a paid run starts, and a `cost` field recorded in provenance | ✅ | SC-4, NN-2 | A bakeoff or ingest with a budget ceiling stops at the ceiling and reports spend; a dry-run prints an estimate before any paid call | missing |
| **F22** | CLIP zero-shot tagging (keyless image→text) | embed a configurable label vocabulary with the CLIP text tower; tag each image by nearest labels; output is **text** into the unified plane | ✅ | SC-1, SC-2, NN-2, NN-4 | A folder of images yields searchable text tags with **no key, no GPU, no new dependency**; vocabulary is user-configurable | missing (dep present) |
| **F4** | OCR — rung 2 | Local OCR engine behind `chunkshop[ocr]`; applies to image files **and** as a fallback when a PDF page yields no embedded text | ✅ | canonical scenario, Job-A | A scanned PDF of known text ingests with that text recoverable in `original_content` | missing |
| **F5** | Empty-derivation is loud, not silent | A parser yielding `""`/no derivation raises or records a failure state; never a silently-indexed empty doc | ✅ | SC-5 | Scanned PDF with no OCR configured → explicit error or recorded `derivation_failed`, **never** a silent 0-chunk success | missing (**active defect**: `parsers/pdf.py` `or ""`) |
| **F6** | Remote VLM caption deriver — rung 3 | OpenAI-compatible `/v1/chat/completions` over stdlib urllib, copying `embedders/openai_provider.py`; `base_url`/`api_key_env`/model from YAML; `response_format: json_schema`; **must handle reasoning models** (`enable_thinking:false`, and `content: None` is an error not an empty caption) | ✅ | SC-4 arm, SC-10, Job-A, NN-4 | Against `192.168.1.133:8000`, an image yields schema-valid JSON split into content + **structured metadata (art_style, dominant_colors, composition) that `promote_metadata` lifts to indexed columns**; a `content: None` response fails loudly | missing |
| F7 | ASR deriver — audio → transcript | Audio file → transcript text + timestamp/duration/speaker metadata. Local (opt-in extra) and remote (rung 3) paths | ⬜ **v1.1** | Job-A, Job-B | A known recording ingests; transcript text searchable; `duration_s` promotable to a column | deferred to v1.1 |
| F8 | Audio pre-segmentation | Long audio split before ASR (API duration/size ceilings), transcript reassembled with inline timestamps → **`→ Design`** (inverts Source→Chunker order) | ⬜ **v1.1** | 90-min meetings | A 90-minute recording ingests end-to-end; chunk metadata carries correct offsets | deferred to v1.1 |
| **F9** | Subprocess deriver — rung 5 | argv template + extension list + timeout; stdout parsed as JSON `{text, metadata}`. chunkshop ships zero model knowledge on this rung | ✅ | NN-5, Job-A | A trivial shell script standing in for a deriver produces an indexed doc; a non-zero exit fails loudly | missing |
| **F10** | Derivation cache | Memo keyed on **`(content-hash, derivation-hash)`**, where derivation-hash covers model + params + prompt + argv | ✅ | SC-6, SC-7 | Unchanged re-run → **0** derivation calls. Change the prompt → full re-derive. Change only the target table → 0 re-derives | missing |
| **F11** | Provenance stamping | Every derived field carries model id, model version, timestamp, params/prompt hash. Namespaced (`derived.<tier>.*`) | ✅ | SC-8, NN-3 | Two different models on the same asset produce distinguishable provenance; nothing derived lacks it | missing |
| **F12** | Supersession + staleness | A newer/stronger derivation never silently overwrites; superseded values remain recoverable and staleness is queryable | ✅ | SC-9, NN-3 | Re-derive with a different model → prior value recoverable; "which rows are stale vs model X" is a query | missing |
| **F13** | Media arms in the bakeoff | Extend the existing bakeoff to media strategies: CLIP-only / caption-only / caption+CLIP / OCR / late-interaction, across models | ✅ | SC-3, SC-4, NN-1 | `chunkshop bakeoff` emits NDCG@10 + recall@10 per arm on a labelled corpus, reproducibly | partial (text bakeoff exists) |
| **F14** | Extras packaging + lazy model load | `chunkshop[vision]`, `[ocr]`, `[audio]`; **no** media dep in core; weights download on first *use*, never at install or import | ✅ | SC-13, NN-6 | Base install runs existing sample cells with byte-identical output; no media package importable; no download triggered | missing |
| F15 | Cross-plane search fusion (RRF across cells) | weighted RRF over per-cell legs; only needed when the optional visual plane is used | ⬜ **Later** | — (canonical scenario is served by the unified text plane, D1) | — | deferred |
| F16 | Vector-space separation | falls out of one-cell-per-modality for free; no code needed | ⬜ **Later** | — | — | structural |
| **F21** | Two explicit APIs: `Deriver` + `MediaEncoder` | separate protocols on separate stages; `MediaEncoder.encode_query` guarantees same-space queries; two search verbs (`search`, `search --visual/--similar-to`) | ✅ | NN-1, NN-4, Job-A, Job-B | A user cannot accidentally get vector-search when they configured text derivation, or vice versa; `--similar-to` works only on an API-2 table and says so | missing |
| **F17** | Rust parity | Same YAML, same target schema, interchangeable vectors. `fastembed` `image-models` feature is **already enabled** (`rust/chunkshop/Cargo.toml:76`) | ✅ | SC-12 | Cross-impl test: same input + model → vectors match within float tolerance | missing (flag on) |
| F18 | Video ingest | keyframe sampling + audio split + fusion | ⬜ | — | — | deferred |
| F19 | Late-interaction (ColPali) arm | multi-vector page-image retrieval | ⬜ | SC-4 (as an arm) | — | deferred |
| F20 | 3D mesh / proxy-image derivation | render or texture-extract, then image path | ⬜ | — | — | deferred (gamelib has it) |

### Bidirectional traceability check

**SC → Feature (no gaps):**
SC-1→F2,F22,F14 · SC-2→F22,F14 · SC-3→F3,F13,F22 (F16 is NOT required — deferred with F15) · SC-4→F6,F13 · SC-5→F5 · SC-6→F10 ·
SC-7→F10 · SC-8→F1,F11 · SC-9→F12 · SC-10→F1,F2 · SC-11→F23 + D1 (with a real integration test, not asserted-by-design) · SC-12→F17 · SC-13→F14.
Job-A→F1,F4,F6,F7,F9,F21 · Job-B→F7,F11,F21. **No SC or Job is unimplemented.**

**Feature → SC (no gold-plating):** every F1–F17 traces to ≥1 SC or Job. F18–F20 trace to
nothing in v1 scope and are therefore correctly deferred, not built.

### Items flagged `→ Design` (phase 8 must resolve)
- **F3** — an Embedder that embeds a *file*, not chunk text. This breaks the stage contract and is
  the single biggest architectural question in the spec.
- **F8** — segmentation before derivation inverts Source→Chunker ordering.
- ~~F15/F16~~ — **resolved by D1**: derived text goes to the unified text plane, so the canonical
  scenario needs no new retrieval work at all. Fusion is deferred with its design preserved.

## Scope
<!-- phase: features -->

- **In — v1 (critical, images + spine):** F1, F2, F3, F4, F5, F6, F9, F10, F11, F12, F13, F14,
  F17, F21, F22, F23, F24. Seventeen items, of which the majority are the **modality-agnostic
  spine** (provenance, cache, two APIs, packaging, reconciliation, cost governance) — built once,
  reused by every later modality.
- **v1.1 (next, decided not deferred-vague):** F7 ASR + F8 audio segmentation. The stack is already
  chosen (faster-whisper + PyAV, see Tech Stack) and the seam is `Deriver` — audio is **a rung, not
  a rewrite**. This is the owner's own dogfooding corpus, so it should not slip far. Images + audio. The escalation ladder rungs 0/1/2/3/5, the
  provenance+cache layer, bakeoff arms, extras packaging, Rust parity, and enough retrieval
  design that the canonical scenario actually returns one ranked list.
- **Later (could):** local on-box captioning rung (BLIP-class, torch ~2GB — the insurance persona's deferred gap) · `chunkshop bakeoff init` to bootstrap a labelled query set · F15 cross-plane RRF fusion · F16 · F18 video (keyframes + audio fusion) · F19 late-interaction/ColPali arm
  (GPU-bound, but the bakeoff should be able to *include* it) · F20 3D-mesh proxy derivation ·
  audio speaker diarization · image dedup/perceptual hashing.
- **Won't:** model hosting or serving · a bundled inference server · managed cloud · curation UI ·
  manifests · `where_is`/path-history · dedup groups · variation grouping · scheduling beyond
  `orchestrate`'s subprocess fan-out. (Per phase-6 "What this is explicitly NOT".)

## Design — How It Works
<!-- phase: design -->

### D1 — THE UNIFIED TEXT PLANE (primary path)

**Derived text is just text.** An OCR'd bill, a VLM caption, an ASR transcript — once derived,
none of them differ from an email. They go into the **same cell, same table, same embedder, same
search** as every other text document. There is no second vector space, no fusion, and **no new
search machinery**.

```yaml
# ONE cell. Emails, PDFs, scans, recordings, screenshots all land here.
source:
  type: files
  glob: /corpus/**/*
  derive:                       # extension -> rung; all outputs are TEXT
    pdf:            {rung: text}              # pypdf. add text_then_ocr with chunkshop[ocr]
    png|jpg|webp:   {rung: clip_tags}         # KEYLESS, base install, no extra. F22
    # upgrades, each needing an extra or an endpoint:
    #   png|jpg:     {rung: ocr}              -> chunkshop[ocr]      (text-in-image)
    #   png|jpg:     {rung: vlm_caption, ...} -> endpoint            (rich description)
    #   wav|mp3|m4a: {rung: asr}              -> chunkshop[audio]
chunker:  {type: hierarchy}
embedder: {type: fastembed, model: Xenova/bge-base-en-v1.5-int8, dim: 768}
target:   {table: claims, promote_metadata: [source_path, media_type, duration_s, page_count]}
```

**The canonical insurance scenario is fully served by this and nothing else.** Claims, emails,
PDF bills (OCR), call transcripts (ASR), images (OCR/caption) -> one table -> one query -> one
ranked list, using the hybrid search that **already ships**. Zero new retrieval work.

**Consequence:** F15 (cross-modal fusion) and F16 (vector-space separation) drop from **Critical
to Later**. They are only needed if a user adds the optional visual plane below.

---

### D2 — THE OPTIONAL VISUAL PLANE (CLIP), and why it still exists

CLIP is **not** a redundant way to make images searchable. Its job is **visual similarity that
text cannot express**: *"find sprites that look like this one"*, *"which of these 40k assets match
this style"*. No caption answers that at any price.

It is also the answer to a real gap in the ladder — the two corpora need **different keyless paths**:

| Image type | Keyless, no-GPU options | Notes |
|---|---|---|
| **Text-bearing** (scanned bill, screenshot, form) | **OCR** -> text -> unified plane | the words are the content |
| **Not text-bearing** (sprite, photo, texture) | **CLIP zero-shot tags** -> text -> unified plane; **and/or** CLIP vector -> visual plane | see the keyless derive ladder below — an earlier draft wrongly claimed no keyless captioner exists without torch |

**Mechanism:** its own cell, own table, `dim: 512`, via `fastembed.ImageEmbedding`. Queries embed
with the **CLIP text tower** (`Qdrant/clip-ViT-B-32-text`) so text->image retrieval works within one
space. Two spaces are never compared because they are never in the same table.

**When both planes exist**, the collection/RRF design below applies — but that is **Later**, not v1.

<details>
<summary>Deferred design: cross-plane fusion (F15/F16) — preserved for when it is needed</summary>

A *collection* names member cells; a query fans out one leg per cell, each embedding the query with
**that cell's own embedder** (bge-768 for the text plane, CLIP-text-512 for the visual plane), then
fuses by **weighted reciprocal rank** — never by score. Scores are not commensurable: the phase-1
probe measured CLIP similarities at 0.23-0.30 across both a *correct* and an *incorrect* match,
which is meaningless beside a bge cosine. gamelib independently landed on the same answer
(`RRF_K=60`, weighted legs). Metadata filters must AND across every leg, requiring a shared
promoted-column convention (`source_path`, `project`, `created_at`); a cell not promoting a filtered
column is excluded from that leg **loudly**. Per NN-1, weights are bakeoff output, not asserted.
</details>

---

### D2c — The keyless derive ladder for images (corrects an earlier error)

An earlier draft asserted *"no keyless captioner exists without torch."* **That was wrong.**
API 1 has a fully keyless path for non-text-bearing images, using models chunkshop **already ships**.

| Rung | Mechanism | Deps | Cost | Output |
|---|---|---|---|---|
| 0 | **Pillow metadata** — dimensions, format, mode, EXIF, transparency, dominant palette, aspect ratio | **free** (Pillow is a required dep of `fastembed`) | ~ms | exact metadata, hallucination-proof |
| 0 | **Path/filename convention** — `<type>/<style>/<subject>/` (gamelib's trick) | free | ~µs | exact metadata |
| **1** | **CLIP zero-shot tagging** — embed a label vocabulary with the CLIP **text** tower, classify images against it by nearest label | **free** (fastembed, already core) | **~18 ms/img** | **searchable TEXT tags** |
| 2 | **OCR** (RapidOCR, ONNX) | `chunkshop[ocr]` | ~100s ms | text-in-image |
| 2 | Object detection (YOLO-family ONNX weights via `onnxruntime` directly) | ONNX weights only | ~10s ms | object labels as text |
| 3 | BLIP-style local captioning | torch + transformers (~2 GB) | ~450 ms | free-form caption |
| 3 | Remote VLM captioning | endpoint + key | ~1.2 s + $ | richest caption |

**Rung 1 verified on this box** (3/3 correct, clean margins, 18 ms/img, zero new dependencies):
```
gradient_sky  -> 'a blue gradient sky'                    0.282  (2nd 0.239)
checkerboard  -> 'a black and white checkerboard pattern' 0.361  (2nd 0.226)
red_circle    -> 'a red circle on a white background'     0.319  (2nd 0.229)
```
This is gamelib's CLAP-vocabulary trick (`audio_embed.py:27-43`, a 40-term vocabulary) applied to
images — except the model is already a chunkshop dependency, so it costs nothing.

**Why this matters to the spec:** SC-1's "5 minutes, no key, no GPU" is now satisfiable
**entirely inside the unified text plane**, for *any* image type. The visual plane (API 2) is no
longer required for the wedge — it narrows to its true job: **perceptual** similarity.

⚠️ **Licensing note on object detection:** the `ultralytics` package is **AGPL-3.0** and must never
be a default or a required extra. YOLO-family **ONNX weights** loaded directly through
`onnxruntime` avoid that package entirely — check each specific checkpoint's own licence.

⚠️ **Open question for the bakeoff (NN-1 — do not assert):** the label vocabulary is the whole
game for rung 1. A generic vocabulary produces generic tags. Vocabulary should be user-configurable
and, ideally, derivable from the corpus. gamelib hard-coded 40 audio terms and then needed
`scripts/redescribe_sfx_batch.py` when that vocabulary changed — a re-derivation the cache
(F10) would have made cheap, and a mistake worth not repeating.

---

### D2d — Structured derivation captures most of what "perceptual" meant

A VLM prompt that explicitly asks for **palette, style, colors, and composition** recovers much of
what a CLIP pixel-vector encodes — and with `response_format: json_schema` it returns them as
**structured fields**, not prose:

```yaml
derive:
  png|jpg:
    rung: vlm_caption
    schema: {description: str, art_style: str, dominant_colors: [str],
             composition: str, subject: str, tags: [str]}
```
```sql
-- promote_metadata lifts them into real indexed columns:
WHERE art_style = 'cel-shaded' AND 'red' = ANY(dominant_colors)   -- exact + filterable
```

**This is strictly better than a vector for that query class.** A CLIP similarity search for
"cel-shaded red things" is fuzzy and unranked-by-attribute; a promoted column is exact, indexable,
and composable with every other filter. gamelib's own strong-VLM prompt already asks for exactly
these fields (`strong_vlm.py:30-35`) — it just stored the answer as prose in a text column instead
of as structured, promotable metadata.

### What is genuinely left for API 2 (CLIP), after D2c and D2d

Honest accounting — the visual plane's unique territory is narrower than the earlier draft implied:

| Capability | Covered by derive? | Only CLIP? |
|---|---|---|
| Conceptual "more like this" | ✅ description-space, 0.88 vs 0.52 measured | |
| Style / palette / composition match | ✅ **as filterable columns**, often better | |
| Text-bearing images | ✅ OCR | |
| Keyless tagging of any image | ✅ zero-shot, 18 ms | |
| **Reference-image query with no words** ("find more like *this file*", user cannot describe it) | ❌ | ✅ |
| **Near-duplicate / exact-visual match** (same asset re-exported, crop, recolor) | ❌ prose collapses these | ✅ |
| **Detail no prose captures** (fine texture, exact geometry) | ❌ | ✅ |
| **Cost** | VLM ~1.2 s + $/img | **~30 ms, free, keyless** |
| **No prompt design required** | ❌ caption quality is prompt-dependent | ✅ |

**F3 stays Critical** for three reasons: it is nearly free to build (dependency already present),
it is the **only** reference-image path, and **SC-3 compares against gamelib — whose search fuses a
CLIP leg** — so matching it plausibly requires one. But its pitch is now precise: *perceptual*
similarity and zero-cost scale, not "how images become searchable."

---

### D2b — F3: how an image gets a vector (visual plane only)

**One cell per modality.** No change to the five-stage contract: the visual plane is its own YAML
with `embedder.type: clip, dim: 512`, its own table, run alongside the text cell by
`chunkshop orchestrate`.

**Alternatives rejected.** *(A)* an `ImageEmbedder` reading a path out of chunk metadata — breaks
the invariant that `embedded_content` is what was embedded, load-bearing for debugging today.
*(B)* parser returns a precomputed vector with a `passthrough` embedder — puts embedding logic in
the parser stage for one case.

**Open question.** `chunker: whole_document` (one chunk per image) may not exist; if absent it is
~20 lines.

---

### D6 — TWO EXPLICIT APIs, never one that guesses

The two planes are **separate call surfaces**, deliberately chosen by the user. They are not two
modes of one API and chunkshop must never silently pick between them: their outputs, guarantees,
costs, and even their *pipeline stage* differ.

| | **API 1 — `derive`** (media → text → embed → search) | **API 2 — `encode`** (media → embed → search) |
|---|---|---|
| Stage | **parser/deriver** (pre-Chunker) | **embedder** |
| Output | text + metadata | vector + metadata |
| Lands in | the **unified text plane** (D1), same table as email/PDF | its **own** table, `dim: 512` |
| Greppable / quotable / auditable | ✅ yes | ❌ no — a vector cannot be read |
| Feeds an LLM, graph, or agent | ✅ yes | ❌ no |
| **"more like this"** | ✅ **description-space** (conceptual): embed one item's derived text, find nearest. Measured: query *"a cartoon cat who is black"* → 0.879 / 0.858 for two other black cartoon cats vs 0.524 for unrelated | ✅ **pixel-space** (perceptual): style, palette, composition, exact-look matching |
| text → image search | ✅ via the derived text | ✅ via the CLIP text tower |
| Keyless / no-GPU | OCR ✅ · ASR ✅ · **CLIP zero-shot tags ✅** · PIL metadata ✅ · VLM caption ❌ | ✅ always |
| Cost per image | OCR ~100s ms · VLM ~1.2 s + $ | **~30 ms** |
| Provenance needs | model + version + **prompt** + params | model + version |
| Fails when | image has no text (OCR) / no endpoint (VLM) | image content is textual (CLIP cannot read) |

#### API 1 — `Deriver` protocol (media → text)

```python
class Deriver(Protocol):
    supported_extensions: list[str]
    def derive(self, path: Path) -> ParseResult: ...   # ParseResult(text, metadata)
```
Config lives on the **source**, keyed by extension → rung:
```yaml
source:
  type: files
  glob: /corpus/**/*
  derive:
    pdf:          {rung: text_then_ocr}
    png|jpg:      {rung: ocr}
    wav|mp3|m4a:  {rung: asr, segment_seconds: 600}
    psd:          {rung: subprocess, argv: ["my-tool", "--json", "{path}"]}
```
This is `FileParser` generalised (F1) — the existing pdf/docx/xlsx/html parsers **are** derivers at
rung 0. Nothing new conceptually; media just adds rungs.

#### API 2 — `MediaEncoder` protocol (media → vector)

```python
class MediaEncoder(Protocol):
    def encode_media(self, paths: list[Path]) -> list[Vector]: ...   # ingest side
    def encode_query(self, texts: list[str]) -> list[Vector]: ...    # query side, SAME space
```
Config lives on the **embedder**, in its own cell:
```yaml
source:   {type: files, glob: /assets/**/*.png}
chunker:  {type: whole_document}
embedder: {type: clip, model: Qdrant/clip-ViT-B-32-vision,
           query_model: Qdrant/clip-ViT-B-32-text, dim: 512}
target:   {table: assets_visual}
```
**`encode_query` is what makes the API honest** — it guarantees the query is embedded into the
*same* space as the media, which is precisely the invariant a single blurred API would lose.

#### Search surface — also two verbs

```bash
chunkshop search "goblin warrior sprite"                    # text plane (unchanged, ships today)
chunkshop search "goblin warrior sprite" --visual assets_visual   # CLIP text tower -> image vectors
chunkshop search --similar-to ./ref.png  --visual assets_visual   # image -> image; API 2 only
```

**Why two APIs and not one.** A single `media:` config that "does the right thing" would have to
choose between OCR-ing a sprite (returns noise) and CLIP-ing a scanned bill (cannot read it) — and
would hide from the user which guarantee they actually got. Per NN-1 (never assert an unmeasured
winner) and NN-4 (the ladder is explicit, not silent), the choice is the user's, the bakeoff
informs it, and both may run over the same corpus as separate cells.

---

### D3 — F8: long audio, segmented before transcription

**Mechanism — segmentation is internal to the ASR deriver.** The audio parser splits on
duration/size ceilings, transcribes each segment, and **reassembles ONE document** whose text
carries inline `[HH:MM:SS]` markers. `ParseResult.metadata` additionally carries a
`segments: [{char_offset, t_start, t_end}]` map. chunkshop's normal Chunker then chunks that
transcript with no special casing, and an extractor maps each chunk's start offset through the
segment map into `metadata.t_start` — so **"jump to 14:32 in the recording" works** without the
pipeline ever knowing audio existed.

**Why chosen.** It keeps stage order intact: one recording = one `Document`, which preserves
document identity, fingerprinting, and the incremental cursor. Fable's framing is right — chunking
for an ASR API's 25MB/duration ceiling is an implementation detail *of transcription*, not a
pipeline stage.

**Alternative considered.** *Emit N Documents, one per segment.* Rejected — one recording becomes
N doc_ids, breaking identity, dedup, and "show me the whole meeting".

**Open question.** Segment boundaries should prefer silence over hard time cuts to avoid splitting
mid-word; whether that needs an audio dependency (VAD) or a crude fixed overlap is a build-time call.

---

### D4 — F6: remote VLM calls (gotchas already measured)

**Mechanism.** Copy `embedders/openai_provider.py` verbatim in shape: stdlib `urllib`, no new
dependency, `base_url` + `api_key_env` + model from YAML, exponential backoff on
`{429,500,502,503,504}` + `URLError`, per-call timeout.

**Three measured gotchas that must be in the implementation, not discovered later:**
1. **Reasoning models silently return nothing.** Verified against `192.168.1.133:8000`
   (`qwen36-nvfp4`): at defaults, 2,391 chars of reasoning, `content: None`,
   `finish_reason: length` — **even with `response_format: json_schema` and 600 max_tokens.**
   With `chat_template_kwargs: {enable_thinking: false}` → 1.17s, clean schema-valid JSON, 105
   tokens. **A `content: None` response MUST raise, never write an empty caption** (this is the
   same class of bug as F5's `or ""`).
2. **Concurrency melts endpoints.** gamelib measured 8.85× speedup at n=32, but sustained 16-way
   over 2.7h degraded a vLLM endpoint from 1.58 to 0.54 req/s with **38.7% errors**. Ship a
   conservative default (~6) **and a circuit breaker**, not just a worker count.
3. **Resize before send.** gamelib measured 2.81s → 2.01s per request from a 512px downscale alone.

**Audio transport quirk:** gamelib's strong-audio path had to use `audio_url` + a `data:` base64
URI, **not** OpenAI's `input_audio` type, because the real vLLM decoder rejected the standard
shape. Transport must be configurable, not hard-coded to the spec-blessed form.

---

### D5 — F10/F11/F12: cache, provenance, supersession are one mechanism

**Mechanism.** The cache key **is** the provenance record:
`key = H(content_hash, derivation_hash)` where `derivation_hash = H(rung, model_id, model_version,
params, prompt, argv)`. Stored alongside every derived artefact and stamped into
`metadata.derived.<tier>.*`.

Consequences that fall out for free:
- Re-chunk / re-embed / change target → **0** re-derivations (key unchanged).
- Change model or prompt → **full** re-derive (key changed) — no stale captions, ever.
- **Supersession is automatic**: a different model is a different key, so the prior derivation is
  not overwritten but co-exists. F12's "which rows are stale vs model X" becomes a key query.
- Crash-resume (SC-6) is "run it again" — cache hits skip completed work, and the sink already
  commits per document.

**Where it lives — inside the deriver, not `FilesSource`.** ~30 lines checking a cache dir before
work and writing after. No `RawStore` wiring in v1 (`RawStore` remains the later swap if
S3-backed caching is ever wanted). This is Fable's correction to an earlier, larger design and it
is accepted.

**Cache security, retention, and versioning (was missing — added after persona review):**
- The cache stores **raw OCR'd claim text and call transcripts on local disk**. For the canonical
  insurance corpus that is a compliance surface, not a convenience. v1 must ship: a configurable
  cache root (so it can sit on an encrypted volume), a documented **retention/TTL**, a
  `chunkshop cache purge` command, and an explicit **opt-out** (`cache: false`) for corpora that
  must not persist derived text outside the target database.
- **No redaction and no encryption-at-rest in v1** — stated as a limitation, not implied to be solved.
- **Cache schema version** is part of the key: `key = H(cache_schema_version, content_hash,
  derivation_hash)`. If the `derivation_hash` formula itself changes in a later release, the
  version bump invalidates cleanly instead of silently serving mismatched entries.
- Size: 40k captions is real disk. Document expected footprint per 10k items.

## Tech Stack
<!-- phase: stack -->

Researched 2026-09-01. Constraint order: (1) keyless + no GPU on the default path, (2) no new
**required** dependency — extras only, (3) pip-installable over system binaries, (4) plausible Rust
equivalent, (5) permissive licence.

### Already free (no new dependency at all)

| Need | Provided by | Note |
|---|---|---|
| ONNX inference | `onnxruntime` 1.24.4 | **required dep of `fastembed`**, which is core to chunkshop |
| Image decode/preprocess | `Pillow` 12.3.0 | **required dep of `fastembed`** — verified in its `pyproject.toml`, not from memory |
| CLIP image + text towers | `fastembed.ImageEmbedding` / `TextEmbedding` | `Qdrant/clip-ViT-B-32-{vision,text}`, 512-d, ~0.6 GB lazy download |
| Remote HTTP calls | stdlib `urllib` | copy `embedders/openai_provider.py`'s retry/backoff shape |
| Vector storage, hybrid search, promote columns, orchestrate | chunkshop itself | unchanged |

**Consequence:** the entire **API-2 (visual) path costs zero install weight.** Only OCR and ASR
need extras.

### Decisions

**OCR → `RapidOCR`** (`chunkshop[ocr]`)
- Runs on **ONNX Runtime — the engine chunkshop already ships**. No torch, no PaddlePaddle, no
  `apt install`. pip-only. Apache 2.0. PP-OCR-family accuracy without Paddle's dependency tax.
- *Runner-up:* **Tesseract/pytesseract** — tiny, decades-hardened, but needs a **system binary**
  (`apt install tesseract-ocr`), which is a real adoption tax against NN-2. If offered, it must be
  documented as a system prerequisite, never hidden behind `pip install`.
- **REJECTED — Surya:** code Apache 2.0 but **model weights** are modified OpenRAIL-M /
  CC-BY-NC-SA-4.0: free only under $2M revenue **and** $2M lifetime funding, and must not compete
  with Datalab's API. Unacceptable to push onto unknown downstream adopters.
- **REJECTED — EasyOCR** (torch, ~500 MB), **docTR** (torch/TF; its `onnxtr` sibling is a possible
  future arm), **PaddleOCR** (PaddlePaddle framework), **olmOCR** (7B VLM, **GPU required** —
  disqualified by constraint 1).
- *Rust:* no dedicated crate, but PP-OCR ONNX models load directly via the `ort` crate.

**ASR → `faster-whisper`** (`chunkshop[audio]`)
- MIT. pip-only. **No torch. No system ffmpeg** (bundles PyAV). CPU int8 ≈ **6.4× real-time**
  (13 min audio in ~2m04s, 8 threads). **Native word + segment timestamps** and built-in Silero
  **VAD segmentation** — which is exactly F8's requirement, so segmentation is largely free.
- *Runner-up:* **sherpa-onnx** — the only option that is pure ONNX Runtime **and** has an
  **official Rust crate**, i.e. one codebase both languages. Whisper word-timestamp maturity looks
  unsettled (upstream discussion #2942), so prototype before committing.
- *Rejected:* openai-whisper (torch), NeMo/Parakeet (torch ≥2.7, 5.5 GB resident on CPU),
  Moonshine (pure ONNX and attractive, but built for short/streaming utterances — a component,
  not a 90-minute-meeting solution), Vosk (weak modern WER evidence), pydub (maintenance mode).

**Audio decoding → `PyAV`, with `soundfile` as the fast path** (`chunkshop[audio]`)
- **PyAV is the only verified pip-only path that decodes m4a/AAC** — its wheels bundle compiled
  FFmpeg libraries, so no system binary. This matters directly: client meeting recordings are
  routinely `.m4a`. `soundfile`/libsndfile and `miniaudio` are lighter but **cannot** do AAC
  (confirmed upstream issues). This mirrors what faster-whisper itself does.
- *Rust:* `symphonia` — pure Rust, decodes AAC/MP4/MP3/FLAC/WAV. ⚠️ **MPL-2.0** (file-level weak
  copyleft) — not Apache/MIT/BSD; flag for a licence decision before the Rust port ships.

### Extras layout (NN-6)

```toml
vision = []                                   # nothing! CLIP+Pillow already core via fastembed
ocr    = ["rapidocr>=3.0"]
audio  = ["faster-whisper>=1.1", "av>=13", "soundfile>=0.12"]
media  = ["chunkshop[ocr,audio]"]             # umbrella
```
No media package enters `dependencies`. Model weights download on first **use**, never at install
or import.

### ⚠️ Rust-parity nuance this surfaces (amends SC-12)

Python ASR (CTranslate2) and Rust ASR (whisper-rs or sherpa-onnx) are **different inference
engines**. Even with identical model weights, transcripts may differ in minor ways — so
"interchangeable vectors" cannot mean bit-identical for *derived* content.

**Parity is therefore defined at two levels:**
- **Bit-level** for embedding: same input text + same model → vectors match within float tolerance.
  Unchanged, and testable.
- **Contract-level** for derivation: same YAML, same schema, same metadata keys, same provenance
  fields. The **engine is recorded in provenance** (NN-3), so a Rust-derived transcript is
  attributable and distinguishable rather than silently different.

This is a genuine amendment to SC-12, not a workaround — asserting bit-identical ASR across
engines would have been a claim the spec could not keep.

### Decision log

| Decision | Alternatives weighed | Why |
|---|---|---|
| RapidOCR | Tesseract, Surya, EasyOCR, docTR, PaddleOCR, olmOCR | only ONNX-native + pip-only + permissive; reuses an engine already shipped |
| Reject Surya | — | model-weight licence (revenue/funding caps, no-compete) is untenable for OSS adopters |
| faster-whisper | sherpa-onnx, whisper.cpp, NeMo, Moonshine, Vosk | MIT, no torch, no system ffmpeg, native timestamps **+ VAD** (F8 nearly free) |
| PyAV (+soundfile) | soundfile alone, miniaudio, audioread, pydub, torchaudio | only pip-only decoder covering **m4a/AAC** — required for real meeting audio |
| stdlib urllib for VLM/ASR HTTP | requests, httpx | matches existing `openai_provider.py`; adds no dependency |
| Two-level Rust parity | claim bit-identical derivation | different ASR engines cannot guarantee identical transcripts; provenance makes the difference visible |

## Architecture
<!-- phase: architecture -->

### Components (new code in **bold**; everything else already exists)

```
                      ┌─────────────── API 1: derive (media -> TEXT) ───────────────┐
files/s3/http Source ->│ **DeriverRegistry**  ext -> rung                            │
                      │   rung 0  existing parsers (pdf/docx/xlsx/html) + **media meta** │
                      │   rung 2  **OcrDeriver**      (RapidOCR, chunkshop[ocr])    │
                      │   rung 3  **VlmDeriver**      (OpenAI-compat HTTP, urllib)  │
                      │   rung 3  **AsrDeriver**      (faster-whisper + **Segmenter**) │
                      │   rung 5  **SubprocessDeriver** (argv -> JSON)               │
                      │        all wrapped by **DerivationCache** + **ProvenanceStamp** │
                      └──────────────────────┬─────────────────────────────────────┘
                                             v  ParseResult(text, metadata)
                              Document -> Chunker -> Embedder -> Extractor -> Sink
                                             (unchanged; UNIFIED TEXT PLANE)

                      ┌─────────────── API 2: encode (media -> VECTOR) ─────────────┐
files Source ------->  │ whole_document Chunker -> **ClipEmbedder(MediaEncoder)**    │ -> own table
                      │   encode_media(paths) ingest | encode_query(texts) search   │    dim 512
                      └────────────────────────────────────────────────────────────┘
```

### The seams a coding agent builds against

```python
# --- API 1 ---------------------------------------------------------------
@dataclass(frozen=True)
class ParseResult:
    text: str
    metadata: dict            # deterministic + derived; namespaced derived.<tier>.*

class Deriver(Protocol):                       # generalises today's FileParser
    supported_extensions: list[str]
    def derive(self, path: Path) -> ParseResult: ...

# --- API 2 ---------------------------------------------------------------
class MediaEncoder(Protocol):
    dim: int
    def encode_media(self, paths: list[Path]) -> list[Sequence[float]]: ...
    def encode_query(self, texts: list[str]) -> list[Sequence[float]]: ...

# --- cross-cutting -------------------------------------------------------
class DerivationCache(Protocol):
    def key(self, content_hash: str, derivation_hash: str) -> str: ...
    def get(self, key: str) -> ParseResult | None: ...
    def put(self, key: str, result: ParseResult) -> None: ...
```

**Adding a modality = one new `Deriver` file + one registry entry + one pydantic variant.** This is
the same "one file + one loader branch + one union member" rule chunkshop already documents for
sources/chunkers/embedders. No new stage, no new pipeline concept.

### Data flow, API 1 (the primary path)

1. Source yields a path. `content_hash` = sha256 of bytes (the existing incremental cursor already
   computes this — reuse, do not recompute).
2. `DeriverRegistry` picks the rung by extension + config.
3. `derivation_hash = H(rung, model_id, model_version, params, prompt, argv)`.
4. **Cache lookup on `(content_hash, derivation_hash)`.** Hit → skip all work.
5. Miss → run the deriver. **A `""`/`None` result is a failure, not an empty success (F5).**
6. `ProvenanceStamp` writes `derived.<tier>.{model,version,ts,params_hash}` into metadata.
7. Cache put. `ParseResult` → `Document(content=text, metadata=...)`.
8. Unchanged pipeline from here: Chunker → Embedder → Extractor → Sink. Chunker-wins merge applies.

### Where state lives

| State | Location | Notes |
|---|---|---|
| Derived text + provenance (cache) | `~/.cache/chunkshop/derived/<key>.json` (configurable) | inside the deriver, **not** `FilesSource`; `RawStore` is the later S3 swap |
| Change-detection cursor | existing `source.incremental.cursor_path` | reused, not duplicated |
| Chunks, vectors, promoted columns | Postgres (or sqlite/mariadb/clickhouse) | unchanged |
| Model weights | fastembed/HF cache | lazy on first **use** (NN-6) |

### Failure modes and their required behaviour

| Failure | Required behaviour | SC |
|---|---|---|
| Deriver returns empty text | **raise / record `derivation_failed`** — never a silent 0-chunk doc | SC-5 |
| VLM returns `content: None` (reasoning model) | raise; never write an empty caption | SC-5, D4 |
| Endpoint 429/5xx | backoff + retry, then **circuit-break** (gamelib measured 38.7% errors under sustained 16-way) | SC-5 |
| Process killed mid-run | restart re-runs the cell; cache hits skip completed work; sink commits per document | SC-6 |
| Extra not installed | `RuntimeError` naming the exact extra — the existing parser convention | NN-6 |
| Filtered column missing on a cell | exclude that leg **loudly** | (Later, F15) |

### Concurrency

`orchestrate --concurrency N × embedder.threads ≈ cores` still holds for local work. Remote
derivation is **separately bounded**: default ~6 in-flight with a circuit breaker, from gamelib's
measurement that sustained 16-way degraded a vLLM endpoint from 1.58 → 0.54 req/s over 2.7 h.
Local CPU derivation (RapidOCR, faster-whisper) shares the ONNX/CTranslate2 thread budget and must
respect the existing `OMP_NUM_THREADS` discipline set in `runner.run_cell` **before** numpy/ORT import.

### What does NOT change

`Document`, `Chunk`, the five stages, the sink schema, `promote_metadata`, `mode:` semantics, the
orchestrator, and every existing YAML. `FileParser` is *generalised* to `Deriver`, **without breaking source compatibility**: the return
type widens to `str | ParseResult` and a bare `str` is auto-wrapped. Third-party parsers written
against the v1.0 signature keep working; `FileParser` remains an exported deprecated alias. There
is therefore **no breaking change in this spec** — a deliberate reversal of an earlier draft, which
proposed a hard signature change in a project at `v1.0.0-rc6`.

## Persona Feedback
<!-- phase: personas -->

Four hostile readers were run against the full drafted spec. Every objection below is either
**FIXED** (the spec changed) or **DEFERRED** (explicitly, with a reason). None are waved away.

### P1 — The RAG builder *(mid-size co, mixed corpus, migration-averse)*
**Wins them:** D1's unified text plane — one YAML, one table, one query across PDF/scan/recording.
F13's bakeoff versus hand-rolling Unstructured+LlamaIndex glue with no measurement.
**Objection:** *"F1 breaks the core parser protocol at `v1.0.0-rc6`. Your acceptance check only
proves output identity for your own six parsers — it says nothing about source compatibility for
my custom `FileParser`. That code breaks silently at exactly the version where a library is
supposed to stop doing that."*
**→ FIXED.** F1 is now **backward-compatible**: the return type widens to `str | ParseResult`, a
bare `str` is auto-wrapped, `FileParser` stays exported as a deprecated alias with a
`DeprecationWarning` for ≥1 minor. SC-13 now asserts **source compatibility** (a parser written
against the old signature must still work), not just output identity. **The spec now contains no
breaking change.** Verdict flips to adopt.

### P2 — The agentic-ops builder *(pg-raggraph / stele shaped)*
**Wins them:** D1 + SC-11 — media rows land in the same table and interface as text. F11/F12
provenance and supersession are exactly what memory/graph infra needs to reason about staleness.
**Objection (two-part, both landed):** *(a)* SC-11 was marked satisfied **by design, with zero code
and no test** — "suspiciously convenient for the SC that matters most to me." *(b)* A VLM
hallucination and a verbatim email are **byte-identical in shape** once both are `original_content`,
with provenance in a side-channel jsonb namespace. "No changes on your side" and "you must inspect
provenance before trusting content" are in tension.
**→ FIXED, both.** SC-11 now requires a **concrete integration test** (mixed corpus → one table →
each downstream tool's real read path, unmodified). And **F23** adds `content_origin`
(`authored` | `derived`) plus `derived_rung` as **first-class promoted columns, on by default** —
so ground-truth-vs-derived is filterable **in the query the consumer already runs**, no join, no
opt-in. Verdict flips to adopt.

### P3 — The skeptical OSS evaluator *(10 minutes, has seen a thousand RAG libraries)*
**Wins them:** the packaging finding — CLIP costs **zero** incremental install weight because
fastembed/Pillow/onnxruntime are already core. Market research confirms nobody markets the
5-minute zero-config story.
**Objection (a):** *"SC-1 claims base `pip install` works in 5 min, but D1's own showcased YAML
routes images through `{rung: ocr}` — and OCR is `chunkshop[ocr]`, not core. Your headline example
fails on a base install with your own documented `RuntimeError`."*
**→ FIXED.** Caught a genuine self-contradiction. D1's example now defaults images to
**`rung: clip_tags`** (F22 — keyless, base install, no extra), with OCR/VLM/ASR shown as commented
upgrades. SC-1 now names the tested path explicitly and requires CI to run **the exact YAML printed
in the README**.
**Objection (b) — NOT fixed, escalated to the owner:** *"Phase 2 answered the table-stakes test
with (b) → NARROW, EXCELLENT, ONE modality. Scope then ships two modalities, two APIs, a bakeoff
CLI, Rust parity and a subprocess escape hatch — 19 Critical features. That is not narrow by your
own definition."*
**→ DEFERRED TO OWNER DECISION.** This is fair. Mitigating facts: video, 3D, cross-plane fusion,
and vector-space separation were all cut; several "features" (F16, parts of F8) are structural or
free; and the owner explicitly chose images **+ audio** when asked. But 19 Critical items is not a
narrow v1, and the spec should not pretend otherwise. **See "Open decision: v1 scope" in Synthesis.**

### P4 — The insurance architect *(the spec's own canonical scenario)*
**Wins them:** F4 local OCR fixes the acknowledged active defect (`parsers/pdf.py`'s `or ""`
silently dropping scanned bills). F11/SC-8 provenance is compliance-grade and better than anything
in the competitive set. Rungs 0-2 are entirely local/keyless — real data-residency comfort.
**Objection:** *"'Fully served by D1' overclaims. Claim **damage photos** are not text-bearing: OCR
returns nothing and (per F5) must fail loudly; metadata/CLIP give shallow similarity. The only path
that describes damage-photo content is rung 3 remote VLM — which for any buyer without their own
vLLM box means **sending claims photos to a third party**. And every comparable tool's headline
tutorial defaults to GPT-4o."*
**→ PARTIALLY FIXED; one item deferred.**
- **F22 with a domain vocabulary is the on-box answer** for damage photos: zero-shot classify
  against `["dented bumper","water damage","roof damage","hail damage","cracked windshield",…]`
  yields real searchable tags, keyless, on-prem, ~18 ms/image. This is the same mechanism gamelib
  used for audio, and it directly answers "what does this photo show" without leaving the network.
- **New documentation guarantee (added to Vision):** chunkshop's own docs, README, and quickstarts
  **never demo a public cloud VLM API key.** Every shipped example points at a local or
  user-configured endpoint. Rung 3 is bring-your-own-endpoint by default, in the docs as well as
  in the code.
- **DEFERRED:** a local on-box *captioning* rung between OCR and remote VLM (BLIP-class, torch,
  ~2 GB extra). Real gap for buyers wanting free-form descriptions on-prem without a vLLM box.
  Recorded as a v1.1 candidate, not silently omitted.

### Cross-cutting gaps the review found that the spec had missed entirely

| Gap | Resolution |
|---|---|
| **Cache holds raw claim text / transcripts unencrypted on local disk** | **FIXED** — configurable cache root, documented TTL, `chunkshop cache purge`, `cache: false` opt-out. No redaction/encryption-at-rest in v1, **stated as a limitation** rather than implied solved. |
| **No cache-schema versioning** — a later change to the `derivation_hash` formula would silently serve mismatched entries | **FIXED** — `cache_schema_version` is part of the key. |
| **No cost governance for the bakeoff**, the one feature explicitly allowed to spend money | **FIXED** — **F24**: budget ceiling, pre-run estimate, `cost` recorded in provenance. |
| **SC-3 was an unfair fight** — a single CLIP leg measured against gamelib's 5-leg fused RRF, with the fusion technique (F15) deferred | **FIXED** — SC-3 is now whole-system vs whole-system (unified text plane incl. FTS + vector legs), and the stale `SC-3→F16` traceability row is corrected. |
| **No contingency if the bakeoff says chunkshop loses** | **FIXED** — SC-3 now states the result is published **whichever way it goes**; a loss redirects v1.1. Required by NN-1: a gate that is quietly dropped when unfavourable is not a measurement. |
| **SC-5's signal measured the wrong thing** — "0 rows lacking an embedding" is trivially true when failures never become rows | **FIXED** — now a reconciliation: `count(source files) == count(indexed) + count(recorded failures)`, each failure naming a reason and rung. |
| **No labelled-query-set story for adopters** — the differentiating feature presumes a gold set the adopter does not have | **DEFERRED, named.** Real, unaddressed adoption labour. v1.1 candidate: a `chunkshop bakeoff init` that bootstraps a query set from the corpus. Flagged rather than hidden. |
| **No access control / multi-tenancy for "one table, one query"** | **OUT OF SCOPE, stated.** chunkshop writes to a table the user owns; row-level security and tenant isolation are the database's job and the embedding application's. Recorded in Vision's "What this is explicitly NOT". |

## Synthesis
<!-- phase: synthesize -->

### Self-review scan

- **Placeholders:** none remaining. Every `_italic template text_` from the template is replaced.
- **Contradictions:** four were found by persona review and **all four are fixed** — SC-1 vs D1's
  quickstart, SC-5's signal, SC-3's unfair comparison + stale F16 traceability, and F1's source
  compatibility. See `## Persona Feedback`.
- **Scope honesty:** phase 2 answered "narrow and excellent". The first draft then specced 19
  critical features. **Resolved** by the v1 / v1.1 split above — v1 is images + spine.
- **Corrections made during the interview, recorded so they are not silently re-introduced:**
  1. *"No keyless captioner exists without torch"* — **wrong.** CLIP zero-shot tagging is keyless,
     18 ms/image, zero new deps (F22, D2c). Verified 3/3 on this box.
  2. *"Only CLIP can do 'more like this'"* — **wrong.** Description-space similarity does it, and
     measured **better separated** (0.879/0.858 vs 0.524) than CLIP's pixel-space margins
     (0.28 vs 0.24). Different similarity semantics, not a missing capability (D6, D2d).
  3. *"Media needs cross-modal fusion"* — **wrong for the canonical scenario.** Derived text is
     just text; it goes in the unified text plane and the shipped hybrid search already handles it.
     F15/F16 deferred (D1).
  4. *"F1 must be a breaking change"* — **avoidable.** Widening the return type preserves source
     compatibility (F1).

### Open decisions carried forward (none blocking)

| Item | Status |
|---|---|
| `chunker: whole_document` may not exist | verify at build time; ~20 lines if absent |
| Zero-shot **label vocabulary** design — generic vocab gives generic tags | user-configurable; bakeoff-tuned per NN-1. gamelib's hard-coded 40 terms then needed a re-derivation script — the cache (F10) makes that cheap, but the lesson stands |
| `symphonia` is **MPL-2.0** for the Rust audio path | licence decision needed before the Rust port ships (v1.1 concern) |
| sherpa-onnx vs faster-whisper for Rust ASR parity | prototype before committing (v1.1) |
| No redaction / encryption-at-rest for the derivation cache | **stated limitation**, not implied solved |
| Labelled-query-set bootstrapping for adopters | named gap; `bakeoff init` is a Later candidate |

### The one-paragraph version

chunkshop already owns four of the five steps in *select a file → convert to text → embed →
extract metadata → search*. This spec fills the missing step for non-text bytes. The primary path
is the **unified text plane**: OCR, zero-shot tags, captions, and (v1.1) transcripts all become
ordinary text in the ordinary table, searched by the hybrid search that already ships — so the
canonical five-modality insurance query needs **zero new retrieval code**. A second, explicitly
separate API embeds pixels with CLIP for perceptual and reference-image search. Underneath both
sits the part that is actually differentiating and that nobody in the competitive set does:
**provenance-keyed derivation caching**, so nothing is ever derived twice, nothing is written
without attribution, and nothing is silently overwritten by a better model. The zero-config path
needs no key, no GPU, and no extras, because CLIP rides a dependency chunkshop already ships. And
because the right derivation strategy is corpus-dependent, chunkshop **measures rather than
asserts** — the bakeoff that produced its `hierarchy` chunker result gains media arms, and the
head-to-head it would publish does not currently exist in the field.
