# LLM-Judge Benchmark Evaluation

Chunkshop benchmark accuracy should use the public `llm-judge` utility for
answer-quality scoring and audit traces. Exact substring checks are useful as a
debug signal, but they are not the final accuracy metric.

## Install

Use the project helper to install `llm-judge` from GitHub into an isolated venv:

```bash
scripts/setup_llm_judge_venv.sh
```

The script uses `uv venv` when available and falls back to `python3 -m venv`.
It prints the `llm-judge` executable path.

## Convert Chunkshop Benchmark JSON

For an existing audited benchmark with generated answers:

```bash
scripts/chunkshop_to_llm_judge.py \
  --input skill-output/benchmarks/v05_audited_judge.json \
  --out .llm-judge-inputs/v05-audited.jsonl
```

For records that have context but should regenerate answers before judging:

```bash
scripts/chunkshop_to_llm_judge.py \
  --input skill-output/benchmarks/experiments/raw_clean.json \
  --out .llm-judge-inputs/e1e8-generate.jsonl \
  --blank-answers
```

The JSONL uses the `chunkshop-e1e8` profile and preserves question, retrieved
chunks/context, config label, generated answer when present, expected answer,
required facts, old judge/substr fields, and timing metadata.

## Smoke Run

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-smoke.yaml \
  --limit 12
```

Quick mode is deterministic and cheap. Use it to verify schema conversion,
resume behavior, and audit output. Omit `--limit` to score the full file.

## Accurate Local Run

Two local OpenAI-compatible judges are configured:

- `http://192.168.1.193:8000/v1`
- `http://192.168.1.133:8000/v1`

Run:

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-accurate-local.yaml
```

If answers need to be generated first:

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-generate-local.yaml
```

Both configs use `cache_dir: .llm-judge-cache` and `resume: true` for long
sweeps. The local accurate configs also set `max_tokens` and
`strict_json_fallback`, which are supported by current upstream `llm-judge`.
Provider failures produce `ERROR` rows and should not abort the run.
On this network, a local accurate probe should be checked for `ERROR` rows
before trusting aggregate accuracy; JSON-mode support varies by server/model.
When running from a sandboxed agent, the local API calls may need explicit
network permission even though the endpoints are LAN addresses.

To run a bounded accurate probe without creating a separate input file:

```bash
.venv-llm-judge/bin/llm-judge evaluate \
  --config docs/samples/llm-judge/chunkshop-accurate-local.yaml \
  --limit 2 \
  --out .llm-judge-runs/chunkshop-accurate-probe
```

## Optional Third Judge

The accurate configs include a commented OpenAI-compatible third judge. Enable
it by uncommenting the block and setting `OPENAI_API_KEY` in the environment.
Never write API keys into config files.

## Review Output

Each run writes:

- `.llm-judge-runs/<run>/summary.md`
- `.llm-judge-runs/<run>/results.jsonl`
- `.llm-judge-runs/<run>/cases/*.md`

When reporting benchmark results, include aggregate accuracy and concrete cases
where `llm-judge` disagrees with old exact-match or substring scoring.
