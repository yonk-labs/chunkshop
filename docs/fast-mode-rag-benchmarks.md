# Fast-Mode RAG: Benchmarks, Metrics & Best Practices

> **Status:** research findings from the `feat/lede-v04-hints` branch (2026-05-22). The Postgres hybrid-search surface (`chunkshop/search.py`) and lede v0.4.1 hint-biased summarization are the substrate. Numbers come from real runs against a live Postgres test DB; raw data in `skill-output/benchmarks/` (gitignored).

## TL;DR

- **Summarizing retrieved chunks with lede before sending them to an LLM saves 77–92% of input tokens** (~90% on 772 real SCOTUS docs) for ~2–3 ms of latency, costing about **one query in ten** of accuracy (LLM-judged). Savings *grow* with realistic document length.
- **Biasing the summary toward query keywords (lede hints) is what makes it viable** — answer preservation 5/7 vs 2/7 un-hinted. A free win on tokens too.
- **The summary's accuracy ceiling is set by heading-drop, not length.** Bulking up `max_length` never reaches raw (no knee). The fix is to **prepend the chunk headings/captions to the summary** — retention 0.36 → 0.72, captions 0.03 → 0.90, for +110 tokens. (Feeding heading-bearing `embedded_content` alone barely helps — extractive lede compresses the caption back out.)
- **The FTS leg AND-ed all query terms (now fixed to OR — recall 0.08 → 0.67).** But on strong-embedding corpora **semantic-only wins**; fusing a weaker FTS leg *dilutes* it. Hybrid pays off only when legs are complementary — don't fuse reflexively.
- **Do NOT use lede keyword tags as a hard metadata filter** — vocabulary/synonym mismatch silently drops the answer. Keep keyword tags for faceting / soft re-rank.
- All chunker/embedder knobs still work post-changes (bakeoff regression passed); hybrid search now works on all 4 backends (pg/sqlite/mariadb full FTS, clickhouse degraded).

Caveats up front: gold sets are small (12–30 queries); deterministic facts-retention + token + latency numbers are exact; LLM-judged answer numbers are directional. See §8 for limitations and how to fix each.

---

## 1. The idea — "Fast mode" for RAG

Classic RAG sends the LLM the top-K retrieved chunks verbatim. At K=10–30 that's thousands of input tokens per query, most of it padding around the relevant sentence. "Fast mode" inserts a deterministic, ~10 ms extractive-summarization step between retrieval and the LLM:

```
query
  ├─ parse into keywords            (lede top_terms on the query)
  ├─ hybrid search                  (semantic + FTS, RRF-fused)  -> top-K chunks
  ├─ summarize the K chunks         (lede, biased toward query keywords)
  └─ send ONE focused summary       (instead of K raw chunks) -> LLM
```

The LLM sees a ~300-token summary instead of ~4,000 tokens of chunks — same answer, a fraction of the input cost, and the summarization is deterministic (reproducible, no API call).

---

## 2. What was built to measure it

- **`chunkshop/search.py`** — Postgres hybrid retrieval: `semantic_search` (pgvector cosine), `keyword_search` (tsvector + `ts_rank`), `hybrid_search` (RRF or weighted fusion), and a filter-only `where` predicate. (Postgres-first; other backends are follow-ups.)
- **lede v0.4.1** — `summarize(..., hints=, hint_focus=, hint_mode=)` for query-biased extractive summaries; `top_terms(query, with_scores=True)` to parse a query into weighted keywords.
- **Benchmark harness** — `skill-output/benchmarks/fastmode_bench.py` (token/latency/recall), `keyword_filter_bench.py` (tag-filter experiment), `bakeoff-regression.yaml` (knob regression).

---

## 3. Headline result — token savings

Top-10 hybrid_rrf chunks vs a single lede summary (max_length 1200 chars), tokens counted with `tiktoken` `cl100k_base`:

| Corpus | mean raw tokens (top-10) | summary tokens (plain) | **token savings** |
|---|---|---|---|
| Sample (4 docs, short) | 1,143 | 265 | **76.9%** |
| NTSB (20 docs, full-length reports) | 4,092 | 312 | **92.4%** |

**Longer, more realistic documents make Fast mode look *better*** — the raw payload grows while the summary stays bounded. Summarization latency is ~2–3 ms; combined retrieve+summarize is ~20 ms.

### Query hints help on both axes

Biasing the summary toward keywords parsed from the query (and optionally lemma-expanding them):

| Summary variant | tokens | answer-preservation proxy (key-term recall) |
|---|---|---|
| plain (no hints) | 265 | 0.51 |
| query-keyword hints | 259 | 0.61 |
| hints + lemma expansion | 259 | **0.63** |

Hinting preserved *more* of the gold document's distinctive terms while costing *fewer* tokens. The "parse the prompt into keywords and bias the summary" instinct is validated.

---

## 4. Retrieval — hybrid vs semantic vs FTS

NTSB corpus (the more discriminating of the two), per-variant:

| Variant | recall@10 | MRR@10 | retrieval ms |
|---|---|---|---|
| semantic only | 1.000 | 0.903 | ~10 |
| FTS only | **0.250** | 0.250 | ~10 |
| hybrid RRF (sem+fts) | 1.000 | **0.958** | ~18 |
| hybrid weighted | 1.000 | 0.958 | ~18 |

- **FTS-only collapses on narrative queries** (recall 0.25): users ask "how do we rotate API keys" but the doc says "secrets/credentials" — literal `tsquery` misses the synonym.
- **Hybrid still beats semantic-only on MRR** (0.958 vs 0.903): fusing even a weak FTS signal pulls a couple of gold docs from rank 2 to rank 1. RRF (a row appearing in both legs ranks higher) is the right default.
- Recall saturates at 1.0 on these small corpora — see caveats; rank with MRR here.

### Pipeline vs parallel

- **Parallel** (one fused `hybrid_rrf` call) vs **pipeline** (semantic over-fetch, then intersect with FTS): tied on recall/MRR, parallel marginally faster. **Recommendation: parallel + RRF.** The pipeline's extra round-trip bought nothing at this scale.
- Adding a **structured** predicate (e.g. `category` filter) nudged MRR up (0.929→0.964 on the sample corpus) by excluding cross-category noise — *when the metadata is structured*. See §5 for why *keyword* predicates are different.

---

## 5. Metadata & predicate filtering — a cautionary result

We tested using lede-extracted keyword tags as a hard predicate filter (`WHERE tags && query_keywords`):

| | sample | NTSB |
|---|---|---|
| semantic baseline recall@10 | 1.000 | 1.000 |
| + keyword-tag filter (no expansion) | 0.714 | 0.833 |
| queries where filter EXCLUDED the gold doc | 4/14 | 2/12 |
| + lemma expansion | 0.714 | 0.917 |

**Verdict: keyword-tag hard filtering hinders recall.** It never raised recall and frequently zeroed it out — the user's query keyword ("keys") and the document's extracted tags ("secrets") are a *vocabulary/synonym mismatch* that exact array-overlap can't bridge. Lemma expansion only adds morphological variants (`key`→`keys`) plus some noise (`manif`, `datum`); it can't cross a synonym gap, and it bloats tags (10→43 per chunk), eroding the filter's discrimination.

**Best practices:**
- Reserve hard metadata filters (`WHERE`) for **structured** fields — `source`, `category`, `date`, tenant — where values are controlled, not free-text keywords.
- Use lede keyword tags for **faceting, display, or a soft re-rank bonus** — never as a recall-gating predicate.
- If you must filter on keywords, normalize both sides and accept synonym misses, or push the keywords into the *ranking* (hybrid FTS leg) rather than a hard filter.

---

## 6. Knob regression (bakeoff)

Four chunkers × bge-small × Postgres on the sample corpus, to confirm nothing regressed under the lede changes:

| Chunker | MRR | r@1 |
|---|---|---|
| neighbor_expand(window=1, base=hierarchy) | **0.964** | 0.929 |
| hierarchy | 0.929 | 0.857 |
| sentence_aware | 0.917 | 0.857 |
| fixed_overlap | 0.863 | 0.786 |

All chunkers ingest + score correctly. `neighbor_expand` wins on this corpus (consistent with chunkshop's prior bakeoff findings).

---

## 7. Recommended Fast-mode recipe

```
1. Parse the query: kw = top_terms(query, n=6, with_scores=True)
2. Retrieve:        hits = hybrid_search(query, query_vec, k=10,
                                         legs=("semantic","fts"), fusion="rrf")
3. (optional) filter ONLY on structured metadata (source/category), never keywords
4. Summarize:       summary = lede.summarize("\n\n".join(h.text for h in hits),
                                  max_length=1200, hints=kw,
                                  hint_focus=0.7, hint_mode="soft")
5. Send `summary` (not the raw hits) to the LLM with the original question.
```

Operating points: K=10 is a good default; `hint_mode="soft"` (bias, don't filter); lemma expansion is optional and low-cost but marginal — skip synonyms expansion unless you've measured a gain.

---

## 7a. Raw chunks vs. lede summary — side by side (SCOTUS, 772 docs)

The headline trade, measured with the claude-CLI judge over 12 gold queries at k=10:

| Dimension | Raw chunks | lede summary | Delta |
|---|---:|---:|---|
| Mean input tokens | 2,431 | 234 | **−90%** |
| Median / max input tokens | 2,414 / 3,028 | 229 / 259 | ~−91% |
| LLM input cost @ $3/M (illustrative) | $0.00729 | $0.00070 | **−90%** |
| Retrieval latency | ~19 ms | ~19 ms | same |
| + Summarization | 0 ms | ~2–3 ms | +2–3 ms |
| Answerable (LLM could answer) | 7/12 | 5/12 | −2 |
| Answer preservation (answerable subset, **hinted**) | — (ref) | **5/7 = 71%** | — |
| Answer preservation (plain, un-hinted) | — | 2/7 = 29% | hinting matters |
| Facts CORRECT vs gold (10 matched) | 6/10 | 5/10 | −1 |

**The trade in one line: ~90% fewer input tokens and ~90% lower input cost for the loss of about one query in ten of accuracy** — and query-hinting is what makes the summary viable (5/7 vs 2/7). For high-volume/cost-sensitive RAG this is a clear win; for high-stakes single-answer lookups, send raw (or summary + an "expand if unsure" escape hatch).

## 7b. Does bulking up the summary help? (length sweep)

We swept `max_length` and measured **deterministic required-facts retention** (fraction of the gold answer's required facts present in the context — no LLM, no variance), on the answerable subset of the 30-question pg-raggraph SCOTUS set:

| summary length | facts retention | tokens | % savings vs raw |
|---|---:|---:|---:|
| 800 | 0.295 | 165 | 91.7% |
| 1,200 | 0.375 | 247 | 87.6% |
| 2,000 | 0.453 | 405 | 79.7% |
| 3,000 | 0.474 | 603 | 69.7% |
| 4,000 | 0.530 | 794 | 60.1% |
| **RAW** | **1.000** | 1,990 | 0% |

**There is no knee.** Retention rises monotonically with length but never plateaus and never reaches raw, while token savings collapse from 88% to 60%. So "more detail" is directionally true but not a free win — full retention essentially requires the full context.

**Why — and the actual fix (measured A/B/C, summary @1200 chars, answerable subset):** the single most-dropped fact is the **case caption** ("Apple v. Pepper", "Bostock v. Clayton County"). The `hierarchy` chunker stores the caption as a **heading line**, and lede is an extractive *sentence* summarizer, so it drops non-sentence heading fragments at every length.

| Summary built from | facts retention | caption retention | tokens |
|---|---:|---:|---:|
| A. `original_content` (status quo) | 0.362 | 0.026 | 246 |
| B. `embedded_content` (heading-bearing) | 0.375 | 0.026 | 251 |
| **C. `embedded_content` + heading prepended to the summary** | **0.718** | **0.897** | 356 |

**The decisive nuance: B barely helps.** Feeding the heading-bearing `embedded_content` puts the caption in the *input*, but lede is extractive and compresses it right back out (0.362 → 0.375). The fix that works is **C — explicitly prepend the deduped chunk headings to the summary output** (0.362 → **0.718** facts, 0.026 → **0.897** captions, for +110 tokens). That nearly doubles fact retention and restores captions, far cheaper than the length-sweep alternative (which only reached 0.53 at 4,000 chars / 60% savings).

So: (1) **I-14** — `search.Hit` now exposes `embedded_text` so a recipe *can* reach the heading-bearing text; (2) **I-15** — the Fast-mode recipe must **prepend headings to the summary**, not just swap the source column. The "bulk it up" instinct found the right symptom (summary misses facts); the cause is heading-drop and the fix is a heading-aware recipe, not a higher `max_length`.

## 8. Limitations & how to fix them

| # | Limitation | Why it matters | How to fix |
|---|---|---|---|
| L1 | **Small eval set** — 12–30 queries, ~7–16 answerable | One query ≈ 6–8 points; headlines are directional, not tournament results | Expand to 100s of RAG-answerable questions; report confidence intervals |
| L2 | **Gold facts include knowledge-graph attributes** (term-years, issue labels) absent from prose (I-16) | ~14/30 facts are unanswerable by *any* free-text RAG → deflates absolute accuracy | Use the answerable subset (facts present in raw) or author RAG-native gold with answer spans |
| L3 | **LLM judge is noisy** — over-refuses NOT_IN_CONTEXT on summaries (I-13) | Single preservation %s wobble ±1–2 queries | 3-trial voting (done) + the **deterministic required-facts-retention metric** (no LLM) as the reproducible headline; reserve the LLM only for generation, score its output deterministically |
| L4 | **Comparative judge measures preservation-vs-raw, not truth** | A wrong raw answer makes a faithfully-wrong summary look "CORRECT" | Score against `required_facts` / `gold_answer` absolutely (done where gold matched); prefer the deterministic metric |
| L5 | **Heading/caption drop** (I-14/I-15) — lede drops non-sentence fragments | Captions ("Apple v. Pepper") lost at every summary length; caps summary accuracy | **Prepend deduped chunk headings to the summary output** → facts 0.36→0.72, captions 0.03→0.90, +110 tokens. `Hit.embedded_text` (I-14, shipped) exposes the heading-bearing text; the recipe must prepend it (I-15) — feeding it as input alone is compressed back out |
| L6 | **Hybrid dilutes when one leg dominates** | On strong-embedding corpora, fusing a weaker FTS leg lowers MRR vs semantic-only | Quality-weight the legs (semantic ≫ fts), or skip fusion when semantic confidence is high; fusion earns its keep only when legs are complementary |
| L7 | **`similar` expansion blocked** (I-10) | lede-spacy `_nlp()` hardcodes `en_core_web_sm` (no vectors) | lede-spacy fix: let `_nlp()` honor a model env var (same pattern as the `top_terms` fix) |
| L8 | **WordNet synonym sense-ambiguity** (I-11) | "keys" → "Florida key", not "secrets" — synonyms add noise as easily as signal | Sense-disambiguate with query context, or prefer lemma + leave synonyms opt-in |
| L9 | **Single-run timings, Postgres-only heavy runs** | ms are one run; sqlite/mariadb/clickhouse only have light correctness coverage | Repeat-run distributions; port the heavy benchmark to the other engines |

**What's exact vs directional:** token-savings, latency, and deterministic facts-retention are reproducible and trustworthy. LLM-judged answerability/preservation are directional (small set + judge variance). Read the headline token numbers as fact; read the accuracy numbers as "about one query in ten."

See `skill-output/benchmarks/ISSUES.md` for the full triage list (I-1 … I-16, with resolved items marked).
