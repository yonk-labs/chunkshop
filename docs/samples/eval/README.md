# Evaluation Matrix Samples

These files describe the intended higher-level RAG evaluation harness shape.
They are not consumed directly by today's `chunkshop bakeoff` command.

- `showcase-matrix.yaml`: small, stable benchmark set for demos, release notes,
  and release gates.
- `deep-matrix.yaml`: broad regression/search matrix covering workloads,
  embedders, chunkers, pgvector operators, retrieval modes, context packers,
  answer generation, and `llm-judge` scoring.
- `scotus-50-query-buckets.yaml`: 50 SCOTUS questions split into five
  difficulty/fairness buckets, including explicit corpus-aggregation questions
  that should be scored through metadata/SQL rather than top-k semantic RAG.

The next harness layer should expand these configs into concrete retrieval
runs, answer-generation runs, `llm-judge` configs, and reports.

The deep matrix should include both custom corpora and industry benchmarks.
SCOTUS and the PG code base cover project-specific behavior; RAGAS, LoCoMo,
and LongBench keep the recommendations honest against standard workloads.

Current CLI:

```bash
cd python
uv run chunkshop eval validate --config ../docs/samples/eval/showcase-matrix.yaml
uv run chunkshop eval plan \
  --config ../docs/samples/eval/showcase-matrix.yaml \
  --out ../skill-output/eval/showcase-plan
```

Use `--profile general_default` or another profile name with
`deep-matrix.yaml` to plan a specific easy mode.

To materialize the runnable 30-question SCOTUS retrieval input:

```bash
cd python

# Optional but recommended for lede-report facts in summary_toc_facts:
CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test \
  uv run chunkshop ingest \
  --config ../docs/samples/eval/scotus-lede-report-ingest.yaml

cd ..
scripts/scotus_retrieval_to_llm_judge.py \
  --schema chunkshop_lede_report_scotus \
  --out .llm-judge-inputs/scotus-30-retrieval.jsonl
```

Then run the generated-answer accurate judge:

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/scotus-30-generate-local.yaml
```
