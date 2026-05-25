# Testing and Benchmarking Tools

Chunkshop uses two benchmark tracks:

- **Showcase runs**: small, stable, easy-to-repeat demos for release notes,
  talks, and "what does this buy me?" comparisons.
- **Deep evaluation runs**: larger matrices for regressions, default-setting
  decisions, retrieval tuning, and workload-specific recommendations.

Both tracks should preserve inspectable per-case artifacts. Aggregate accuracy
without the question, retrieved context, generated answer, expected answer,
judge verdict, rationale, and timing is not enough to trust.

## Tooling Map

### `chunkshop bakeoff`

Use for customer/user-facing comparisons and retrieval-focused experiments. It
is the right tool when the question is "compare these retrieval settings on
this corpus" and the output needs to be readable by someone outside the
project.

Typical uses:

- Compare semantic, FTS, and hybrid search.
- Compare pgvector operators: cosine, inner product, and L2.
- Compare chunkers and top-k settings.
- Export retrieval traces that can later be judged.

### `chunkshop eval`

Use for planning repeatable evaluation matrices. The sample matrix files live
under `docs/samples/eval/`.

Current commands:

```bash
cd python
uv run chunkshop eval validate --config ../docs/samples/eval/showcase-matrix.yaml
uv run chunkshop eval plan \
  --config ../docs/samples/eval/deep-matrix.yaml \
  --out ../skill-output/eval/deep-plan
```

The `eval` layer is the long-term first-class CLI surface for standard testing
profiles such as `general_default`, `accuracy_max`, `low_cost`, `codebase_qa`,
and `multihop_enumeration`.

### `lede --mode report`

Use at ingest time to create compact human summaries and full-fidelity machine
metadata.

Human/debug output:

```bash
lede doc.md --mode report --output markdown --max-chars 4000 --max-facts 40
```

Machine ingest output:

```bash
lede doc.md --mode report --output json --max-chars 4000 --max-facts 40
```

For Chunkshop ingest, store the JSON under `metadata.lede_report`, promote
stable attributes such as term, docket number, and citation into SQL columns,
and include `metadata.lede_report.search_text` in FTS.

### `llm-judge`

Use for answer-quality scoring and audit traces. Exact substring scoring is
only a debug signal; it is not the final metric.

Install or refresh:

```bash
scripts/setup_llm_judge_venv.sh
```

Run with a config:

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-generate-local.yaml
```

Use `quick` mode for smoke tests and cheap CI checks. Use `accurate` mode for
final reports, disputed cases, and release decisions.

Important rules:

- Use `--cache-dir` and `--resume` for long sweeps.
- Generate answers before judging when records have retrieved context but no
  candidate answer.
- Provider failures should become `ERROR` rows and should not abort the run.
- Do not store API keys in files. Use environment variables only.

### `scripts/run_deep_bucket_sweep.py`

This is the current concrete deep-run harness while the first-class
`chunkshop eval run` command is still being shaped.

For the focused follow-up on summary source, density, top-5 raw chunks, and
fair oracle baselines, see `docs/summary-density-validation-plan.md`.
For the product-facing follow-up on KB-specific cheap↔accurate profile ladders,
see `docs/kb-profile-ladder-plan.md`.
For the Phase D findings, recommendations, and next-step checklist, see
`docs/phase-d-findings-next-steps.md`.

Default guidance from the current work: do not treat a namespace as a random
bag of documents. A namespace/KB is a retrieval contract. SCOTUS-like legal
corpora, MHR-like news/multi-hop corpora, and code/documentation corpora should
normally use separate namespaces or tables so each can have its own metadata,
indexes, and profile defaults.

It runs in phases:

1. Ingest SCOTUS once per embedder, chunker, and pgvector operator.
2. Retrieve all 50 SCOTUS bucket questions for every context and hint policy.
3. Build per-case markdown audits and frozen JSONL inputs.
4. Build a LongBench matrix from a JSONL input.
5. Run `llm-judge` at the end against the frozen JSONL.
6. Write aggregate markdown and CSV summaries.

Current run:

```bash
setsid -f /bin/bash -lc \
  'cd /home/yonk/yonk-tools/chunkshop &&
   exec env UV_CACHE_DIR=/tmp/uv-cache \
     CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
     uv run --project python --extra lede \
       python scripts/run_deep_bucket_sweep.py \
       --out-dir skill-output/eval/deep-bucket-sweep-20260524 \
       --longbench-input data/benchmarks/longbench-stratified-50.jsonl \
       --force-ingest \
       >> skill-output/eval/deep-bucket-sweep-20260524/nohup.log 2>&1'
```

The current sweep covers:

- **SCOTUS**: 50 questions across five difficulty/fairness buckets.
- **LongBench**: a stratified 50-record slice from the full 8,418-record
  LongBench download.
- **Embedders**: `bge-small`, `bge-base`, `bge-large`.
- **Chunkers**: hierarchy, sentence-aware, fixed 500/100 overlap.
- **Vector operators**: cosine, inner product, L2.
- **Context policies**: summary + metadata, summary + metadata + headers.
- **Hints**: off and on.

That is 108 configs per workload, or 5,400 SCOTUS rows and 5,400 LongBench rows
before judging.

## Setup

### Local services

Postgres test database:

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
psql "$CHUNKSHOP_TEST_DSN" -c 'select current_database(), now();'
```

Local OpenAI-compatible LLM endpoints:

- `http://192.168.1.193:8000/v1`
- `http://192.168.1.133:8000/v1`

These are used by the generated `llm-judge` configs for answer generation and
accurate judging. A third OpenAI-compatible cloud judge can be added with an
environment variable such as `OPENAI_API_KEY`, but the key should never be
written into YAML.

### Python environment

Use the Python project environment for Chunkshop commands:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --project python --extra lede chunkshop --help
```

Use the isolated judge venv for `llm-judge`:

```bash
scripts/setup_llm_judge_venv.sh
.venv-llm-judge/bin/llm-judge profiles
```

### Data

SCOTUS source files:

```text
/home/yonk/yonk-tools/pg-raggraph/benchmarks/scotus/*.md
```

SCOTUS question fixture:

```text
docs/samples/eval/scotus-50-query-buckets.yaml
```

LongBench source:

```text
data/benchmarks/longbench.jsonl
data/benchmarks/longbench-stratified-50.jsonl
```

The downloaded LongBench JSONL files are ignored by git.

## Output Layout

A deep run writes everything under `skill-output/eval/<run-name>/`:

```text
manifest.json
configs/scotus-ingest/*.yaml
configs/llm-judge/*.yaml
logs/ingest/*.log
logs/llm-judge-*.log
inputs/scotus-50-matrix.jsonl
inputs/longbench-matrix.jsonl
audits/scotus/*.md
audits/longbench/*.md
llm-judge-cache/
llm-judge-runs/scotus-50-matrix/
llm-judge-runs/longbench-matrix/
reports/scotus-50-summary.md
reports/scotus-50-summary.csv
reports/longbench-summary.md
reports/longbench-summary.csv
```

The important audit files are:

- `inputs/*.jsonl`: frozen judge inputs. These are the reproducible cases.
- `audits/*/*.md`: retrieved chunks, packed context, settings, expected answer,
  required facts, token estimates, and timing before judging.
- `llm-judge-runs/*/cases/*.md`: generated answer, judge verdict, rationale,
  and provider timings after judging.
- `reports/*.md` and `reports/*.csv`: aggregate tables by bucket and config.

## Use Cases

### 1. Release smoke test

Purpose: check that the eval machinery still works before a release.

Workflow:

```bash
scripts/setup_llm_judge_venv.sh
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-smoke.yaml \
  --limit 12
```

Expected output:

```text
.llm-judge-runs/<run>/summary.md
.llm-judge-runs/<run>/results.jsonl
.llm-judge-runs/<run>/cases/*.md
```

Trust condition: `ERROR` rows should be zero or very close to zero. A run with
provider/runtime errors is a systems failure, not an accuracy result.

### 2. Retrieval/default-setting sweep

Purpose: decide which defaults are defensible across embedders, chunkers,
operators, context packers, and hints.

Workflow:

```bash
UV_CACHE_DIR=/tmp/uv-cache \
CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
uv run --project python --extra lede \
  python scripts/run_deep_bucket_sweep.py \
  --out-dir skill-output/eval/deep-bucket-sweep-local \
  --longbench-input data/benchmarks/longbench-stratified-50.jsonl
```

Expected output:

```text
skill-output/eval/deep-bucket-sweep-local/reports/scotus-50-summary.md
skill-output/eval/deep-bucket-sweep-local/reports/longbench-summary.md
```

Use the markdown report for a quick read and the CSV for sorting/filtering.
The recommendation should weigh answer score, pass rate, token reduction,
latency, and failure patterns by bucket.

### 3. Bad-case diagnosis

Purpose: inspect a single failure without trusting the aggregate.

Workflow:

1. Open `reports/scotus-50-summary.csv` and find a low-scoring bucket/config.
2. Open the matching pre-judge audit under `audits/scotus/`.
3. Open the matching post-judge case under `llm-judge-runs/scotus-50-matrix/cases/`.
4. Decide whether the problem is retrieval, context compression, answer
   generation, or judge strictness.

Useful questions:

- Did the retrieved chunks contain the required fact?
- Did the packed summary preserve the fact?
- Did metadata or `lede_report.search_text` contain the fact?
- Did the answer model ignore available context?
- Did the judge reject a valid paraphrase?

## Example Questions and Outputs

### Example A: impossible for top-k RAG

Question:

```text
Which Supreme Court terms in this corpus list Justice Ketanji Brown Jackson as a justice?
```

Expected answer:

```text
2022 and 2023.
```

Why this is special:

This is not a fair pure semantic top-k question. With a million files, top-k
retrieval cannot prove that no other term contains a matching document. The
right path is promoted metadata or SQL first, then RAG only to explain/support
the deterministic result.

Expected workflow:

```sql
select distinct metadata->'lede_report'->'attributes'->'term'->>'value' as term
from chunkshop_lede_report_scotus.chunks
where metadata->'lede_report'->>'search_text' ilike '%Ketanji Brown Jackson%'
order by term;
```

Example audit outcome:

```text
bucket: impossible_for_llm_topk_rag
retrieval_contract: exhaustive_metadata_query
rag_applicable: false
verdict expectation: do not use top-k semantic RAG score as default failure
```

### Example B: hard metadata/multi-hop RAG

Question:

```text
Which justices dissented in both 303 Creative LLC v. Elenis and Snyder v. United States?
```

Expected answer:

```text
Sonia Sotomayor, Elena Kagan, and Ketanji Brown Jackson.
```

Expected workflow:

1. Retrieve or filter to both source cases.
2. Pack summary + metadata + headers.
3. Generate an answer from the packed context.
4. Judge against the gold answer and required facts.

Example frozen JSONL shape:

```json
{
  "id": "scotus-b50-013::bge_base__hierarchy__cosine__summary_meta_headers__hints_on",
  "question": "Which justices dissented in both 303 Creative LLC v. Elenis and Snyder v. United States?",
  "gold_answer": "Sonia Sotomayor, Elena Kagan, and Ketanji Brown Jackson.",
  "required_facts": [
    "303 Creative dissent included Sotomayor, Kagan, and Jackson.",
    "Snyder dissent included Jackson, Sotomayor, and Kagan.",
    "The overlap is Sotomayor, Kagan, and Jackson."
  ],
  "retrieved_chunks": ["SUMMARY:\n...\n\nMETADATA AND FACTS:\n..."],
  "answer": "",
  "config_label": "bge_base__hierarchy__cosine__summary_meta_headers__hints_on"
}
```

Example judged case summary:

```text
verdict: CORRECT
score: 1.0
rationale: The answer lists the three overlapping dissenters and does not add
contradictory justices.
```

### Example C: LongBench standard workload

Question shape:

```text
LongBench row with input/question, long context, and benchmark answers.
```

Expected workflow:

1. Chunk the provided LongBench context in memory.
2. Embed the chunks and the question.
3. Rank chunks with cosine, inner product, or L2.
4. Pack summary + metadata or summary + metadata + headers.
5. Generate an answer with `llm-judge`.
6. Judge against the benchmark-provided `answers`.

Example matrix label:

```text
bge_large__sentence_aware__l2__summary_meta_headers__hints_off
```

Example aggregate output row:

```text
bucket/dataset: hotpotqa
config: bge_large__sentence_aware__l2__summary_meta_headers__hints_off
cases: 2
accuracy: 50.0%
score: 0.625
avg_context_tokens: 780
avg_token_reduction: 88.4%
```

The numbers above show the report shape, not a completed benchmark result.
Only use the generated `reports/*.md` and `reports/*.csv` from the completed
run for real claims.

## How to Interpret Results

Do not rank configs by accuracy alone.

Use these checks:

- **Bucket sanity**: impossible/corpus-wide questions should be judged
  separately from fair top-k semantic questions.
- **Retrieval first**: if required facts never enter the retrieved context,
  summary length and answer model changes are noise.
- **Compression loss**: if raw retrieved chunks contain the fact but packed
  summary does not, the context policy is the issue.
- **Answer failure**: if packed context contains the fact and the generated
  answer misses it, the answer model or prompt is the issue.
- **Judge failure**: if the answer is semantically right but marked wrong,
  inspect the case markdown and rerun accurate/dual judging.
- **Cost/latency tradeoff**: the best default should be near the Pareto frontier,
  not merely the most expensive high-score config.

## What Gets Reported

Final benchmark writeups should include:

- top configs overall;
- top configs by bucket/workload;
- accuracy, mean score, token reduction, and latency;
- deltas against raw/full-context and classic RAG baselines when available;
- failure-pattern examples;
- exact paths to `summary.md`, `results.jsonl`, and `cases/*.md`;
- `ERROR` row count.

A result with a high or unexplained `ERROR` count is invalid for accuracy
claims.
