# tests/sub/ — scenario library

Self-contained scenarios that pair input data with a chunkshop YAML config.
Each scenario exercises a specific feature (or combination). Doubles as:

- **Demos**: "how do I use the jsonpath framer?" → point at `scenarios/04-framer-jsonpath-nested/`.
- **CI smoke tests**: `run-all.sh` drives every scenario end-to-end through a real Postgres.

Not a replacement for `pytest` in `python/tests/` — that's the unit/integration suite.
This is coarser: it answers "does the tool still run end-to-end on each supported feature path?"

## Layout

```
tests/sub/
├── README.md                 # this file
├── run-all.sh                # loop over scenarios, ingest each, fail fast
├── assert-row-counts.py      # post-run DB assertions from each scenario's expected.json
└── scenarios/
    └── NN-slug/
        ├── README.md         # 4–6 lines: what this exercises
        ├── data/             # fixture inputs (docs, JSON corpora)
        ├── config.yaml       # the cell config
        └── expected.json     # row-count assertions for the asserter
```

## How to run one scenario

From repo root:

```bash
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
cd python && uv run chunkshop ingest --config ../tests/sub/scenarios/01-markdown-hierarchy-default/config.yaml
```

Relative globs in each `config.yaml` are written to resolve from the **repo root**,
matching the `docs/samples/*.yaml` convention. Always invoke from repo root (or
`cd python` and use `../tests/sub/...`).

## How to run all scenarios

```bash
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
bash tests/sub/run-all.sh
python tests/sub/assert-row-counts.py     # optional: check row counts match expected.json
```

All scenarios write to schema `chunkshop_sub_scenarios`. Cleanup is one drop:

```sql
DROP SCHEMA chunkshop_sub_scenarios CASCADE;
```

If `$CHUNKSHOP_TEST_DSN` is unset, both scripts exit 0 with a skip message (safe
to run in CI matrices that don't always have Postgres).

## How to add a new scenario

1. `mkdir tests/sub/scenarios/NN-slug/{,data}` where `NN` is the next number.
2. Drop fixture files under `data/` (keep each under ~1 KB; whole scenario under 10 KB).
3. Write `config.yaml` following the shape below.
4. Write `expected.json` with `min_rows`, `min_docs`, and optional `metadata_keys_required`.
5. Write `README.md` explaining which feature(s) this scenario exercises in 4–6 lines.
6. `bash tests/sub/run-all.sh` to confirm it ingests cleanly.

### Config shape (copy-paste starting point)

```yaml
cell_name: sub_<slug>
source:
  type: files
  glob: tests/sub/scenarios/NN-slug/data/*.md
  id_from: stem
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 2
target:
  dsn_env: CHUNKSHOP_TEST_DSN
  schema: chunkshop_sub_scenarios
  table: <scenario_table>
  mode: overwrite
  hnsw: false
runtime:
  log_path: /tmp/chunkshop_sub_<slug>.log
```

Always use:
- `target.schema: chunkshop_sub_scenarios` (single schema, one drop to clean up)
- `embedder.dim: 768` with `Xenova/bge-base-en-v1.5-int8` (current default)
- `hnsw: false` (corpora are tiny — HNSW build is slower than seq scan)
- `runtime.log_path: /tmp/...` (keeps logs out of the repo)

## How CI uses it

Suggested GitHub Actions step (not wired here — this directory is the
ingredient, not the workflow):

```yaml
- name: Run chunkshop sub-scenarios
  env:
    CHUNKSHOP_TEST_DSN: ${{ secrets.CI_PG_DSN }}
  run: |
    bash tests/sub/run-all.sh
    python tests/sub/assert-row-counts.py
```

Any non-zero exit from `run-all.sh` fails the step; logs of the failing scenario
are printed to stdout for the CI log viewer. `assert-row-counts.py` fails if the
resulting table is below each scenario's declared floor (catches silent "ingest
exited 0 but wrote nothing" regressions).
