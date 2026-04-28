# chunkshop (Python)

Reference implementation of the chunkshop ingest tool. v0.2.0, alpha.

**New here?** Start with the [**end-to-end tutorial**](../docs/tutorial.md) — a guided
walkthrough from zero (no Postgres) to a running semantic query.

This file is the field-by-field reference: every CLI flag, every YAML field, the
troubleshooting table. Use it alongside the tutorial once you know what you're doing.

For the high-level shape and mermaid diagram, see the [top-level README](../README.md).

## Install

From source (recommended while alpha):

```bash
cd chunkshop/python
uv sync --extra dev
```

As a path dependency from another project:

```toml
[tool.uv.sources]
chunkshop = { path = "../chunkshop/python", editable = true }
```

Optional extras:

| Extra        | What you get                                                         |
|--------------|----------------------------------------------------------------------|
| `extractors` | `rake-nltk` + `nltk` for the RAKE extractor.                         |
| `keybert`    | `keybert` + `sentence-transformers` for the `keybert_phrases` extractor. |
| `spacy`      | `spacy` for the `spacy_entities` NER extractor.                      |
| `lang`       | `langdetect` for the `lang_detect` extractor.                        |
| `nlp`        | Umbrella: `keybert` + `spacy` + `lang` in one install.               |
| `lede`      | Sibling `extractive_summary` repo as a path dep — enables `summary_embed` with `lede.tfidf.summarize`. |
| `sumy`       | `sumy` + NLTK corpora for the sumy adapter shim (`chunkshop.summarizers.sumy`). |
| `quantize`   | `onnx` for on-the-fly quantization scratch.                          |
| `dev`        | `pytest`, `pytest-asyncio`, `onnx`.                                  |

Python ≥ 3.12 required.

## Prerequisites

- **Postgres ≥ 14** with the `pgvector` extension installed
  (`CREATE EXTENSION vector;` must succeed in your target DB).
- **Disk space for model cache** in `~/.cache/fastembed/` — ~85 MB for int8 `bge-base`,
  ~550 MB for `nomic`.
- **An env var holding your DSN.** The target config references it by name, not by value.

## Quick run

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# Point at the sample corpus in docs/samples/ for a real end-to-end run:
chunkshop ingest --config ../docs/samples/sample.yaml

# Or copy the template and edit it:
cp src/chunkshop/configs/example-files-to-bge.yaml my-cell.yaml
chunkshop ingest --config my-cell.yaml
```

Success looks like:

```json
{
  "cell_name": "example_files",
  "docs_processed": 47,
  "chunks_written": 312,
  "wall_seconds": 18.4,
  "error": null
}
```

## CLI

Two subcommands: `ingest` (one cell) and `orchestrate` (many cells in parallel).

### `chunkshop ingest`

Runs one YAML end-to-end.

```
chunkshop ingest --config PATH [--doc-limit N] [--log PATH] [--omp-threads N]
```

| Flag            | YAML override          | Purpose                                 |
|-----------------|------------------------|-----------------------------------------|
| `-c, --config`  | —                      | Required. Path to YAML.                 |
| `--doc-limit`   | `runtime.doc_limit`    | Smoke-test mode; stop after N docs.     |
| `--log`         | `runtime.log_path`     | Append stdout log lines to this file.   |
| `--omp-threads` | `runtime.omp_num_threads` | Cap BLAS/OMP threads before ORT loads. |

Exit code: `0` on success, `1` if the cell errored. Stdout = a JSON summary.

### `chunkshop orchestrate`

Runs N cells in parallel as subprocesses.

```
chunkshop orchestrate (--config-dir DIR | --config PATH [--config PATH ...])
                      [--concurrency N]
                      [--checkpoints "60,120,300,600"]
                      [--timeout SECONDS]
                      [--smoke | --full]
```

| Flag             | Default         | Purpose                                                             |
|------------------|-----------------|---------------------------------------------------------------------|
| `-d, --config-dir` | —             | Run every `*.yaml`/`*.yml` in the directory.                        |
| `-c, --config`   | —               | Explicit path; repeatable. Mutually exclusive with `--config-dir`.  |
| `--concurrency`  | `4`             | Max parallel cells (subprocess pool size).                          |
| `--checkpoints`  | `60,120,300,600`| Seconds at which to print a status report.                          |
| `--timeout`      | `7200` (2h)     | Overall wall limit; survivors get SIGTERM to their process group.   |
| `--smoke`        | off             | Force `doc_limit=1` + `concurrency=1`. Useful for "does it crash".  |

Stdout = checkpoint reports during the run, JSON summary at the end.

### `chunkshop bakeoff`

Runs a chunker × embedder matrix against a corpus with hand-written gold
queries, scores recall@k + MRR per combo, writes a leaderboard + a
runnable `recommended.yaml`. Config-driven — the matrix lives in YAML,
not on the command line.

```
chunkshop bakeoff --config PATH [--dsn DSN] [--yes] [--keep-schema]
```

| Flag            | Default                 | Purpose                                                       |
|-----------------|-------------------------|---------------------------------------------------------------|
| `--config`      | —                       | Path to the bakeoff YAML. Required.                           |
| `--dsn`         | `$CHUNKSHOP_DSN`        | Postgres DSN. Required (env var or flag).                     |
| `--yes`         | off                     | Bypass the >50-cell matrix confirmation prompt.               |
| `--keep-schema` | off                     | Keep the bakeoff schema after run — useful for debugging.     |

Outputs land in `skill-output/bakeoff/{name}/`:
- `results.json` — raw per-combo + per-query data.
- `report.md` — leaderboard sorted by MRR, per-query detail, statistical-
  power caveat.
- `recommended.yaml` — top combo pre-filled as a runnable
  `chunkshop ingest` cell.

Full walkthrough: [`../docs/tutorial-bakeoff.md`](../docs/tutorial-bakeoff.md).
Recipe card: [`../docs/quickstart-bakeoff.md`](../docs/quickstart-bakeoff.md).

## YAML reference

Every cell config has five sections plus an optional `runtime`. Extra keys are rejected
(`extra="forbid"` in pydantic), so typos fail loudly.

```yaml
cell_name: my_cell
source:   { ... }
chunker:  { ... }
embedder: { ... }
extractor: { ... }   # optional, defaults to {type: none}
target:   { ... }
runtime:  { ... }    # optional, sensible defaults below
```

### `source`

| `type`         | Required fields                        | Optional fields                                                          |
|----------------|----------------------------------------|--------------------------------------------------------------------------|
| `files`        | `glob`                                 | `id_from: path \| stem \| sha1` (default `stem`), `encoding` (`utf-8`)   |
| `json_corpus`  | `path`                                 | `documents_key` (`documents`), `id_field` (`id`), `content_field` (`content`), `title_field` (`title`) |
| `pg_table`     | `dsn_env`, `schema`, `table`, `id_column`, `content_column` | `title_column`, `where`                               |
| `http`         | `urls` or `sitemap`                    | — (stub today)                                                           |
| `s3`           | `bucket`                               | `prefix` (stub today)                                                    |

### `chunker`

Seven chunkers in three families. Pick one per cell.

**Structural** — split on headings, paragraphs, or word counts:

| `type`            | Required                  | Defaults                                     |
|-------------------|---------------------------|----------------------------------------------|
| `sentence_aware`  | —                         | `doc_type: prose` (or `code`), `max_chars: 2000`, `min_chars: 200` |
| `fixed_overlap`   | —                         | `window_words: 300`, `step_words: 150`       |
| `hierarchy`       | —                         | `prefix_heading: true`, `min_section_chars: 100`, `max_chars: 2000` |
| `neighbor_expand` | `base:` (nested chunker)  | `window: 1`                                  |

**Semantic** — splits on embedding-drift boundaries (no heading needed):

| `type`     | Required | Defaults                                                           |
|------------|----------|--------------------------------------------------------------------|
| `semantic` | —        | `boundary_model: "sentence-transformers/all-MiniLM-L6-v2-int8"`, `breakpoint_percentile: 95`, `min_sentences_per_chunk: 3`, `max_chunk_chars: 2000`, `sentence_splitter: "naive"` |

Pass `boundary_model: "same"` to reuse the cell's main embedder (trades
speed for memory). See [`../docs/tutorial-semantic.md`](../docs/tutorial-semantic.md).

**Summary-layer** — wrap any base chunker and change what gets embedded
vs. what gets stored (`summary_embed`) or emit fine+coarse rows linked by
`group_id` (`hierarchical_summary`):

| `type`                   | Required                           | Defaults                             |
|--------------------------|------------------------------------|--------------------------------------|
| `summary_embed`          | `base:`, `summarizer:`             | —                                    |
| `hierarchical_summary`   | `base:`, `summarizer:`, `grouping:` | `grouping: {strategy: fixed_n, n: 5}` |

The `summarizer` config is a discriminated union: `{mode: external, field: ...}`
pulls a pre-computed summary from a source document metadata field; `{mode:
callable, module: "lede.tfidf", function: "summarize", kwargs: {...}}`
imports lazily at first use; `{mode: passthrough}` reuses the raw chunk as
the summary (baseline). See [`../docs/summaries.md`](../docs/summaries.md)
and [`../docs/tutorial-summaries.md`](../docs/tutorial-summaries.md).

Full per-chunker guidance: [`../docs/chunkers.md`](../docs/chunkers.md).

### `embedder`

Only `fastembed` today.

| Field        | Required | Default | Notes                                                    |
|--------------|----------|---------|----------------------------------------------------------|
| `type`       | yes      | —       | Literal `fastembed`.                                      |
| `model_name` | yes      | —       | e.g. `Xenova/bge-base-en-v1.5-int8`. See [embedders.md](../docs/embedders.md). |
| `dim`        | yes      | —       | Must match the model. Mismatch fails loudly at first embed. |
| `batch_size` | no       | `64`    | Per-call batch to `fastembed.embed`.                     |
| `threads`    | no       | `None`  | `None` = auto (bad on shared boxes). Set to 4 typically. |

### `extractor`

| `type`            | Fields                                    |
|-------------------|-------------------------------------------|
| `none`            | — (default)                               |
| `rake_keywords`   | `top_k: 10`, `min_chars: 3` (defaults)    |

RAKE downloads NLTK corpora (`stopwords`, `punkt`) on first use to `~/nltk_data/`.

### `target`

| Field              | Required             | Default                  | Notes                                                                                                                                             |
|--------------------|----------------------|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| `dsn_env`          | no                   | `AGE_BAKEOFF_PGRG_DSN`   | Name of the env var holding your DSN. **Override this** to `CHUNKSHOP_DSN` in your configs.                                                       |
| `schema`           | yes                  | —                        | Lowercase ident; must match `^[a-z_][a-z0-9_]*$`. Created if missing.                                                                             |
| `table`            | yes                  | —                        | Same ident rule.                                                                                                                                  |
| `mode`             | no                   | `overwrite`              | One of `overwrite`, `append`, `create_if_missing`. See [`../docs/tutorial-multi-source.md`](../docs/tutorial-multi-source.md).                     |
| `source_tag`       | when `mode=append`   | `null`                   | Ident-safe tag written to every row's `source` column. Required for `append`; optional (but recommended) for `overwrite`/`create_if_missing`.     |
| `promote_metadata` | no                   | `[]`                     | List of `{path, type}` pairs lifting jsonb metadata paths into typed columns. `path` is lowercased + `.` → `__` for the column name.              |
| `force_overwrite`  | no                   | `false`                  | Bypasses the "refuse to drop a table that holds rows from a foreign `source_tag`" safety check in `overwrite` mode.                               |
| `overwrite`        | no (soft-deprecated) | `false`                  | Legacy boolean. Still honored when `mode=overwrite` (acts as the DROP+CREATE switch). Prefer the new `mode` field for new configs.                |
| `hnsw`             | no                   | `true`                   | `false` for tiny test tables where HNSW is slower than seq scan.                                                                                  |

### Multi-source ingest

Multiple cells can write to the same table by tagging each cell's rows with a `source_tag`.
Cell A creates the table with `mode: create_if_missing`, Cell B appends with `mode: append`
and its own tag. Queries filter or group by the `source` column. See
[`../docs/tutorial-multi-source.md`](../docs/tutorial-multi-source.md) for the end-to-end walkthrough.

```yaml
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: all_docs
  mode: append
  source_tag: support_tickets
```

### `runtime`

| Field               | Default | Notes                                                            |
|---------------------|---------|------------------------------------------------------------------|
| `omp_num_threads`   | `1`     | Sets `OMP/MKL/OPENBLAS/NUMEXPR` env vars before ORT loads.       |
| `doc_limit`         | `null`  | Stop after N docs. Smoke-test lever.                             |
| `log_path`          | `null`  | Mirror stdout heartbeats to this file. Parent dirs auto-created. |
| `heartbeat_every`   | `25`    | Log a progress line every N docs.                                |

## Environment variables

| Var                                    | When chunkshop reads it                                    |
|----------------------------------------|------------------------------------------------------------|
| `$<target.dsn_env>` (default `AGE_BAKEOFF_PGRG_DSN`) | At sink construction; must be a valid libpq DSN. |
| `OMP_NUM_THREADS` and friends          | Set by `runner` before any numpy/ORT import.               |
| `HF_HOME` / `HF_HUB_CACHE`             | Respected by fastembed's downloader if you've moved the cache. |

## Troubleshooting

### "no files matched glob: /path/**/*.md"

Your `source.glob` didn't match anything. Test it in a shell first:

```bash
ls /path/**/*.md | head
```

Note that chunkshop uses Python's `glob.glob(..., recursive=True)` — `**` only matches across
directories when it's its own path component (`/foo/**/*.md`, not `/foo/**.md`).

### "relation already exists" on second run

`target.overwrite` is `false` by default. Either flip it to `true` (drops + recreates) or
drop the table yourself. The `ON CONFLICT DO UPDATE` in the writer will also happily upsert
into an existing table.

### "model X produced dim Y, config says dim=Z"

Your YAML's `embedder.dim` doesn't match the model's output. Look up the right dim in
[`../docs/embedders.md`](../docs/embedders.md) — `bge-small`=384, `bge-base`=768,
`nomic`=768.

### "CREATE EXTENSION IF NOT EXISTS vector" fails with permission denied

Your DB role can't create extensions. Ask a superuser to run it once per database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Then re-run chunkshop — the sink's `CREATE EXTENSION IF NOT EXISTS` will be a no-op.

### "table/schema must match ^[a-z_][a-z0-9_]*$"

chunkshop refuses to interpolate mixed-case or quoted identifiers — SQL injection safety via
allowlist. Lowercase your `schema` and `table`.

### Ingest is slow and my CPU fans are loud

Three knobs. Pick one:

- Drop `embedder.batch_size` from 64 to 32 — less memory pressure, slower per-doc.
- Set `embedder.threads: 4` (or 2) — caps ORT's worker pool.
- If running under `orchestrate`, reduce `--concurrency`.

See the thread-tuning table in [`../docs/embedders.md`](../docs/embedders.md).

### First run hangs on "downloading model"

Fastembed is pulling the ONNX from HuggingFace. Network / HF outage. Check
`curl -sI https://huggingface.co/` and your proxy settings. The file lands in
`~/.cache/fastembed/<model-name>/`.

### nltk errors on first `rake_keywords` run

The extractor downloads `stopwords`, `punkt`, `punkt_tab` into `~/nltk_data/` on first use.
Behind a strict firewall? Pre-download once:

```python
import nltk
for r in ("stopwords", "punkt", "punkt_tab"):
    nltk.download(r)
```

## Using chunkshop as a library

```python
from chunkshop import load_config
from chunkshop.runner import run_cell

cfg = load_config("my-cell.yaml")
result = run_cell(cfg)
print(result.docs_processed, result.chunks_written, result.wall_seconds)
```

Or skip the YAML and build a `CellConfig` directly — every section is a plain pydantic
model.

## Tests

```bash
cd python
uv run pytest
```

Most tests are offline. `test_embedder_fastembed.py` and `test_int8_registry.py` download the
int8 `bge-base` model on first run and cache it — budget ~85 MB + a few seconds the first
time.
