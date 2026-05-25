# Summary Density Validation Plan

This plan captures the pg-raggraph findings that should be reproduced inside
Chunkshop using only Chunkshop and lede capabilities. The goal is to determine
which context-packing strategies should become recommended defaults or easy
modes.

For the backend/product plan that turns these findings into per-KB profile
defaults and an N-rung cheap↔accurate ladder, see
`docs/kb-profile-ladder-plan.md`.

For the concrete Phase D results and recommendations that motivate this
validation, see `docs/phase-d-findings-next-steps.md`.

## Findings To Validate

The pg-raggraph deep run produced several useful signals:

1. **Oracle baselines must be symmetric.** A "full document oracle" capped at
   three documents was not a real ceiling when classic RAG retrieved chunks
   spanning 17-25 documents. A fair oracle must send all gold-supporting
   documents or all documents touched by the retrieved chunks.
2. **Smaller top-k can win.** Hybrid retrieval at `top_k=10` with classic
   chunks was the best balanced recipe in the multi-dataset run, beating larger
   `top_k` settings on score/tokens.
3. **Summary source depends on corpus structure.**
   - MHR/news: summarizing retrieved chunks beat full-document summaries.
   - SCOTUS/legal: full-document summaries beat chunk summaries.
4. **A robust hedge may be `doc + chunk summaries + top5`.** It was not always
   the top recipe, but it performed well across the inverted MHR and SCOTUS
   cases.
5. **`chunkshop:hierarchy` is a likely free token win.** It matched auto
   accuracy on MHR while using fewer tokens because section-boundary chunks
   ended before the chunk cap.

These are directional until tested with larger question sets, fair oracles,
and inspectable audits in this repo.

## Hypotheses

H1. **Chunk summaries win on cross-document news/compositional workloads.**
When evidence is scattered across many documents, summarizing the retrieved
chunks should beat summarizing one or a few full documents at the same token
budget.

H2. **Document summaries win on self-contained legal/document workloads.**
When the answer is usually inside one coherent source document, full-document
lede reports should beat chunk-only summaries.

H3. **`doc_and_chunk_summary_toc_facts_plus_top5` is the best general
fallback.** It should be less brittle across corpus types than doc-only or
chunk-only summaries, even when it is not the cheapest option.

H4. **`top_k=10` is a strong default candidate for focused retrieval.** Larger
candidate sets should be justified by measurable answer-quality gains, not just
retrieval recall.

H5. **`chunkshop:hierarchy` should be preferred over fixed-size auto chunking
when accuracy is tied.** It should reduce context tokens without sacrificing
answer quality on structured prose.

## Datasets

### Required

- **SCOTUS**: use the 50-question bucket fixture, but score only fair RAG
  buckets for this validation:
  - `medium_semantic_rag`
  - `easy_semantic_rag`
  - `layup_summary`

- **LongBench-derived multi-hop/compositional tasks**:
  - MuSiQue
  - 2WikiMultiHopQA
  - HotpotQA, if available in the current LongBench slice

### Preferred Additions

- **MHR/news** if available locally as raw benchmark rows.
- **PG code base** for code/documentation behavior once SCOTUS and LongBench
  are stable.

Do not use pg-raggraph caches or product-only retrieval methods. Use benchmark
gold answers only for question/reference data, not for retrieval shortcuts.

## Question Counts

Run enough questions that a one-case swing does not dominate conclusions.

Minimum validation:

| Workload | Questions | Selection |
|---|---:|---|
| SCOTUS | 30 | all fair RAG questions from easy, medium, layup |
| MuSiQue | 60 | stratified from answerable multi-hop rows |
| 2Wiki | 60 | stratified from answerable multi-hop rows |
| MHR/news | 60 | if local data is available |

If runtime is too high, run a 15-question smoke per workload first, then run
the full validation once provider error rate is near zero.

## Retrieval Setup

Use the same retrieval layer for every context strategy so the context packer
is the isolated variable.

Primary retrieval policy:

- embedder: `BAAI/bge-large-en-v1.5`
- chunker: `hierarchy`
- vector operator: `cosine`
- retrieval mode: hybrid RRF over semantic + FTS
- metadata: use promoted lede fields and `lede_report.search_text` in FTS
- hints: off for primary comparison, then optional on/off sensitivity pass

Top-k sweep:

- `top_k=10`
- `top_k=25`

Use `top_k=25` as the classic RAG baseline because it is the current familiar
reference point. Use `top_k=10` to test the "small top-k wins" claim.

Secondary sensitivity pass, only after the primary run:

- embedders: `bge-small`, `bge-base`, `bge-large`
- operators: cosine, inner product, L2
- chunkers: hierarchy, sentence-aware, fixed 500/100

## Context Strategies

Validate these five strategies first:

| Strategy | Description | Purpose |
|---|---|---|
| `classic_chunks` | raw retrieved chunks, no summary | baseline |
| `chunk_summary_facts` | lede/facts summary over retrieved chunks | tests MHR/news finding |
| `doc_summary_facts` | lede/facts summary over full selected documents | tests SCOTUS/legal finding |
| `chunk_summary_toc_facts_plus_top5` | chunk summary + TOC/facts + raw top 5 chunks | tests focused hybrid context |
| `doc_and_chunk_summary_toc_facts_plus_top5` | document summary + chunk summary + TOC/facts + raw top 5 chunks | tests robust hedge |

Definitions must be strict:

- **Chunk summary** summarizes only the retrieved chunks.
- **Document summary** summarizes whole parent documents selected by retrieval.
- **TOC** means headings/case titles/section labels available from Chunkshop
  metadata, not a manually curated outline.
- **Facts** come from lede report JSON or Chunkshop extractors.
- **Top5** means the five highest-ranked raw chunks from the same retrieval run.

## Oracle Baselines

The oracle baseline needs a correction before any claims are made.

Run two oracle variants:

1. **Gold-support oracle**: send all benchmark-provided gold/supporting
   documents or contexts when those IDs are available.
2. **Touched-doc oracle**: send every parent document touched by the retrieved
   top-k chunks.

Do not cap the oracle at an arbitrary three documents unless the classic RAG
baseline is capped to the same document set. If the touched-doc oracle is too
large for the model context window, record it as `TOO_LARGE` and report the
token count instead of calling it an upper bound.

## Metrics

Per case:

- question
- workload and bucket
- retrieval settings
- context strategy
- selected document IDs
- retrieved chunk IDs
- generated answer
- expected answer
- required facts
- judge verdict and score
- judge rationale
- retrieval latency
- context build latency
- answer latency
- judge latency
- context token estimate
- token reduction vs classic chunks
- token reduction vs oracle when oracle is available
- provider `ERROR` status

Aggregate:

- pass rate
- mean judge score
- mean context tokens
- token reduction vs `classic_chunks@25`
- latency p50/p95
- `ERROR` row count
- win/loss vs baseline by question
- missing-fact categories

A run with substantial provider errors is invalid for accuracy claims.

## Audit Requirements

Every strategy/question pair must preserve:

- frozen JSONL input to `llm-judge`
- pre-judge audit markdown with retrieved chunks and packed context
- post-judge case markdown from `llm-judge`
- aggregate markdown and CSV reports

The audit should make these failure modes distinguishable:

- retrieval miss: required facts never retrieved
- compression loss: facts in raw chunks but absent from packed context
- answer failure: facts in packed context but absent from answer
- judge failure: semantically valid answer marked wrong
- oracle unfairness: oracle saw fewer relevant documents than RAG

## Proposed Workflow

### Phase 0: Smoke

Run a small smoke before spending judge time:

```bash
UV_CACHE_DIR=/tmp/uv-cache \
CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
uv run --project python --extra lede \
  python scripts/run_summary_density_validation.py \
  --out-dir skill-output/eval/summary-density-smoke \
  --limit-per-workload 5 \
  --skip-judge
```

Check:

- JSONL rows are generated.
- Audits show the right context strategy.
- Token counts are plausible.
- No context strategy accidentally receives more source documents than intended.

### Phase 1: Primary Validation

Run the five strategy comparison on the larger question sets:

```bash
UV_CACHE_DIR=/tmp/uv-cache \
CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
uv run --project python --extra lede \
  python scripts/run_summary_density_validation.py \
  --out-dir skill-output/eval/summary-density-validation \
  --workloads scotus,musique,twowiki \
  --top-k 10,25
```

The script should collect all contexts and audits first, then invoke
`llm-judge` with `cache_dir` and `resume`.

### Phase 2: Sensitivity

Only after Phase 1 is clean:

```bash
UV_CACHE_DIR=/tmp/uv-cache \
CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
uv run --project python --extra lede \
  python scripts/run_summary_density_validation.py \
  --out-dir skill-output/eval/summary-density-sensitivity \
  --workloads scotus,musique,twowiki \
  --strategies classic_chunks,chunk_summary_facts,doc_and_chunk_summary_toc_facts_plus_top5 \
  --embedders bge_small,bge_base,bge_large \
  --chunkers hierarchy,sentence_aware,fixed_500_100 \
  --vector-metrics cosine,inner_product,l2
```

This pass checks whether the Phase 1 recommendation survives changes in
embedder, chunker, and vector operator.

## Implementation Tasks

1. Add `scripts/run_summary_density_validation.py`.
   - Reuse the file-backed/resumable pattern from
     `scripts/run_deep_bucket_sweep.py`.
   - Generate frozen `llm-judge` JSONL before judging.
   - Keep answer generation and judging at the end.

2. Add config samples:
   - `docs/samples/eval/summary-density-validation.yaml`
   - `docs/samples/llm-judge/summary-density-local.yaml`

3. Add workload adapters:
   - SCOTUS fair buckets from `docs/samples/eval/scotus-50-query-buckets.yaml`
   - LongBench rows filtered to MuSiQue and 2Wiki
   - optional MHR/news rows if local data is present

4. Add context packers:
   - `classic_chunks`
   - `chunk_summary_facts`
   - `doc_summary_facts`
   - `chunk_summary_toc_facts_plus_top5`
   - `doc_and_chunk_summary_toc_facts_plus_top5`

5. Add oracle builders:
   - gold-support oracle when support IDs exist
   - touched-doc oracle from parent document IDs in retrieved chunks
   - explicit `TOO_LARGE` handling

6. Add reports:
   - per-workload strategy table
   - per-question win/loss table
   - token/latency Pareto table
   - failure-mode summary
   - oracle fairness diagnostics

## Decision Criteria

Promote a strategy to an easy mode only if it satisfies all of these:

- beats or ties `classic_chunks@25` on mean score;
- reduces context tokens by at least 40%, or has a clear accuracy gain that
  justifies higher cost;
- has no unexplained provider error rate;
- works on at least two workload families, or is explicitly labeled as a
  specialist mode;
- has audit examples showing why it wins and where it fails.

Candidate easy modes:

- `fast_focused`: `chunk_summary_facts` or
  `chunk_summary_toc_facts_plus_top5` if the MHR/MuSiQue result holds.
- `legal_doc`: `doc_summary_facts` if SCOTUS/legal results hold.
- `balanced_robust`: `doc_and_chunk_summary_toc_facts_plus_top5` if it remains
  the best cross-corpus hedge.
- `classic_rag`: raw chunks at `top_k=25` retained as the baseline mode.

## Open Questions

- Does the SCOTUS document-summary win hold beyond the 10-question Phase C
  sample?
- Does chunk-summary dominance hold on larger MuSiQue/2Wiki samples?
- Is `top_k=10` still best once the context strategy includes summaries plus
  top-5 raw chunks?
- Does inner product or L2 change the best context policy, or only retrieval
  ranking quality?
- Can metadata-filtered retrieval improve the hard SCOTUS questions enough to
  change the summary strategy recommendation?
- At what touched-doc oracle size does the answer model stop improving and
  start losing signal?
