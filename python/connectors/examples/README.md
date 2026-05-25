# chunkshop-connectors — runnable end-to-end demos

Six scripts that exercise the user-facing expectations the verified
connectors promise. Each one is self-contained and prints a clear
trace of what it did.

| # | Script | What it verifies | Prerequisites |
|---|--------|------------------|---------------|
| 1 | [`e2e_github_with_code_chunker.py`](e2e_github_with_code_chunker.py) | GH repo → files → `code_aware` chunker; cursor refresh emits 0 changes on re-run | Live internet; optional `GITHUB_TOKEN` for 5k req/hr |
| 2 | [`e2e_gdrive_mocked.py`](e2e_gdrive_mocked.py) | Drive folder → docs → `sentence_aware`; mocked cursor refresh | None (offline) |
| 3 | [`e2e_s3_mocked.py`](e2e_s3_mocked.py) | S3 bucket → objects → `sentence_aware`; ETag cursor skip | None (offline) |
| 4 | [`e2e_url_crawl.py`](e2e_url_crawl.py) | URL crawl at depth N → pages → `sentence_aware`; conditional GET cursor | Live internet (default seed: `https://example.com`) |
| 5 | [`e2e_database.py`](e2e_database.py) | `PgTableSource` regression smoke (real Postgres); cursor narrows to the new row | Postgres on `localhost:5434` (the test stack) |
| 6 | [`e2e_pipeline_full.py`](e2e_pipeline_full.py) | Full Source → Chunker → Embedder → Sink against pgvector | Postgres on `:5434`; downloads `bge-small-en-v1.5-int8` (~30 MB) on first run |

## Quick start

```bash
# from python/connectors/
uv sync --extra dev

# Offline demos — no setup required
uv run --no-sync python examples/e2e_gdrive_mocked.py --reset
uv run --no-sync python examples/e2e_s3_mocked.py --reset

# Live demos — needs network
uv run --no-sync python examples/e2e_github_with_code_chunker.py --reset
uv run --no-sync python examples/e2e_url_crawl.py --reset

# DB demos — needs Postgres on :5434
docker compose -f ../../docker-compose.test.yaml up -d
uv run --no-sync python examples/e2e_database.py
uv run --no-sync python examples/e2e_pipeline_full.py
```

## Cursor files

Each demo persists its cursor under `/tmp/` so re-runs exercise the
incremental path:

- `/tmp/chunkshop-demo-github-cursor.json`
- `/tmp/chunkshop-demo-gdrive-cursor.json`
- `/tmp/chunkshop-demo-s3-cursor.json`
- `/tmp/chunkshop-demo-url-cursor.json`

Pass `--reset` to wipe the cursor and run a full ingest.

## Mapping to user expectations

These demos are the executable proof of the five expectations
stated in this session's mission:

1. **"Sync from Google Drive (folder or docs). If rerun and pass
   refresh, it only grabs and processes changes since the last
   run."** → `e2e_gdrive_mocked.py` (incremental via the Drive
   v3 changes API; cursor is a `page_token`).
2. **"Provide a GH repo, and have it ingested. I expect the chunker
   to be code aware… AST tree, and more."** → `e2e_github_with_code_chunker.py`
   wired through `chunkshop.chunkers.code_aware.CodeAwareChunker`
   (Python AST nodes, one chunk per def/class).
3. **"Point to an S3 bucket… same scan-for-changes logic."** →
   `e2e_s3_mocked.py` (cursor is the full `{key: etag}` map; only
   keys with a mutated ETag re-emit).
4. **"Point to a URL, pass a depth… easy incremental."** →
   `e2e_url_crawl.py` (conditional GET via `If-None-Match`;
   `If-Modified-Since` fallback when the server omits ETag).
5. **"Database connections continue to function."** →
   `e2e_database.py` (PgTableSource tuple cursor `(after_ts,
   after_id)` correctly catches boundary-row inserts).

Plus a sixth bonus end-to-end: `e2e_pipeline_full.py` wires a full
pipeline (files → sentence_aware → fastembed → pgvector) using the
production `chunkshop.runner.run_cell` API — proof that connectors
land in real pgvector tables, not just in test asserts.

## Hermetic vs. live

The pytest suite under [`../tests/test_e2e_user_expectations.py`](../tests/test_e2e_user_expectations.py)
covers the same five expectations with hermetic mocks (the
loopback-only socket guard in `conftest.py` forbids non-loopback
egress, so the tests can't drift into the live API by accident).
The demos here complement the tests:

- **pytest** — hermetic, fast, asserts behaviour for CI.
- **demos** — live or seeded, slow-but-tangible, for humans who
  want to see ingest happen.

Both are needed. A passing test suite that doesn't run against the
real API is necessary but not sufficient evidence.
