# Experiment: caveman filler-word reduction at ingest — 2026-05-31

**Question.** If we strip stopwords/filler from a chunk's `embedded_content` before
embedding ("speak like a caveman"), does ingestion get faster, and does semantic
search accuracy suffer? And — does the accuracy answer **flip across embedding
models**?

**Short answer.** Ingestion gets ~25–27% cheaper (the text is ~18% shorter / ~34%
fewer whitespace tokens). Accuracy cost is **mild and model-dependent**, not the
catastrophe a first (confounded) run suggested: most models lose 2–15% MRR, one
model (all-MiniLM-L6-v2) actually *gains*, and the best baseline model
(nomic-embed) barely moves (−2%). For a **paid/remote** embedder billed per token,
the trade can be worth it; for a **local** embedder it usually isn't, because the
same ingest speedup is available for free by raising `embedder.threads`.

This is a directional result on a **small gold set (12 queries)** — treat the
deltas as signal, not a leaderboard.

---

## Setup

- **Corpus:** SCOTUS 772-doc legal QA set (case overviews + decisions).
- **Chunker:** `hierarchy` (prefix_heading=true, min_section_chars=100) → 1095 chunks.
- **Gold:** `docs/samples/bakeoff-scotus/gold-scotus.yaml`, 12 hand-verified queries,
  each with a `gold_doc_id` that should rank #1.
- **Reducer:** `chunkshop.summarizers.caveman` — drops a fixed stopword list +
  punctuation-only tokens. Deterministic, no deps.
- **Metric:** semantic-only retrieval. For each query, embed it, cosine-rank all
  chunks, dedupe to the best rank per doc, and score **recall@1**, **recall@5**,
  **MRR** over the 12 queries. Brute-force exact cosine (no HNSW approximation) so
  the index is not a variable.
- **Three arms:**
  - **A** baseline — embed `X`, query raw.
  - **B** caveman index — embed `caveman(X)`, query raw.
  - **C** caveman both — embed `caveman(X)`, query `caveman(query)`.

Where `X` is **the exact text chunkshop embeds** (`embedded_content` =
heading-prefixed body for `hierarchy`). Caveman is applied to that same `X`, so the
**only** variable is filler removal.

## The confound that nearly produced the wrong headline

A first pass wired the baseline as plain `hierarchy` (embeds heading+body) and the
caveman arm as `summary_embed(hierarchy, caveman)` — but `summary_embed` applies the
reducer to the base chunk's `original_content`, which for `hierarchy` is the body
**without** the heading. So that run changed *two* things at once: it removed filler
**and** dropped the heading-prefix framing (the thing chunkshop's own bakeoff credits
for `hierarchy` winning on prose). Result: recall@1 0.67 → 0.33, MRR −33% — a
"disaster" that was mostly the missing heading, not caveman.

The corrected experiment above applies caveman to the **same** heading-prefixed text
in both arms. Lesson: when A/B-ing a text transform, hold the chunk-framing constant
or you measure the framing, not the transform.

## Ingestion cost

| | avg chars/chunk | whitespace tokens/chunk | embed time (772 docs) |
|---|---|---|---|
| raw | 1299 | 196.6 | 41.6 s |
| caveman | 1063 | 128.6 | 30.5 s |
| **delta** | **−18.2%** | **−34.6%** | **−26.7%** |

Smaller sequences → less ONNX compute → ~27% faster embedding, the dominant cost of
ingest. (Measured on `Xenova/bge-base-en-v1.5-int8`, threads=12.)

## Accuracy across models (caveman on the exact embedded text)

| Model | dim | Base MRR | B caveman+rawQ | C caveman+caveQ | recall@1 (base → caveman) |
|---|---|---|---|---|---|
| nomic-embed-v1.5-Q | 768 | **0.896** | 0.875 (−2%) | 0.875 (−2%) | 0.83 → 0.75 |
| BGE-base int8 | 768 | 0.806 | 0.764 (−5%) | 0.778 (−3%) | 0.67 → 0.58 |
| OpenAI text-embedding-3-small | 1536 | 0.792 | 0.736 (−7%) | 0.778 (−2%) | 0.58 → 0.50 / 0.58 |
| BGE-small fp32 | 384 | 0.765 | 0.705 (−8%) | 0.705 (−8%) | 0.67 → 0.58 |
| BGE-small int8 | 384 | 0.766 | 0.662 (−14%) | 0.648 (−15%) | 0.67 → 0.50 |
| all-MiniLM-L6-v2 | 384 | 0.667 | 0.708 (**+6%**) | 0.736 (**+10%**) | 0.33 → 0.42 / 0.50 |

### Readings

1. **The sign flips by model.** all-MiniLM-L6-v2 (older, weaker baseline) *improves*
   when filler is removed — fewer common tokens to dilute a small model's signal.
   Stronger models (nomic, BGE-base, OpenAI) lose a little. So yes: the verdict is
   model-dependent.
2. **Magnitude is mild once measured cleanly** — −2% to −15% MRR for the losers, vs
   the −33% the confounded run implied. The earlier "caveman destroys recall" claim
   was a measurement artifact.
3. **Quantization amplifies the hit.** BGE-small int8 (−14%) lost roughly twice the
   fp32 BGE-small (−8%) — int8's coarser vectors are less tolerant of
   out-of-distribution (caveman) text. If you reduce filler, prefer a less
   aggressively quantized model.
4. **Match the query transform for remote models.** OpenAI went −7% (raw query) →
   −2% (caveman query). BGE-base −5% → −3%. If the index is caveman-style, caveman
   the query too. (BGE-small is the exception — caveman-query made it slightly
   worse.)
5. **Best retriever barely cares.** nomic-embed had the top baseline (0.896) and lost
   only 2%. Caveman doesn't wreck a good model; it nibbles.

## So, is it worth doing?

| Situation | Verdict |
|---|---|
| **Local fastembed** (free compute) | **No.** The ~25% ingest speedup is available for free via `embedder.threads` (see `docs/perf-optimization-2026-05-31.md`) with zero accuracy cost. Don't pay 2–15% recall for speed you already have. |
| **Paid/remote embedder** (OpenAI etc., per-token billing) | **Maybe.** ~18% fewer tokens ≈ ~18% lower embedding bill, for ~2% MRR loss *if you caveman the query too*. A real cost/accuracy lever — measure on your corpus. |
| **Small/older model** (MiniLM-class) | **Possibly a win** — filler removal helped here. |
| **Aggressively quantized model** (int8) | **Avoid** — biggest accuracy hit. |

## Caveats

- **12 queries.** One query flipping moves recall@1 by ~0.08. The MRR deltas (a
  continuous score over 12×rank) are more robust, but this is a directional probe,
  not a tournament. Re-run with a 100+ query gold set before betting a pipeline on it.
- **One corpus, one domain** (legal prose). Filler density and the value of function
  words vary by domain; code or telemetry would behave differently.
- **One reducer.** `caveman` is a blunt stopword list. A gentler reducer (keep
  negations, keep prepositions that flip meaning) would likely shrink the accuracy
  hit — untested.
- Semantic-only. The FTS leg is unaffected (its `search_vector` is built from
  `original_content`, which caveman leaves raw), so hybrid search would mask part of
  the loss.

## Reproduce

Harness: `/tmp/cs-bench/caveman_models.py` (chunk once, embed raw + caveman per
model, rank the 12 gold queries by cosine). OpenAI leg reads the key-env name and is
skipped when unset. Corpus path is the pg-raggraph SCOTUS json; gold at
`docs/samples/bakeoff-scotus/gold-scotus.yaml`.
