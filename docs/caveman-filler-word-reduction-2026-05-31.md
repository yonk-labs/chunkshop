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
6. **The predictor is representational headroom, not dimension count.** Sorting the
   table, the hit tracks how much slack the model has, not its raw size:
   - 768/1536-dim, full-precision (nomic, BGE-base, OpenAI): −2% to −5% — room to
     absorb the out-of-distribution grammar.
   - 384-dim fp32 (BGE-small): −8% — less room.
   - 384-dim **+ int8** (BGE-small int8): −14% — quantization already discarded
     precision; caveman discards more, and the two compound.
   - 384-dim but very weak (MiniLM): **+6–10%** — so capacity-starved that filler was
     net noise, so removing it helps.

   Note the two 384-dim models (BGE-small, MiniLM) move in **opposite** directions, so
   dimension alone is not predictive. The usable heuristic: roomy full-precision model
   → caveman barely registers; small **and** quantized → it bites hardest; truly tiny
   → it can be a net win.

## Two different kinds of "speedup" — don't confuse them

An earlier draft of this doc said local users should skip caveman because
`embedder.threads` gives the speedup "for free." That was wrong, and it's worth
being precise about why, because it changes the verdict.

- **`embedder.threads` does not reduce the work.** It spreads the *same* matrix math
  across more cores. Total core-seconds stay flat (slightly up, from sync overhead).
  So it only lowers wall-time when cores are **idle**. It's a **latency ⇄ CPU-headroom**
  trade against a fixed core budget — which is exactly why chunkshop's rule is
  `orchestrate --concurrency N × embedder.threads ≈ cores`. The headroom is a pie you
  divide, not free capacity.
- **Caveman reduces the actual work.** Fewer tokens → fewer FLOPs per chunk. That
  raises throughput-*per-core*, which is the only thing that helps when the box is
  already saturated.

Measured on the 24-core box, simulating a busy server with **8 concurrent embed jobs**
(aggregate throughput, texts/sec — the metric a shared server cares about):

| Scenario | thread demand | aggregate throughput |
|---|---|---|
| raw text, threads=3 (matched to cores) | 24 / 24 | 46.5 texts/s |
| raw text, threads=12 (oversubscribed) | 96 / 24 | **35.6 texts/s (−23.5%)** |
| **caveman** text, threads=3 (matched) | 24 / 24 | **56.1 texts/s (+20.6%)** |

Under load, raising threads **lost** 23.5% throughput (oversubscription: 8×12=96
threads fighting over 24 cores). Caveman **gained** 20.6% at the same thread budget,
because it cut the work. The "threads = 1.5× faster" number elsewhere is a real win
**only on an idle box**; it inverts under contention.

## So, is it worth doing?

It's a genuine **speed/cost ⇄ accuracy** trade, not a free lunch and not a trap —
context decides.

| Situation | Verdict |
|---|---|
| **Idle / single-tenant box, local model** | **Probably not.** Spare cores mean `embedder.threads` buys the wall-time win at zero accuracy cost. Caveman's −2–15% recall isn't worth it when you have idle cores to throw at the problem. |
| **Busy / multi-tenant local server** (the hundreds-of-users case) | **Now it's a real lever.** Threads can't help — they oversubscribe and *lose* throughput (−23.5% measured). Caveman raises per-core throughput (+20.6%) by doing less work. If you're CPU-bound, this is the trade: ~2–15% recall (model-dependent) for ~20% more ingest capacity. |
| **Paid/remote embedder** (OpenAI etc., per-token billing) | **Often yes.** ~18% fewer tokens ≈ ~18% lower embedding bill, every ingest, for ~2% MRR loss *if you caveman the query too*. Measure on your corpus. |
| **Small/older model** (MiniLM-class) | **Possibly a win** — filler removal *helped* here (+6–10% MRR). |
| **Aggressively quantized model** (int8) | **Avoid** — biggest accuracy hit (−14%). |

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

Accuracy harness: `/tmp/cs-bench/caveman_models.py` (chunk once, embed raw + caveman
per model, rank the 12 gold queries by cosine). OpenAI leg reads the key-env name and
is skipped when unset. Corpus path is the pg-raggraph SCOTUS json; gold at
`docs/samples/bakeoff-scotus/gold-scotus.yaml`.

Contention harness: `/tmp/cs-bench/contention.py` (spawns W concurrent embedder
processes at `threads=T`, measures aggregate texts/sec — backs the "threads aren't
free under load" table above).
