# Experiment: caveman filler-word reduction at ingest — 2026-05-31

**Question.** If we strip stopwords/filler from a chunk's `embedded_content` before
embedding ("speak like a caveman"), does ingestion get cheaper, and how much does
semantic-search accuracy suffer? And does the accuracy answer change by embedding
model?

**Answer (validated on third-party benchmarks).** On prose, caveman reduction buys
**~25% cheaper embedding** for **~1–2% lower retrieval accuracy (NDCG@10)** — and
that trade is **flat across model size and quantization**. The dramatic
model-dependent swings in the first draft of this experiment (−14% to +6%, "int8
amplifies it," a "representational-headroom" theory) were **sampling noise from a
12-query home-grown gold set**. They vanished the moment we measured on real
benchmarks (BEIR SciFact + NFCorpus, 600+ queries). This doc keeps the wrong turns
on purpose — the methodology lesson is the most valuable part.

---

## 1. The trade, in numbers

### Ingestion / embedding cost (real, unchanged)

Caveman shrinks the embedded text, which is pure win on the cost side. On the SCOTUS
corpus (`Xenova/bge-base-en-v1.5-int8`, threads=12):

| | avg chars/chunk | whitespace tokens/chunk | embed time (772 docs) |
|---|---|---|---|
| raw | 1299 | 196.6 | 41.6 s |
| caveman | 1063 | 128.6 | 30.5 s |
| **delta** | **−18.2%** | **−34.6%** | **−26.7%** |

How that ~25% shows up depends on your bottleneck:

| You care about… | The win |
|---|---|
| Embedding wall-time (single job) | **~25–27% faster** |
| Throughput on a busy/multi-tenant box | **~+20%** aggregate (survives contention — see §4) |
| A paid per-token embedder (OpenAI etc.) | **~18–34% lower token bill**, every ingest |

The exact number scales with how much filler your text has: legal/scientific prose
sheds ~18% of characters / ~34% of words; terser text (code, logs, telemetry) sheds
less and saves less.

### Accuracy cost (BEIR — third-party, 600+ queries)

Caveman applied to the document text, scored with standard NDCG@10 against real
relevance judgments:

| Dataset (domain) | queries | model | baseline NDCG@10 | caveman ΔNDCG@10 | Recall@10 (base→cav) |
|---|---|---|---|---|---|
| SciFact (science) | 300 | BGE-small fp32 | 0.7215 | **−2.3%** | 0.840 → 0.838 |
| SciFact | 300 | BGE-small int8 | 0.7134 | **−1.6%** | 0.836 → 0.840 |
| SciFact | 300 | BGE-base int8 | 0.7413 | **+0.2%** | 0.872 → 0.888 |
| NFCorpus (medical) | 323 | BGE-small fp32 | 0.3409 | **−1.5%** | 0.317 → 0.315 |
| NFCorpus | 323 | BGE-small int8 | 0.3394 | **−0.5%** | 0.315 → 0.320 |

Range **+0.2% to −2.3%**, mean ≈ **−1%**. Two domains, two relevance structures
(sparse binary vs dense graded ~38 rel/query), fp32 and int8 — all cluster at ~1–2%.
Recall@10 barely moves (sometimes *up*). **Semantic-only**; with hybrid search the
keyword leg runs on raw `original_content`, so the real-world hit is smaller still.

## 2. Why the accuracy hit is so small — the chunk-overlap view

Gold-free check (50 SCOTUS queries): for each query, how many of the raw-retrieval
top-10 chunks does caveman-retrieval still return?

| Model | overlap@10 (caveman vs raw) |
|---|---|
| BGE-small fp32 | 0.72 ± 0.13 |
| BGE-small int8 | 0.70 ± 0.14 |
| BGE-base fp32 | 0.75 ± 0.14 |
| BGE-base int8 | 0.75 ± 0.12 |
| all-MiniLM-L6-v2 | 0.73 ± 0.12 |

Caveman reshuffles ~28% of the top-10, but the swaps are **near-equivalent chunks** —
relevance holds, so NDCG barely moves. Overlap is tight (std ~0.13) and flat across
size/quant: no model signal here either. It changes *which* chunks, not *how good*.

## 3. The cautionary tale (kept on purpose)

The first version of this experiment used a **12-query hand-written SCOTUS gold set**
(`gold-scotus.yaml`) scored by recall@1 / MRR. It produced a confident, totally wrong,
model-dependent story. The path to noticing:

1. **Confound (caught first).** Baseline plain-`hierarchy` embeds `embedded_content`
   (heading-prefixed body); the caveman arm via `summary_embed` reduced the base
   chunk's `original_content` (body *without* heading). That changed two things at
   once — filler **and** the heading framing — for a fake −33%. Fixed by reducing the
   *same* heading-prefixed text in both arms.
2. **Wild per-model numbers.** Post-fix, the 12-query set still gave −14% to +6% MRR
   across models. I fit a "more representational headroom → smaller hit" rule to six
   points… then **bge-large (1024-dim, fp32)** lost −13%, breaking it. Worse: the
   *same* bge-large served two ways (LAN fp32 −13% vs local fp32 −9%) disagreed, and
   bge-base and bge-large produced **identical** recall — the metric was out of
   resolution.
3. **"int8 amplifies it" died too.** BGE-small said int8 (−14%) > fp32 (−8%); BGE-base
   said the opposite (int8 −5% < fp32 −9%). Two controlled pairs, opposite signs.
4. **The fix wasn't rerunning** (embeddings are deterministic per fixed batch — see
   caveat) and it **was never an LLM judge** (retrieval is pure cosine vs labels). It
   was **sampling noise**: at 12 queries, recall@1 moves in 1/12 = 8.3% steps, so a
   −13% vs −5% gap is 1–2 questions flipping. The cure is more queries + third-party
   labels → BEIR, where the whole spread collapses to ~1–2% flat.

### The wrong table (preserved as a warning)

12-query SCOTUS, caveman ΔMRR — **do not trust these; this is what noise looks like:**

| model | dim | quant | ΔMRR (raw query) |
|---|---|---|---|
| nomic-embed-v1.5-Q | 768 | Q | −2% |
| bge-large fp32 (LAN) | 1024 | fp32 | −13% |
| bge-large fp32 (local) | 1024 | fp32 | −9% |
| bge-base fp32 | 768 | fp32 | −9% |
| bge-base int8 | 768 | int8 | −5% |
| OpenAI 3-small | 1536 | — | −7% / −2% |
| bge-small fp32 | 384 | fp32 | −8% |
| bge-small int8 | 384 | int8 | −14% |
| all-MiniLM-L6-v2 | 384 | fp32 | +6% |

Same trade, measured properly (BEIR §1): **+0.2% to −2.3%, flat.**

## 4. Two kinds of "speedup" — caveman vs threads

`embedder.threads` does **not** reduce work; it spreads the same math over more cores,
so it only helps when cores are idle. Caveman reduces the work itself. Measured with 8
concurrent embed jobs on 24 cores (aggregate throughput):

| Scenario | thread demand | throughput |
|---|---|---|
| raw, threads=3 (matched) | 24 / 24 | 46.5 texts/s |
| raw, threads=12 (oversubscribed) | 96 / 24 | **35.6 texts/s (−23.5%)** |
| caveman, threads=3 | 24 / 24 | **56.1 texts/s (+20.6%)** |

Under load, more threads *lose* throughput; caveman *gains* it. That's why caveman is
the right lever on a busy server and threads are the right lever on an idle one.

## 5. So, is it worth doing?

| Situation | Verdict |
|---|---|
| **Idle / single-tenant box, local model** | **Probably not.** Spare cores → `embedder.threads` buys the speed at zero accuracy cost. Don't pay even ~1–2% recall when idle cores are free. |
| **Busy / multi-tenant local server** | **Real lever.** Threads oversubscribe and lose throughput; caveman adds ~20% by doing less work, for ~1–2% recall. |
| **Paid/remote embedder** (per-token billing) | **Often yes.** ~18–34% lower bill, every ingest, for ~1–2% NDCG. |
| **Any model** | No babying required — the hit is flat across size and quantization. |

## 6. Methodology lessons

1. **A 12-question benchmark will lie to you.** It produced a confident, wrong,
   model-dependent narrative. Use ≥hundreds of queries; prefer third-party labels.
2. **Hold the framing constant.** A/B a text transform with everything else identical,
   or you measure the heading prefix (or the tokenizer, or the batch padding), not the
   transform.
3. **Controlled comparisons only.** "int8 amplifies it" came from comparing across
   models; the one within-model pair that looked clean reversed on the next model.
4. **Pick a noise-robust metric.** recall@1 on 12 queries is a coarse 1/12 staircase;
   NDCG@10 over 300 queries, or gold-free chunk-overlap, is far more stable.

## 7. Caveats

- **Two datasets, short documents** (SciFact, NFCorpus). LoCo / long-document and
  more domains would strengthen generality; the ~1–2% is consistent so far.
- **Coverage limited to BGE fp32/int8 on the third-party side.** A 4-bit point
  (snowflake-arctic-embed-l-v2.0 IQ4_XS, a non-BGE architecture) was attempted on
  BEIR NFCorpus but not collected: the LAN GGUF server answered trivial pings yet
  stalled on the real batch load (process blocked ~17 min at 0% CPU). It doesn't
  change the conclusion — "flat across quantization" is already covered by the
  fp32↔int8 BGE pairs on two benchmarks — but a clean 4-bit / different-architecture
  confirmation is still open.
- **One reducer.** `caveman` is a blunt stopword list. A gentler reducer (keep
  negations / meaning-flipping prepositions) might shrink the hit further — untested.
- **Determinism check was inconclusive:** re-embedding a 60-text slice differed from
  the full run, but that's a *batch-padding* artifact (BatchLongest pads to each
  batch's max, and the slice has a different max), plus minor multithreaded-ONNX FP
  reduction-order variance — not run-to-run randomness large enough to matter. The
  decisive evidence against "it's noise" is the stable 600-query NDCG, not this check.

## 8. Reproduce

- Accuracy (home-grown, noisy): `/tmp/cs-bench/caveman_models.py`,
  `/tmp/cs-bench/caveman_local_fp32.py`, `/tmp/cs-bench/caveman_remote.py`
  (LAN OpenAI-compatible servers).
- Chunk-overlap (gold-free): `/tmp/cs-bench/caveman_overlap.py`.
- Contention (threads-aren't-free): `/tmp/cs-bench/contention.py`.
- **Third-party (authoritative):** `/tmp/cs-bench/caveman_beir.py <dataset> <models>`
  — downloads BEIR `scifact` / `nfcorpus` zips, embeds raw vs caveman with chunkshop's
  own fastembed provider, scores NDCG@10 + Recall@10 against the real qrels.
