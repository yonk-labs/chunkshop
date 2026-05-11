# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

chunkshop is a standalone ingest-to-pgvector tool. One YAML config = one "cell" = one end-to-end ingest: read from a source → chunk → embed → optionally tag → write to a pgvector table. It's meant to be embeddable as a library or driven from the CLI. Python (`python/`) is the reference implementation at v0.2.0 alpha; Rust (`rust/`) and Go (`go/`) are planned ports that will share the same YAML schema and target-table layout — vectors from any implementation are interchangeable.

## Commands

All commands below assume you're in `python/` unless noted. `uv` is the default tool; substitute `pip install -e .` if needed.

```bash
cd python

# Install — always include [extractors] so the RAKE test doesn't fail
uv sync --extra dev --extra extractors --extra all-backends

# Full test suite (some tests skip if Postgres unreachable)
uv run pytest -q

# Single test file
uv run pytest tests/chunkshop/test_sink_append_mode.py -v

# Single test
uv run pytest tests/chunkshop/test_sink_append_mode.py::test_append_fails_on_dim_mismatch -v

# CLI (installed as `chunkshop` by the uv sync above)
uv run chunkshop ingest --config ../docs/samples/sample.yaml
uv run chunkshop orchestrate --config-dir src/chunkshop/configs/factorial-int8/ --concurrency 4
```

### Databases for integration tests

Tests that talk to a database connect via env vars and **skip** if unreachable:

- `$CHUNKSHOP_TEST_DSN` — Postgres (default `postgresql://postgres:postgres@localhost:5434/chunkshop_test` if `docker-compose.test.yaml` is up)
- `$CHUNKSHOP_TEST_DSN_MARIADB` — MariaDB 11.7+ (default `mysql://root:rootpw@localhost:3307/chunkshop_test` if `docker-compose.test.yaml` is up)

Spin both up:

    docker compose -f docker-compose.test.yaml up -d

Cross-backend tests (`test_cross_backend.py`) require both DSNs set. SQLite tests use `:memory:` or `tmp_path` and need no infrastructure. Postgres integration tests drop their own schema in teardown (`chunkshop_test_*`, `chunkshop_e2e_samples`, `chunkshop_test_append`, `chunkshop_test_multi`); MariaDB tests drop their own database (`chunkshop_xb_*`, etc.).

## Architecture

The pipeline is `Source → Chunker → Embedder → Extractor → Sink`. Every provider type is a `Protocol` in `python/src/chunkshop/<stage>/base.py`, implemented structurally (no inheritance). A `load_<stage>(cfg)` factory in each package's `__init__.py` dispatches on the pydantic discriminator in `config.py`. **Adding a new source / chunker / embedder / extractor = one new file + one new branch in the loader + one new pydantic model in the union.**

`runner.run_cell(cfg)` wires all five stages together for a single YAML. `orchestrator.py` spawns `N` cells as subprocesses (`python -m chunkshop.cli ingest --config X`); subprocess isolation is deliberate — ONNX Runtime has process-global state that doesn't share cleanly across threads, and one cell crashing must not kill siblings.

### Load-bearing details that will bite you

**`Chunk` has two text fields, not one.** `original_content` is the raw chunk body (for grep / fact-match / audit). `embedded_content` is what gets embedded and may differ — e.g., `hierarchy` prepends the section heading, `neighbor_expand` splices in adjacent chunks, `summary_embed` (planned) replaces it with a summary. The sink writes both.

**Extractor contract returns `ExtractResult(tags, metadata)`.** The runner merges `r.metadata` into each chunk's metadata with **chunker-wins** semantics — chunker-emitted keys (`strategy`, `heading`, `start_word`) survive key collisions, so extractors should namespace (use `entities`, `language`, not bare `strategy`). See `result.py`'s class docstring.

**Schema-flex (`target.mode`) changes sink behavior materially.**
- `overwrite` (default) — DROP + CREATE. But: refuses to drop a table that contains rows with a different `source_tag` unless `target.force_overwrite: true`.
- `append` — requires `source_tag`. Pre-flight (in `_append_preflight`) checks table exists, embedding dim matches, `source` + `promote_metadata` columns added with `ADD COLUMN IF NOT EXISTS`. Refuses non-chunkshop tables (no `embedding` vector column).
- `create_if_missing` — first cell into a table. No pre-flight validation of existing schema.

**`source` column is write-once on `ON CONFLICT`.** The `write_document` UPDATE clause deliberately excludes `source`. Two cells colliding on `(doc_id, seq_num)` → the first writer's source_tag wins forever. This is provenance, not a race.

**`PromoteColumn.column_name` is the single source of truth for jsonb-path → Postgres-ident.** Always use `pc.column_name` (replace `.` with `__`, lowercase), never re-derive. Both `_ensure_promote_columns` and `write_document` rely on this producing the same output.

**`PgVectorSink.write_document` opens a short-lived connection and commits per-document.** Not a pool. This makes `SELECT COUNT(DISTINCT doc_id) FROM ...` a valid live-progress query from another psql session, and a mid-run crash only loses the in-flight doc (primary key is `{doc_id}::{seq_num}`, so rerun upserts).

**Thread discipline matters at runtime.** `runner.run_cell` sets `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `OPENBLAS_NUM_THREADS` / `NUMEXPR_NUM_THREADS` from `runtime.omp_num_threads` **before** any numpy/ONNX import. `embedder.threads` caps ORT's `intra_op_num_threads` separately. Rule: `orchestrate --concurrency N × embedder.threads ≈ physical cores`.

**int8 models aren't in fastembed's default registry.** `embedders/_registry.py` registers Xenova's pre-quantized BGE variants at import time (idempotent). If you add a new int8 model, add it to `_INT8_VARIANTS` there, not to a new file.

**Config is pydantic with `extra="forbid"`.** A typo in YAML produces a validation error, not silent ignore. Every pydantic model inherits `_Base` which sets this. Discriminated unions (`SourceConfig`, `ChunkerConfig`, `EmbedderConfig`, `ExtractorConfig`) dispatch on a `type` field literal — a missing/wrong `type` raises at config-load, not at runtime.

**Identifier safety.** `table`, `schema`, `source_tag`, and `PromoteColumn.path` each pass through a regex validator (`^[a-z_][a-z0-9_]*$` for idents; `^[A-Za-z_][A-Za-z0-9_]*$` per segment for jsonb paths, dot-separated). This is SQL-injection-prevention by allowlist — don't widen the regex without reading `PromoteColumn._safe_path` comments.

### The four chunkers (+ a benchmark verdict)

`sentence_aware`, `fixed_overlap`, `hierarchy`, `neighbor_expand`. chunkshop's own factorial bakeoff (772-doc legal QA corpus, 30 gold questions) found `hierarchy` wins every embedder column by prepending the section heading to `embedded_content` as free framing context. `hierarchy + int8 bge-small` is the shipped default in `example-files-to-bge.yaml`. Full discussion in `docs/chunkers.md`.

## Repo conventions

- **`skill-output/` is gitignored.** Mission briefs live there, not in git. Active plans live in `docs/superpowers/plans/`; completed plans are moved to `archive/docs/superpowers/plans/` once their feature ships.
- **Sample corpus glob is `docs/samples/*-*.md`, not `*.md`** — the latter silently picks up `docs/samples/README.md` and pollutes the corpus. Stick with `*-*.md` for the four dash-named fixtures (`handbook-*`, `release-notes`).
- **Tests that use the sample corpus** use the absolute path from `test_end_to_end_samples_corpus.py::SAMPLES_GLOB` — don't hardcode relative paths from test files.
- **All four sample YAMLs** (`sample.yaml`, `sample-sentence-aware.yaml`, `sample-neighbor-expand.yaml`, `sample-multi-source.yaml`) now use the schema-flex `mode:` shape. The legacy `overwrite: true` field is still accepted by the pydantic model (internal `factorial*/` configs and a few test fixtures still use it) but user-facing docs and samples are all on `mode: overwrite`.
- **Worktree pattern for feature work:** `git worktree add ../chunkshop-<feature> -b feat/<feature>` from main. Each feature gets its own worktree; merge back via `superpowers:finishing-a-development-branch` when tests pass.

## Active work

No in-flight implementation plans. The seven plans for v0.2.0 features (metadata extractors, semantic chunker, summary-embed, DocFramer, schema-flexibility, chunker `max_chars` hotfix, bakeoff CLI) all shipped and were moved to `archive/docs/superpowers/plans/` for historical reference. Mission briefs in `skill-output/mission-brief/` (gitignored) sit alongside the now-archived plans.

When new work starts, run `/mission-brief` then `superpowers:writing-plans` to produce the next plan into `docs/superpowers/plans/`. Check `git worktree list` for any in-flight feature branches before assuming the working state is `main`.

## Sibling repos this one interacts with

- `../extractive_summary/` (lede) — zero-dep extractive summarizer. Brief 4 (`summary_embed` chunker wrapper) wires it via `chunkshop.summarizers.lede` as a callable summarizer.
- `../lede-neural/` — seed-state neural companion to lede. Brief 4's callable path supports it once it ships.
- `../yonk-doctools/` — seed-state sibling repo for PDF/DOCX → markdown preprocessing with VLM image captioning. Feeds chunkshop's `files` source.

Don't import from these in chunkshop's core code. They're user-wired via config (`module: chunkshop.summarizers.lede`, `glob: /path/to/yonk-doctools/output/**/*.md`).
