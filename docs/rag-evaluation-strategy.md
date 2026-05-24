# RAG Evaluation Strategy

Chunkshop needs two benchmark tracks:

1. **Showcase benchmarks**: small, stable, explainable runs for demos,
   release notes, and marketing. These should answer "look what this does"
   with a clean story and a short reproduction path.
2. **Deep evaluation harness**: a larger repeatable matrix for regressions,
   pattern discovery, best-practice recommendations, and workload-specific
   tuning.

The showcase track should stay curated. The deep harness should be broad,
auditable, and boringly repeatable.

## Evaluation Ladder

Every workload should move through the same ladder:

1. **Corpus normalization**: load files, benchmark datasets, database rows, or
   pg-raggraph cells into a common document model with stable IDs.
2. **Question set**: use benchmark gold answers when available; otherwise
   generate reference answers from oracle/full context with `llm-judge
   --generate-expected`.
3. **Baselines**:
   - **Oracle/full-context baseline**: send all relevant source document text
     to the answer model. If this cannot answer correctly, the question,
     source, or gold answer is suspect.
   - **Classic RAG baseline**: embed/query chunks and send top-N chunks to the
     answer model. This is the practical baseline every candidate must beat or
     justify trading off against.
4. **Candidate matrix**: run configured retrieval, context-packing,
   summarization, and answer-generation combinations.
5. **Judging**: use `llm-judge` quick mode for smoke/regression gates and
   accurate mode for final reports or disputed cases.
6. **Reporting**: rank candidates by correctness, fact coverage, recall, token
   cost, latency, and savings/delta against both baselines.

## Baseline Definitions

### Baseline A: Oracle / Full-Context

Purpose: determine whether the model can answer the question when retrieval is
removed as a variable.

Process:

1. Identify the oracle context:
   - benchmark-provided context for RAGAS, LoCoMo, LongBench, HotpotQA,
     MuSiQue, TwoWiki, etc.
   - full source document(s) for custom corpora such as SCOTUS or the PG code
     base.
2. If no gold answer exists, generate a reference answer from this oracle
   context using one to three providers through `llm-judge --generate-expected`.
3. Generate the candidate answer from the same oracle context.
4. Judge against the benchmark gold or generated reference.

This baseline answers: "what is the ceiling if context budget were free?"

### Baseline B: Classic Chunk RAG

Purpose: provide the familiar practical baseline.

Default process:

1. Chunk at 500 tokens with overlap appropriate to the chunker.
2. Retrieve top-N chunks using semantic search.
3. Start with `top_k=25`; sweep `top_k=[10,25,50,100,250]` on deep runs.
4. Send retrieved chunks directly to the answer model.
5. Judge with `llm-judge`.

This baseline answers: "what does conventional RAG cost and achieve?"

### Optional Baseline C: Full-Document After Semantic Doc Match

Purpose: measure the tradeoff between full context and retrieval selectivity.

Process:

1. Search semantically over chunks.
2. Promote top matching chunks to their parent document IDs.
3. Send one or more full source documents to the answer model.
4. Sweep doc count, for example `top_docs=[1,3,5]`.

This is often expensive, but it can be a strong baseline for corpora with
moderate document sizes and high intra-document locality.

## Matrix Axes

The deep harness should treat every candidate as a named policy with explicit
axes. Do not hide defaults in code when they affect interpretation.

Core axes:

- **Workload**: SCOTUS, PG code base, RAGAS, LoCoMo, LongBench, HotpotQA,
  MuSiQue, TwoWiki, pg-raggraph workloads.
- **Embedder**: bge-small, bge-base, bge-large, nomic, domain models, local
  service embedders.
- **Chunking**: hierarchy, sentence-aware, fixed-overlap, semantic,
  neighbor-expand, recursive/code-aware strategies.
- **Vector operator**: cosine, inner product, L2.
- **Retrieval mode**: semantic, FTS, hybrid, metadata/predicate filtered,
  pg-raggraph naive/hybrid/other modes.
- **Candidate set**: top-k chunks, top-k docs, rerank candidate size.
- **Reranker**: none, cross-encoder, LLM rerank, heuristic rerank.
- **Query expansion**: none, synonym, HyDE, multi-query, decomposition.
- **Context packer**: full documents, chunks, summaries, summary+TOC,
  summary+TOC+facts, raw+summary hybrid.
- **Summarization**: none, lede, extractive, key facts, heading-aware,
  per-document vs cross-document.
- **Answer model**: local Qwen, local Gemma, OpenAI-compatible cloud model,
  small/cheap model, larger accurate model.
- **Judge mode**: quick, accurate, dual; one to three judges.

The matrix should support tags so large sweeps can be selected by intent:

- `showcase`: stable demo cells.
- `smoke`: cheap CI sanity cells.
- `nightly`: broad regression cells.
- `expensive`: large matrices and accurate judging.
- `release-gate`: cells that must not regress before shipping.

## Required Metrics

Every run should preserve raw per-case traces and aggregate:

- **answer_accuracy**: pass rate from `llm-judge`.
- **answer_score**: judge score, not just binary pass.
- **fact_coverage**: supported required facts / total required facts.
- **retrieval_recall**: whether required facts or gold document are present in
  retrieved context.
- **context_recall**: required facts present in the context sent to the model.
- **unsupported_claims**: hallucinated or contradicted facts.
- **token_input** and **token_output**.
- **token_savings_vs_oracle**.
- **token_savings_vs_classic_rag**.
- **latency_retrieval_ms**.
- **latency_context_build_ms**.
- **latency_answer_ms**.
- **latency_judge_ms**.
- **cost_estimate** when model pricing is known.
- **delta_vs_oracle** and **delta_vs_classic_rag** for score, tokens, latency,
  and cost.

For retrieval-only bakeoffs, keep recall@k and MRR. For answer-quality runs,
use them as diagnostics, not the final accuracy metric.

## Gold / Reference Policy

When benchmark gold exists, preserve it.

When gold does not exist:

1. Generate the reference from oracle/full context, not from retrieved chunks.
2. Use `llm-judge --generate-expected`.
3. Use up to three providers or repeated samples for important workloads.
4. Preserve acceptable answers, required facts, provider metadata, rationale,
   and timing.
5. Treat generated references as versioned artifacts. Do not silently overwrite
   them between runs.

Question granularity matters. A broad question such as "Where was Matt born?"
can accept "Michigan", "Grand Rapids", or "St. Mary's Hospital" depending on
the expected specificity. A question asking "what city and hospital" requires
both the city and hospital; city-only should be partial.

## Question Difficulty Buckets

Question sets should label whether top-k RAG is a fair tool for the job. A
single aggregate score across mixed question types hides retrieval failures and
also unfairly punishes semantic search for questions that require exhaustive
metadata.

Use five buckets:

- **impossible_for_llm_topk_rag**: complete corpus aggregation, absence checks,
  global counts, and set intersections. Score these through promoted metadata,
  SQL, or another exhaustive path first; use RAG only to explain/support the
  deterministic result.
- **hard_llm_metadata_rag**: cross-document comparisons, multi-hop synthesis,
  and questions that benefit from metadata filters, decomposition, or larger
  candidate sets before answer generation.
- **medium_semantic_rag**: normal answerable RAG questions where semantic/FTS
  retrieval should find the right case or passage.
- **easy_semantic_rag**: direct single-case facts that should be robust under
  ordinary hybrid retrieval.
- **layup_summary**: header, lede, or summary facts that should survive compact
  summary-based context packing.

The SCOTUS fixture in `docs/samples/eval/scotus-50-query-buckets.yaml` is the
first concrete example. Industry workloads such as LongBench should map their
provided tasks into the same reporting buckets when possible, while preserving
the benchmark's own gold answers and task labels.

## Reporting

Every deep run should produce:

- **Executive summary**: top combos and clear recommendations.
- **Pareto table**: accuracy vs token spend vs latency.
- **Baseline comparison**: savings and deltas vs oracle and classic RAG.
- **Specialist recommendations**: 5-6 named profiles such as "cheap default",
  "highest accuracy", "code corpus", "long conversation", "multi-hop QA",
  "low latency".
- **Regression table**: changes against the previous accepted baseline.
- **Failure patterns**: grouped missing facts, retrieval misses, context
  compression losses, answer-model failures, judge disagreements.
- **Inspectable audits**: one Markdown audit per question/candidate.

Suggested recommendation profiles:

- **general_default**: best balanced score/cost/latency.
- **accuracy_max**: highest answer score regardless of cost.
- **low_cost**: best score under a token/cost budget.
- **low_latency**: best score under a latency budget.
- **codebase_qa**: optimized for code/documentation corpora.
- **multihop_enumeration**: query expansion/decomposition plus larger
  candidate set.
- **executive_showcase**: stable, easy-to-explain, high-confidence cells.

## CI / Schedule

Use tiered execution:

- **PR smoke**: small fixture corpus, quick judge, `--limit`, no external paid
  API required.
- **Nightly regression**: representative workloads, broader matrix, local
  judges, resume/cache enabled.
- **Weekly deep sweep**: large matrix, accurate judging, top-k sweeps,
  rerankers, generated references where needed.
- **Release gate**: showcase suite plus selected deep harness sentinels.

Provider/runtime failures should create `ERROR` rows and continue. A run with
too many `ERROR` rows is invalid for accuracy reporting.

## First Implementation Shape

Do not replace `chunkshop bakeoff`. Reuse it for the retrieval-only layer and
add an answer-evaluation layer around its outputs.

Recommended artifacts:

- `docs/samples/eval/showcase-matrix.yaml`: small curated showcase matrix.
- `docs/samples/eval/deep-matrix.yaml`: larger tagged matrix.
- `chunkshop eval plan`: expands matrix YAML into concrete runs.
- `scripts/chunkshop_to_llm_judge.py`: converts run traces into `llm-judge`
  JSONL.
- `.llm-judge-runs/`: ignored audit output.
- `.llm-judge-cache/`: ignored provider cache.

The harness runner should execute phases independently so interrupted work can
resume:

1. `prepare-corpus`
2. `generate-references`
3. `run-baselines`
4. `run-candidates`
5. `judge`
6. `report`

Each phase should write immutable JSONL/JSON artifacts keyed by workload,
policy, corpus version, model version, and timestamp.

## Current Harness Entry Points

The first implementation slice is available through `chunkshop eval`:

```bash
cd python
uv run chunkshop eval validate --config ../docs/samples/eval/showcase-matrix.yaml
uv run chunkshop eval plan \
  --config ../docs/samples/eval/showcase-matrix.yaml \
  --out ../skill-output/eval/showcase-plan \
  --smoke-limit 12
```

`eval validate` checks the matrix shape and expansion count. `eval plan`
writes:

- `manifest.json`: expanded workloads, baselines, candidates, profile policies,
  and workload-policy runs.
- `report.md`: human-readable plan summary.
- `llm-judge/*.yaml`: smoke/final judge configs for workloads that already
  have an input file and profile.

For deep easy-mode planning:

```bash
uv run chunkshop eval plan \
  --config ../docs/samples/eval/deep-matrix.yaml \
  --profile general_default \
  --out ../skill-output/eval/general-default-plan
```

The next implementation layer should execute the plan phases: retrieval,
context packing, answer generation, `llm-judge`, and final comparative report.

The SCOTUS runner is the first concrete retrieval/materialization step:

```bash
scripts/scotus_retrieval_to_llm_judge.py \
  --out .llm-judge-inputs/scotus-30-retrieval.jsonl

.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/scotus-30-generate-local.yaml
```

It produces `30 questions × top_k values × context policies`, with clean answer
generation from each candidate context instead of reusing old saved answers.
