# Tutorial: zero-to-hero ingesting a code repo

Goal: take a real GitHub repo, ingest its source into chunkshop, and
end up with three working queries:

1. **Semantic search** — "find chunks that talk about iteration cursors"
2. **Symbol-filtered search** — "show me chunks for the `HttpSource*` class family"
3. **Impact graph** — "who calls `HttpSource.iter_changes_since`?"

We'll use this very repo (chunkshop itself) as the worked example. By
the end you'll have a Postgres database with ~700 chunks, fully
indexed, with a `code_edges` table for the call/inherits graph, all
hybrid-searchable from the CLI.

Estimated time: 15-20 minutes (most of it waiting for `pip install` and
the embedder model download).

---

## Step 1 — Install

```bash
# Create a fresh venv
python3.12 -m venv /tmp/chunkshop-tutorial
source /tmp/chunkshop-tutorial/bin/activate

# Install chunkshop with the extras this tutorial needs
pip install \
    "chunkshop[code,lede]" \
    "chunkshop-connectors[github]"
```

The extras:

- `[code]` — `tree-sitter` plus real grammars for all ten supported
  languages (Python, Java, Go, TypeScript, JavaScript, Rust, C, C++,
  C#, and Ruby), so the `symbol_aware` chunker uses real tree-sitter
  instead of the regex fallback.
- `[lede]` — `lede` package, used by `code_summary` (default backend)
  and by `chunkshop search --return summary+chunks`.

Verify:

```bash
chunkshop --help
# Should show: ingest, orchestrate, search, impact-of, ...

python -c "from chunkshop.sources import registry; print(registry.available_connectors())"
# Should include 'github' among 25+ entries
```

---

## Step 2 — Spin up Postgres

If you don't already have a Postgres running, use chunkshop's test
compose file:

```bash
# From the chunkshop repo root:
docker compose -f docker-compose.test.yaml up -d
```

That starts Postgres 16 on `localhost:5434` with pgvector preinstalled.
The default DSN:

```bash
export CHUNKSHOP_DSN='postgresql://postgres:postgres@localhost:5434/chunkshop_test'
```

Sanity check:

```bash
psql "$CHUNKSHOP_DSN" -c 'SELECT 1'
# Output: ?column? = 1
```

If you have your own Postgres, just set `CHUNKSHOP_DSN` to it and make
sure `CREATE EXTENSION vector` works.

---

## Step 3 — Get a GitHub PAT (optional but recommended)

Anonymous GitHub API is rate-limited to 60 requests/hour. Each file
fetch is one request, so any non-trivial repo will hit the limit.

```bash
# Create a fine-grained PAT at https://github.com/settings/tokens?type=beta
# Scope: read-only access to public repos
export GITHUB_TOKEN=ghp_…
```

For private repos you need full `repo` scope.

---

## Step 4 — Write the cell config

Save this as `repo.yaml`:

```yaml
cell_name: chunkshop_ingest

source:
  type: connector
  connector: github
  config:
    owner: yonk-labs
    repo: chunkshop
    branch: main
    paths_glob:
      - "python/src/**/*.py"
      - "python/connectors/src/**/*.py"
    # token falls back to $GITHUB_TOKEN
  sync:
    mode: cursor

chunker:
  type: symbol_aware
  granularity: function
  include_imports: true
  max_chars: 8000

extractor:
  type: composite
  extractors:
    - type: code_summary
      backend: lede
      max_length: 280
      file_summary: true
    - type: code_relationships
      unique_match_confidence: 0.9
      ambiguous_match_confidence: 0.5

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  batch_size: 64
  threads: 2

target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: chunkshop_ingest
  table: chunks
  mode: overwrite
  source_tag: chunkshop_main
  hnsw: true
  vector_metric: cosine
  fts:
    enabled: true
    language: english
  promote_metadata:
    - { path: symbol_name, type: text }
    - { path: fqn,         type: text }
    - { path: symbol_type, type: text }
    - { path: language,    type: text }
    - { path: path,        type: text }
    - { path: summary,     type: text }
    - { path: start_line,  type: int }
    - { path: end_line,    type: int }

runtime:
  omp_num_threads: 1
  heartbeat_every: 25
```

Some load-bearing bits to call out:

- **`paths_glob`** restricts to two src trees so the ingest finishes in
  a few minutes. Drop the filter to ingest everything.
- **`granularity: function`** = one chunk per top-level def/class.
- **`include_imports: true`** prepends the file's imports to each
  chunk's `embedded_content`. The raw source slice stays in
  `original_content` (so grep/citation still works).
- **`promote_metadata`** is the load-bearing wiring for `--by-symbol`
  and `impact-of`. Without it, `symbol_name` only exists in jsonb
  metadata where the CLI's column predicates can't reach it.
- **`fts.enabled: true`** creates a Postgres GIN tsvector index so
  `chunkshop search` can use the FTS leg alongside semantic.
- **`Xenova/bge-base-en-v1.5-int8`** is chunkshop's shipped default
  int8 embedder (768 dim, MTEB-backed). Downloaded on first run; cached
  at `~/.cache/fastembed/`. Swap to `Xenova/bge-small-en-v1.5-int8`
  (384 dim, ~30 MB) for a smaller footprint — set `dim: 384` to match.

---

## Step 5 — Ingest

```bash
chunkshop ingest --config repo.yaml
```

What you'll see (timing approx):

```
[chunkshop.runner] cell_name=chunkshop_ingest starting
[chunkshop.runner] source: GitHub connector resolved 412 files matching paths_glob
[chunkshop.runner] heartbeat doc=25 chunks_emitted=148 elapsed=23s
[chunkshop.runner] heartbeat doc=50 chunks_emitted=312 elapsed=51s
[chunkshop.runner] heartbeat doc=100 chunks_emitted=684 elapsed=2m12s
…
[chunkshop.runner] DONE docs=412 chunks=2547 elapsed=8m44s
[chunkshop.runner] write_edges schema=chunkshop_ingest rows=1893
```

If you see `StubError` or `UnknownConnectorError`, double-check
`chunkshop-connectors` is installed in the same venv as `chunkshop`.

Expected end state in Postgres:

```sql
SELECT count(*) FROM chunkshop_ingest.chunks;
-- around 2500 rows

SELECT count(DISTINCT doc_id) FROM chunkshop_ingest.chunks;
-- ~410

SELECT count(*) FROM chunkshop_ingest.code_edges WHERE confidence >= 0.7;
-- ~1500 high-confidence edges
```

If `code_edges` is empty, the SP-E runner integration may not have
fired. You can re-materialize manually:

```python
# materialize_edges.py
from chunkshop.config import load_config
from chunkshop.extractors import load_extractor
from chunkshop.extractors.code_relationships import (
    write_edges_schema, write_edges,
)
from chunkshop.runner import run_cell

cfg = load_config("repo.yaml")
extractor = run_cell(cfg)  # if your run_cell returns the extractor instance
write_edges_schema(cfg.target.resolve_dsn(), schema=cfg.target.database_name)
write_edges(extractor, dsn=cfg.target.resolve_dsn(),
            schema=cfg.target.database_name,
            project_id=cfg.cell_name)
```

(The exact entrypoint depends on the runner version — see
`docs/cookbook/code-search.md` for the canonical recipe.)

---

## Step 6 — Query #1: plain hybrid search

```bash
chunkshop search \
    --config repo.yaml \
    --query "iterate changes since a cursor" \
    --k 5
```

Expected (your output will vary by minor scoring):

```
1. [0.8712] HttpSource.iter_changes_since#0  def iter_changes_since(self, cursor: dict)…  symbol=iter_changes_since fqn=chunkshop.sources.http.HttpSource.iter_changes_since path=python/src/chunkshop/sources/http.py
2. [0.8534] PgTableSource.iter_changes_since#0  def iter_changes_since(self, cursor: dict)…  symbol=iter_changes_since fqn=chunkshop.sources.pg_table.PgTableSource.iter_changes_since path=python/src/chunkshop/sources/pg_table.py
3. [0.8417] S3Source.iter_changes_since#0  def iter_changes_since(self, cursor: dict)…  symbol=iter_changes_since fqn=chunkshop.sources.s3.S3Source.iter_changes_since path=python/src/chunkshop/sources/s3.py
4. [0.7889] GitHubConnector.iter_changes_since#0  def iter_changes_since(self, cursor: dict)…
5. [0.7621] GDriveConnector.iter_changes_since#0  def iter_changes_since(self, cursor: dict)…
```

The five hits are the five `iter_changes_since` implementations across
chunkshop. The hybrid (semantic + FTS) hit them despite varied
docstrings.

---

## Step 7 — Query #2: symbol-filtered search

What if I only want HTTP-related symbols?

```bash
chunkshop search \
    --config repo.yaml \
    --query "polite robots delay" \
    --by-symbol "HttpSource*" \
    --k 5
```

Expected:

```
1. [0.7891] HttpSource._polite_wait#0  def _polite_wait(self)…  symbol=_polite_wait fqn=chunkshop.sources.http.HttpSource._polite_wait
2. [0.7321] HttpSource._robots_for#0  def _robots_for(self, client, url)…  symbol=_robots_for
3. [0.7102] HttpSource._robots_allows#0  def _robots_allows(self, client, url)…
4. [0.6845] HttpSource._fetch_one#0  def _fetch_one(self, client, url, cursor_entry)…
5. [0.6731] HttpSource._request#0  def _request(self, client, url, *, if_none_match, if_modified_since)…
```

The `*` after `HttpSource` becomes a SQL `LIKE 'HttpSource%'` predicate
on the promoted `symbol_name` column. Only HTTP-related chunks
participate in the ranking.

For exact-match (no wildcard), pass comma-separated names:

```bash
chunkshop search \
    --config repo.yaml \
    --query "merge cursor deltas" \
    --by-symbol "merge_cursor,assert_cursor_advances"
```

---

## Step 8 — Query #3: impact-of

Who calls `HttpSource.iter_changes_since`?

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.http.HttpSource.iter_changes_since \
    --direction callers \
    --depth 1
```

Expected:

```
impact-of: chunkshop.sources.http.HttpSource.iter_changes_since
  callers (1 hop):
    [0.9] chunkshop.runner.run_cell  python/src/chunkshop/runner.py:88
```

What does `HttpSource.iter_changes_since` call (depth 1)?

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.http.HttpSource.iter_changes_since \
    --direction callees \
    --depth 1
```

```
  callees (1 hop):
    [0.9] chunkshop.sources.http.HttpSource._crawl  python/src/chunkshop/sources/http.py:348
```

Both directions, depth 2, as JSON:

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.http.HttpSource.iter_changes_since \
    --direction both \
    --depth 2 \
    --json
```

The JSON output shape is documented in
[`docs/reference/cli-impact-of.md`](reference/cli-impact-of.md).

---

## Step 9 — Re-ingest is cheap

Run the same `chunkshop ingest --config repo.yaml` again. The cursor
GitHub uses (`{after_commit_sha}`) is now at the branch's current head,
so the `/compare` endpoint returns zero changed files:

```
[chunkshop.runner] heartbeat doc=0 chunks_emitted=0 elapsed=1.2s
[chunkshop.runner] DONE docs=0 chunks=0 elapsed=2.4s
```

That's the IncrementalSource contract paying off. If you push a commit
to the repo and re-run, only the touched files re-ingest.

---

## Step 10 — Where to go next

You've seen the three queries chunkshop's code-search story delivers.
Next stops:

### More chunkshop primitives

- **[`docs/cookbook/code-and-docs-kbs.md`](cookbook/code-and-docs-kbs.md)** —
  the two-KB pattern: split code (symbol_aware) and docs
  (sentence_aware) into separate cells using the same target schema,
  with cross-KB hybrid search.
- **[`docs/cookbook/file-parsing.md`](cookbook/file-parsing.md)** —
  ingesting PDF / DOCX / PPTX / XLSX / HTML via the `files` source.
- **[`docs/cookbook/incremental-sources.md`](cookbook/incremental-sources.md)** —
  cursor mechanics, `StaleCursorError` handling, the consumer's
  sync-loop contract.
- **[`docs/cookbook/authoring-connectors.md`](cookbook/authoring-connectors.md)** —
  writing a new connector plugin.

### Other connectors

- **[`docs/reference/source-gdrive.md`](reference/source-gdrive.md)** —
  ingesting Google Drive folders via OAuth.
  Live demo: `python/connectors/examples/e2e_gdrive_real_flow.py`.
- **[`docs/reference/source-blob.md`](reference/source-blob.md)** —
  S3 / R2 / GCS / MinIO with the verified `blob` connector.
- **[`docs/reference/source-rss.md`](reference/source-rss.md)** —
  RSS / Atom feed ingest.
- **[`docs/reference/source-http.md`](reference/source-http.md)** —
  depth-bounded URL crawler.

### Real-world demo (the big one)

`python/connectors/examples/e2e_real_world_5kbs.py` clones 3 GitHub
repos (RAGFlow, lede, chunkshop), cross-cuts the .md files into a 4th
KB, downloads 5 arxiv PDFs + 4 LLM-quality MD briefs + 7 ClickHouse
rows into a 5th topical KB. Then runs 3 hybrid queries × 5 KBs = 15
searches. Wall time ~14 minutes. The full end-to-end proof that
chunkshop's primitives compose for a real corpus.

### Agent-shaped digest

For a single doc an LLM agent can read end-to-end and generate working
configs from, see **[`docs/AGENT_REFERENCE.md`](AGENT_REFERENCE.md)**.

### Reference docs

Per-surface reference index: **[`docs/reference/README.md`](reference/README.md)**.

---

## Troubleshooting

### `pydantic.ValidationError: extra fields not permitted`

Typo in your YAML. Every chunkshop config block uses
`extra="forbid"` — pydantic v2's error message points at the exact
path.

### `chunkshop_connectors._stub.StubError: connector 'foo' is registered as experimental`

You used an experimental-tier connector. The four verified connectors
are `blob`, `rss`, `github`, `gdrive`. See
[`docs/reference/experimental-connectors.md`](reference/experimental-connectors.md)
for the full list and workarounds (e.g., use `blob` for R2/GCS/OCI).

### "Search returns nothing despite ingest succeeding"

Most common cause: `--by-symbol` requires `symbol_name` in
`promote_metadata`. Re-ingest with the snippet from step 4.

### "impact-of returns (no edges found)"

`code_edges` table is empty. Re-ingest, or run the
`write_edges_schema` + `write_edges` snippet from step 5.

### "GitHub returned 403 rate-limited"

Either set `GITHUB_TOKEN` (5k req/hr authenticated vs 60 req/hr
anonymous) or wait an hour and retry.

### "Embedder download is slow / hangs"

First run downloads ~30 MB from HuggingFace. Set `HF_HUB_OFFLINE=0`
and check network. The cache lives at `~/.cache/fastembed/`.

---

You now have a working code-search index over a real repo. Adapt the
config to your own repo, add `[code]` + `[lede]` to your install, and
you're done.
