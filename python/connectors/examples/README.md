# chunkshop-connectors — runnable end-to-end demos

Eight self-contained scripts that exercise the user-facing
expectations the verified connectors promise. Each demo is
self-bootstrapping: `python e2e_*.py` works from anywhere in the
repo (each script prepends `python/src` + `python/connectors/src`
to `sys.path` on startup).

| # | Script | What it verifies | Prerequisites | Live or hermetic? |
|---|--------|------------------|---------------|-------------------|
| 1 | [`e2e_github_with_code_chunker.py`](e2e_github_with_code_chunker.py) | GH repo → files → `code_aware` chunker; cursor refresh emits 0 changes on re-run | Live internet; optional `GITHUB_TOKEN` for 5k req/hr | Live |
| 2 | [`e2e_gdrive_mocked.py`](e2e_gdrive_mocked.py) | Drive folder → docs → `sentence_aware`; mocked cursor refresh | None (offline) | Hermetic |
| 3 | [`e2e_gdrive_real_flow.py`](e2e_gdrive_real_flow.py) | **Real Google OAuth** loopback flow + Drive doc / folder ingest + summary + Source → Chunker → Embedder → pgvector full pipeline. CLI exposes `--chunker {sentence_aware,fixed_overlap,hierarchy,neighbor_expand,code_aware}` and `--extractor {none,rake_keywords,lang_detect,lede_top_terms,composite_keywords_lang}`. | Google Cloud OAuth client (Desktop type) with redirect `http://localhost:8765/callback`; `GDRIVE_CLIENT_ID` + `GDRIVE_CLIENT_SECRET`; Postgres on `:5434` | Live |
| 4 | [`e2e_s3_mocked.py`](e2e_s3_mocked.py) | S3 bucket → objects → `sentence_aware`; ETag cursor skip on unchanged keys | None (offline) | Hermetic |
| 5 | [`e2e_url_crawl.py`](e2e_url_crawl.py) | URL crawl at depth N → pages → `sentence_aware`; conditional GET cursor | Live internet (default seed: `https://example.com`) | Live |
| 6 | [`e2e_database.py`](e2e_database.py) | `PgTableSource` regression smoke (real Postgres); tuple cursor narrows to the new row | Postgres on `localhost:5434` (the test stack) | Live (loopback only) |
| 7 | [`e2e_pipeline_full.py`](e2e_pipeline_full.py) | Full Source → Chunker → Embedder → Sink against pgvector | Postgres on `:5434`; downloads `bge-small-en-v1.5-int8` (~30 MB) on first run | Live (loopback only) |
| 8 | [`e2e_real_world_5kbs.py`](e2e_real_world_5kbs.py) | **The big one.** 3 GH repos (ragflow / lede / chunkshop) → per-repo KBs + cross-cut MD KB + 5 PDFs + 4 LLM-MD + ClickHouse → 5 KBs with hybrid search across all of them. 15 hybrid queries; wall time ~14 min. | Postgres on `:5434`, ClickHouse, live internet, multiple model downloads | Live |

## Quick start

```bash
# from python/connectors/
uv sync --extra dev

# Hermetic demos — no setup
uv run --no-sync python examples/e2e_gdrive_mocked.py --reset
uv run --no-sync python examples/e2e_s3_mocked.py --reset

# Live demos — needs internet
uv run --no-sync python examples/e2e_github_with_code_chunker.py --reset
uv run --no-sync python examples/e2e_url_crawl.py --reset

# DB demos — needs Postgres on :5434
docker compose -f ../../docker-compose.test.yaml up -d
uv run --no-sync python examples/e2e_database.py
uv run --no-sync python examples/e2e_pipeline_full.py

# Real Google OAuth (Drive)
export GDRIVE_CLIENT_ID=...apps.googleusercontent.com
export GDRIVE_CLIENT_SECRET=GOCSPX-...
uv run --no-sync python examples/e2e_gdrive_real_flow.py \
    'https://drive.google.com/drive/folders/<ID>' \
    --chunker hierarchy --extractor composite_keywords_lang

# The big one — many minutes, many bytes
uv run --no-sync python examples/e2e_real_world_5kbs.py
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
stated in the SP-1 / SP-2 mission:

1. **"Sync from Google Drive (folder or docs). If rerun and pass
   refresh, it only grabs and processes changes since the last
   run."** → `e2e_gdrive_mocked.py` (Drive v3 changes API,
   `page_token` cursor) + `e2e_gdrive_real_flow.py` (real OAuth).
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

Plus three additional end-to-end demos:

6. **Full pipeline against real pgvector** → `e2e_pipeline_full.py`
   (files → sentence_aware → fastembed → pgvector through
   `chunkshop.runner.run_cell`).
7. **Real OAuth + chunker / extractor matrix** →
   `e2e_gdrive_real_flow.py` (full Source → Chunker → Embedder →
   Sink with every chunker option and a composite extractor).
8. **5-KB cross-domain ingest with hybrid search** →
   `e2e_real_world_5kbs.py` — 3 GH repos + cross-cut MD + 5 PDFs +
   4 LLM-MD + ClickHouse, all hybrid-searchable.

## Hermetic vs live

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

## When demos fail

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: HTML→text conversion requires beautifulsoup4` | `[html]` extra not installed | `pip install chunkshop[html]` |
| `chunkshop_connectors._stub.StubError` | Tried to use an experimental-tier connector | Switch to `blob`/`rss`/`github`/`gdrive` |
| Drive 403 with "API not enabled" | Drive API not activated in the GCP project | Hit the Console URL in the error message |
| `psycopg.OperationalError: connection refused localhost:5434` | Test compose not running | `docker compose -f docker-compose.test.yaml up -d` |
| Demo crawl emits 0 docs | Server returns 304 Not Modified (cursor present) | Pass `--reset` to clear the cursor |
| GitHub 403 rate-limited | Anonymous quota exhausted | Set `GITHUB_TOKEN` env var |

## See also

- [`docs/connectors/README.md`](../../docs/connectors/README.md) — the connector tier model
- [`docs/connectors/_status.md`](../../docs/connectors/_status.md) — per-connector status table
- [`docs/connectors/gdrive.md`](../../docs/connectors/gdrive.md), [`docs/connectors/github.md`](../../docs/connectors/github.md) — auth setup guides
- [`docs/reference/`](../../docs/reference/) — per-surface reference docs
- [`docs/AGENT_REFERENCE.md`](../../docs/AGENT_REFERENCE.md) — single-doc digest for LLM agents
- [`docs/tutorial-code-repo-ingest.md`](../../docs/tutorial-code-repo-ingest.md) — zero-to-hero walkthrough
