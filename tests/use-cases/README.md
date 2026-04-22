# tests/use-cases/ — business-problem scenarios

Self-contained scenarios labeled by **business problem**, not by feature exercised.
Each one pairs realistic fixture data with a chunkshop YAML config and a narrative
that explains *why* the chosen source / framer / chunker / embedder / extractor
combination fits that use case.

Sibling to `tests/sub/`:

| Directory | Axis | Answers the question |
|---|---|---|
| `tests/sub/scenarios/` | **Functional** — one scenario per feature path | "Does the jsonpath framer still work end-to-end?" |
| `tests/use-cases/scenarios/` | **Use-case** — one scenario per business journey | "Which picks do I use for helpdesk search? Why those and not the others?" |

Both directories feed the same shape of runner (`run-all.sh`) and the same
shape of asserter (`assert-row-counts.py`), so CI can drive them identically.

## Layout

```
tests/use-cases/
├── README.md                 # this file
├── run-all.sh                # loop over scenarios, ingest each, fail fast
├── assert-row-counts.py      # post-run DB assertions from each scenario's expected.json
└── scenarios/
    └── NN-slug/
        ├── README.md         # persona + goal + why-these-picks + trade-off + run + query
        ├── data/             # realistic synthetic fixtures (whole scenario <10 KB)
        ├── config.yaml       # the cell config
        └── expected.json     # row-count + required-metadata-key assertions
```

Each scenario README follows a fixed structure:

1. **Persona** — who's running this, in one line.
2. **Goal** — what they want retrieval to do, in plain English.
3. **Why these picks** — per-axis reasoning (source, framer, chunker, embedder, extractor, schema-flex mode).
4. **The trade-off we made** — what we optimized for, what we gave up.
5. **How to run it** — the exact `chunkshop ingest --config ...` command.
6. **What you'd query** — a realistic SQL snippet showing how an app would consume the table.

## How to run one scenario

From repo root:

```bash
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
cd python && uv run chunkshop ingest --config ../tests/use-cases/scenarios/01-support-helpdesk/config.yaml
```

Relative globs in each `config.yaml` resolve from the **repo root**, matching
the `docs/samples/*.yaml` convention. Always invoke from repo root (or
`cd python` and use `../tests/use-cases/...`).

## How to run all scenarios

```bash
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
bash tests/use-cases/run-all.sh
python tests/use-cases/assert-row-counts.py     # optional: check row counts match expected.json
```

All scenarios write to schema `chunkshop_use_cases`. Cleanup is one drop:

```sql
DROP SCHEMA chunkshop_use_cases CASCADE;
```

If `$CHUNKSHOP_TEST_DSN` is unset, both scripts exit 0 with a skip message —
safe for CI matrices that don't always have Postgres attached.

## How CI uses it

Same shape as `tests/sub/` — a single shell command plus an assertion pass:

```yaml
- name: Run chunkshop use-case scenarios
  env:
    CHUNKSHOP_TEST_DSN: ${{ secrets.CI_PG_DSN }}
  run: |
    bash tests/use-cases/run-all.sh
    python tests/use-cases/assert-row-counts.py
```

Non-zero exit from `run-all.sh` fails the step and prints the failing cell's
log. `assert-row-counts.py` enforces each scenario's declared minimum row count
and required metadata keys so silent "ingest exited 0 but wrote nothing"
regressions surface here instead of later.

## How to add a scenario

1. `mkdir tests/use-cases/scenarios/NN-slug/{,data}` where `NN` is the next number.
2. Drop realistic synthetic fixtures under `data/` — write fresh prose that reads like the stated persona's real world (no Wikipedia excerpts, no borrowed copy). Keep the whole scenario under 10 KB.
3. Write `config.yaml` with:
   - `target.schema: chunkshop_use_cases`
   - `target.mode: overwrite` (each scenario is self-contained)
   - `target.hnsw: false`
   - `runtime.log_path: /tmp/chunkshop_usecase_<slug>.log`
4. Write `expected.json` with `table`, `min_rows`, `min_docs`, and optional `metadata_keys_required`.
5. Write `README.md` following the six-section structure above. The *Why these picks* section must state the **why**, not just the **what** — the purpose of this library is to teach which picks map to which use cases.
6. `bash tests/use-cases/run-all.sh` to confirm the new scenario ingests cleanly.

## The scenario library

| # | Slug | Persona | Headline pick |
|---|---|---|---|
| 01 | `support-helpdesk` | Support engineer (B2B SaaS) | `hierarchy` + `bge-small-int8` — latency over +1 MTEB point |
| 02 | `legal-clause-review` | Paralegal | `sentence_aware` + `bge-base-int8` + `spacy_entities` — recall + filter-by-org |
| 03 | `dev-api-docs-rag` | DevTools PM | `sentence_aware` (max_chars 5000) + `nomic-Q` — 8k-token context keeps doc page intact |
| 04 | `research-paper-library` | ML researcher | `hierarchy` + `bge-base-int8` + `spacy_entities` promoted — filter-by-ORG + similarity |
| 05 | `sales-meeting-notes` | RevOps analyst | `neighbor_expand` + `bge-base-int8` + `lang_detect` — context 1 turn before/after |
| 06 | `ecommerce-catalog` | Merchandiser | `jsonpath` framer + `bge-small-int8` + `keybert_phrases` — badge rendering |
