# chunkshop (Python)

Reference implementation of the chunkshop ingest tool. For the current
version and release history see [`CHANGELOG.md`](../CHANGELOG.md).

**New here?** Start with the [**end-to-end tutorial**](../docs/tutorial.md) — a guided
walkthrough from zero (no Postgres) to a running semantic query.

This file is the field-by-field reference: every CLI flag, every YAML field, the
troubleshooting table. Use it alongside the tutorial once you know what you're doing.

For the high-level shape and mermaid diagram, see the [top-level README](../README.md).

## Install

From source when you want sample corpora, dev tooling, or unreleased branch
work:

```bash
git clone https://github.com/yonk-labs/chunkshop && cd chunkshop/python
uv sync --extra dev --extra all-backends
```

Published package:

```bash
pip install 'chunkshop[all-backends]'
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
| `lede`      | `lede>=0.5.0` for `summary_embed`, query hints, and `lede_report` document metadata. |
| `lede-spacy`| `lede-spacy>=0.5.0` for optional spaCy-backed lede hint expansion. |
| `sumy`       | `sumy` + NLTK corpora for the sumy adapter shim (`chunkshop.summarizers.sumy`). |
| `sqlite` / `mariadb` / `clickhouse` | Per-backend drivers. `all-backends` pulls all three (Postgres needs no extra). |
| `s3`         | `boto3` for the `s3` source and the S3 RawStore.                     |
| `pdf` / `docx` / `pptx` / `xlsx` / `html` | File parsers for the `files` source. `office` = pdf+docx+pptx+xlsx; `all-parsers` = office+html. |
| `code`       | tree-sitter grammars (10 languages) for `symbol_aware` + `code_relationships`. A regex fallback runs when absent. |
| `quantize`   | `onnx` for on-the-fly quantization scratch.                          |
| `dev`        | `pytest`, `pytest-asyncio`, `onnx`.                                  |

Python ≥ 3.12 required.

## Prerequisites

- **One supported target backend:** Postgres ≥ 14 + pgvector, MariaDB 11.7+,
  SQLite + sqlite-vec, or ClickHouse 24.10+.
- For Postgres, `CREATE EXTENSION vector;` must succeed in your target DB.
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

Ten subcommands. The three core ones (`ingest`, `orchestrate`, `bakeoff`)
are documented in detail below; the rest have their own reference cards:

| Subcommand | Purpose | Reference |
|---|---|---|
| `init` / `validate` / `prefetch` | Scaffold a cell YAML / check it without touching a DB / pre-download the embedder model. | [`cli-admin.md`](../docs/reference/cli-admin.md) |
| `ingest` | Run one cell end-to-end. | below |
| `orchestrate` | Run N cells as parallel subprocesses. | below |
| `bakeoff` | Chunker × embedder × backend matrix → leaderboard + `recommended.yaml`. | below |
| `eval validate` / `eval plan` | Validate + expand an eval-profile matrix. | `chunkshop eval --help` |
| `search` | Semantic / keyword / hybrid search over an ingested table (`--by-symbol` for code corpora). | [`cli-search.md`](../docs/reference/cli-search.md) |
| `impact-of` | Callers/callees blast radius for a symbol via `code_relationships` edges. | [`cli-impact-of.md`](../docs/reference/cli-impact-of.md) |
| `fact-search` | Query consolidated facts written by the `consolidation` chunker. | [`cli-fact-search.md`](../docs/reference/cli-fact-search.md) |

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
| `files`        | `glob`                                 | `id_from: path \| stem \| sha1` (default `stem`), `encoding` (`utf-8`), `incremental` (cursor-based sync — see [`../docs/incremental.md`](../docs/incremental.md)) |
| `json_corpus`  | `path`                                 | `documents_key` (`documents`), `id_field` (`id`), `content_field` (`content`), `title_field` (`title`) |
| `pg_table`     | `dsn_env`, `database`, `table`, `id_column`, `content_column` | `title_column`, `where`, `metadata_columns`, `updated_at_column` (enables incremental tuple cursor) |
| `sqlite_table` / `mariadb_table` / `clickhouse_table` | `dsn_env`, `database`, `table`, `id_column`, `content_column` | `title_column`, `where`, `metadata_columns` |
| `http`         | `urls` or `sitemap`                    | `crawl_depth` (0–5), `allow_external`, `request_delay_seconds`, `respect_robots`, `max_pages`, `user_agent` — see [`source-http.md`](../docs/reference/source-http.md) |
| `s3`           | `bucket`                               | `prefix`, `endpoint_url` (minio/R2). Needs the `[s3]` extra. See [`source-s3-core.md`](../docs/reference/source-s3-core.md) |
| `inline`       | —                                      | Library mode: the host app calls `Pipeline.ingest_text()` per document. |
| `connector`    | `connector`, `config`                  | `sync`, `raw_store`. Plugin sources from the chunkshop-connectors package (gdrive, github, blob, rss, …) — see [`../docs/reference/`](../docs/reference/README.md) |
| `comment_extracts` | `glob`                             | `languages`, `min_chars` (`20`), `granularity: block \| per_line \| per_file`, `include_docstrings`, `skip_pragmas` |
| `session_staging` | `dsn_env`, `staging_table`, `mode: realtime \| consolidate` | `staging_schema` (`public`), `min_age_seconds` (`3600`), `max_sessions` |

### `chunker`

Ten chunkers in four families. Pick one per cell.

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
| `consolidation`          | `base:`, `consolidator:`           | `fact_max_chars: 1200`               |

`consolidation` is the agent-memory chunker: it collapses a session episode
into a bounded summary plus length-capped facts (queryable via
`chunkshop fact-search`). See
[`../docs/reference/consolidator-fact-extractors.md`](../docs/reference/consolidator-fact-extractors.md).

The `summarizer` config is a discriminated union: `{mode: external, field: ...}`
pulls a pre-computed summary from a source document metadata field; `{mode:
callable, module: "lede.tfidf", function: "summarize", kwargs: {...}}`
imports lazily at first use; `{mode: passthrough}` reuses the raw chunk as
the summary (baseline). See [`../docs/summaries.md`](../docs/summaries.md)
and [`../docs/tutorial-summaries.md`](../docs/tutorial-summaries.md).

**Code-aware** — split source code at function/class/symbol boundaries:

| `type`         | Required | Defaults                                                                  |
|----------------|----------|---------------------------------------------------------------------------|
| `code_aware`   | —        | `language: auto` (Python via stdlib `ast`), `max_chars: 4000`, `min_chars: 100`, `include_imports: true` |
| `symbol_aware` | —        | `granularity: function \| class \| module` (`function`), `max_chars: 8000`, `include_imports: true`, `max_symbols_per_file: 2000` |

`symbol_aware` covers 10 languages (Python, Java, Go, TypeScript,
JavaScript, Rust, C, C++, C#, Ruby) via tree-sitter with the `[code]`
extra, and stamps `fqn` / `scope_chain` / `node_id` per chunk — the
metadata behind `chunkshop search --by-symbol` and `chunkshop impact-of`.
See [`../docs/reference/chunker-symbol-aware.md`](../docs/reference/chunker-symbol-aware.md)
and [`../docs/reference/chunker-code-aware.md`](../docs/reference/chunker-code-aware.md).

Full per-chunker guidance: [`../docs/chunkers.md`](../docs/chunkers.md).

### `embedder`

Two types: `fastembed` (local ONNX, the default choice) and `openai`
(any OpenAI-compatible remote `/v1/embeddings` endpoint).

**`fastembed`:**

| Field        | Required | Default | Notes                                                    |
|--------------|----------|---------|----------------------------------------------------------|
| `type`       | yes      | —       | Literal `fastembed`.                                      |
| `model_name` | yes      | —       | e.g. `Xenova/bge-base-en-v1.5-int8`. See [embedders.md](../docs/embedders.md). |
| `dim`        | yes      | —       | Must match the model. Mismatch fails loudly at first embed. |
| `batch_size` | no       | `64`    | Per-call batch to `fastembed.embed`.                     |
| `threads`    | no       | `None`  | `None` = auto (bad on shared boxes). Set to 4 typically. |

**`openai`** — works with OpenAI, Voyage, Mistral, or a local
OpenAI-compatible server. Full reference:
[`embedder-openai.md`](../docs/reference/embedder-openai.md).

| Field         | Required | Default                       | Notes                                          |
|---------------|----------|-------------------------------|------------------------------------------------|
| `type`        | yes      | —                             | Literal `openai`.                              |
| `model`       | yes      | —                             | e.g. `text-embedding-3-small`.                 |
| `dim`         | yes      | —                             | Must match the endpoint's output.              |
| `base_url`    | no       | `https://api.openai.com/v1`   | Point at any compatible `/v1/embeddings` host. |
| `api_key_env` | no       | `None`                        | Name of the env var holding the API key.       |
| `batch_size` / `timeout` / `max_retries` | no | `64` / `60.0` / `3` | Request shaping.                |

### `extractor`

Eleven types. Field-level detail per extractor:
[`../docs/extractors.md`](../docs/extractors.md).

| `type`               | Extra needed | What it does                                                    |
|----------------------|--------------|-----------------------------------------------------------------|
| `none`               | —            | Default. No tags, no metadata.                                  |
| `rake_keywords`      | `extractors` | RAKE keyword tags (`top_k: 10`, `min_chars: 3` defaults).       |
| `keybert_phrases`    | `keybert`    | KeyBERT keyphrase tags.                                         |
| `spacy_entities`     | `spacy`      | spaCy NER entities into metadata.                               |
| `lang_detect`        | `lang`       | Per-chunk language code into metadata.                          |
| `cooccurrence`       | —            | Term co-occurrence pairs into metadata.                         |
| `lede_top_terms`     | `lede`       | lede scored top terms.                                          |
| `lede_report`        | `lede`       | lede document-level report (summary, key facts) into metadata.  |
| `code_summary`       | `code`       | Per-symbol code summaries ([reference](../docs/reference/extractor-code-summary.md)). |
| `code_relationships` | `code`       | Cross-file call/import edges — feeds `chunkshop impact-of` ([reference](../docs/reference/extractor-code-relationships.md)). |
| `composite`          | per child    | Run several extractors in sequence.                             |

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

#### `target.documents` (Python/Postgres only)

For Postgres targets in the Python implementation, `target.documents.enabled:
true` writes a companion one-row-per-document table next to the chunk table.
It is off by default for compatibility.

```yaml
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: mydata
  table: chunks
  documents:
    enabled: true
    table: documents
    store_full_content: true
    store_lede_report: true
```

Current support boundary:

- Python + Postgres: supported.
- Python + SQLite/MariaDB/ClickHouse: rejected at config load.
- Rust: rejected at config load when `documents.enabled: true`; Rust does not
  write the companion document table yet.

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
