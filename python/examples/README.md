# chunkshop examples

Copy-me reference code, **not** part of the installed `chunkshop` library.
These files show how a *consumer* drives chunkshop's primitives. Copy them into
your own service and adapt — don't import from `examples` in production.

For connector-specific end-to-end demos (live GitHub, mocked Drive, S3 ETag
sync, URL crawl, full pgvector pipeline, the 5-KB real-world test), see
[`python/connectors/examples/`](../connectors/examples/).

## Quick map

| Demo | What it shows | Verifies | Prerequisites |
|---|---|---|---|
| `chunk_python_code.py` | The `code_aware` (Python AST) chunker against a single .py file | `metadata.node_type` + `node_name` stamping; module_block + def chunks emitted | None (stdlib only) |
| `code_search_demo.py` | Full `symbol_aware` + `code_relationships` + `code_summary` pipeline against a real repo; `chunkshop search --by-symbol` + `impact-of` queries | The SP-E end-to-end story — symbol search + impact graph against the chunkshop repo itself | Postgres on `:5434`; `chunkshop[code,lede]` |
| `code_and_docs_kbs_demo.py` | Two-KB pattern: code (symbol_aware) + docs (sentence_aware) ingest into the same schema, cross-KB search | The Track-2 two-KB orthogonality test — code and docs filter cleanly via `source_tag` | Postgres on `:5434`; `chunkshop[code]` |
| `crawl_url.py` | URL ingest with depth crawl + ETag/Last-Modified cursor refresh | The depth-bounded `HttpSource` crawler — conditional GET, robots.txt, sitemap | Live internet; `chunkshop[html]` for HTML→text |
| `parse_corpus.py` | Single-shot ingest of a mixed-format corpus via `FilesSource` + dispatched parsers | PDF/DOCX/etc. parser dispatch by extension | Depends on parsers you exercise: `chunkshop[pdf,docx,...]` |
| `sync_loop.py` | Minimal asyncio sync-loop driving multiple `IncrementalSource` instances | The consumer-side orchestration contract — fan-out, per-source isolation, cursor persistence (your responsibility) | None — illustrative |

## Detailed entries

### `chunk_python_code.py`

Reads a Python file from disk, runs it through `code_aware`, prints
one chunk per top-level def/class plus the leading `module_block`
chunk (imports + constants).

Verifies user expectation: "I want one chunk per function so my LLM
sees clean call sites." Tag: parser-determinism, AST visibility.

**Prerequisites:** none.

```bash
python chunk_python_code.py /path/to/some/module.py
```

### `code_search_demo.py`

End-to-end demo of the SP-E story. Clones a repo (default: chunkshop
itself), ingests with `symbol_aware` + `code_relationships` +
`code_summary`, then runs three queries:

1. Plain semantic search.
2. `--by-symbol` filtered search.
3. `chunkshop impact-of` against an FQN.

Verifies user expectation: "I want to ask 'who calls this symbol' and
'what does this symbol call' against my own codebase."

**Prerequisites:** Postgres on `:5434`; `chunkshop[code,lede]`;
`chunkshop-connectors[github]` (only if cloning instead of using a
local checkout).

```bash
docker compose -f docker-compose.test.yaml up -d
pip install "chunkshop[code,lede]" "chunkshop-connectors[github]"
python code_search_demo.py
```

### `code_and_docs_kbs_demo.py`

Two-cell ingest pattern documented in
[`docs/cookbook/code-and-docs-kbs.md`](../../docs/cookbook/code-and-docs-kbs.md):

- Cell A: `source: files (**.py)` + `symbol_aware` + `code_summary` →
  same schema, `source_tag=code`.
- Cell B: `source: files (**.md, **.rst)` + `sentence_aware` →
  same schema, `source_tag=docs`.

Then runs the same query against both KBs with `--where source=code`
vs `--where source=docs` to show clean orthogonality.

Verifies user expectation: "I want code and docs in the same index but
queryable independently."

**Prerequisites:** Postgres on `:5434`; `chunkshop[code]`.

```bash
python code_and_docs_kbs_demo.py
```

### `crawl_url.py`

Hits a real public URL (default `https://example.com`) with the
depth-bounded `HttpSource`. Persists cursor at
`/tmp/chunkshop-demo-url-cursor.json`; second run sends
`If-None-Match` → 304 → 0 fresh pages.

Verifies user expectation: "I want to crawl a docs site at depth N
with conditional-GET incremental sync."

**Prerequisites:** internet; `chunkshop[html]` for HTML→text parsing
(if seed is HTML).

```bash
python crawl_url.py
python crawl_url.py --seed https://example.org --depth 1
python crawl_url.py --reset
```

### `parse_corpus.py`

Single-shot `FilesSource` ingest over a mixed-format corpus. Useful
for sanity-checking that your installed parser extras dispatch
correctly on a directory of PDFs / DOCXs / Markdowns.

Verifies user expectation: "I drop a directory of mixed files in and
the right parser fires per extension."

**Prerequisites:** install the parser extras you want to exercise:
`chunkshop[pdf]`, `chunkshop[docx]`, `chunkshop[office]`, etc.

### `sync_loop.py`

A minimal, semaphore-bounded async sync loop that drives the
incremental-source primitives (`IncrementalSource` / `PrunableSource`)
across many sources concurrently, isolating per-source failures.

It is the demoted `SourceTaskRunner` (originally numbered #21 in the
SP-1 design): chunkshop intentionally ships the *contracts* (sync
protocols, cursor shapes, `StaleCursorError`) and leaves *orchestration*
to the consumer. This file is the baseline you copy and grow.

**Usage:**

```python
import asyncio
from examples.sync_loop import run_sync

results = asyncio.run(run_sync(
    sources={"docs": my_source},
    cursors={"docs": saved_cursor},          # from YOUR durable store
    on_document=lambda name, doc: ingest(doc),
    max_concurrent_tasks=4,
))
for name, r in results.items():
    if r.success:
        persist_cursor(name, r.new_cursor)   # persisting cursors is YOUR job
    else:
        log.error("sync %s failed: %r", name, r.error)
```

### What `sync_loop.py` intentionally does NOT do

Production orchestration belongs in your service or `chunkshop_api`, not here:

- Scheduling / cron / cadence (see `SyncSettings.refresh_freq_seconds`).
- Retries and backoff.
- Durable cursor persistence (the loop returns the new cursor; *you* store it).
- Multi-tenant isolation, queues, Redis, distributed locking.
- `StaleCursorError` fallback to full resync (shown in
  [`docs/cookbook/incremental-sources.md`](../../docs/cookbook/incremental-sources.md)).

## See also

- [`python/connectors/examples/`](../connectors/examples/) — the eight
  runnable connector demos (`e2e_*.py`)
- [`docs/cookbook/incremental-sources.md`](../../docs/cookbook/incremental-sources.md) —
  the contract `sync_loop.py` consumes
- [`docs/cookbook/authoring-connectors.md`](../../docs/cookbook/authoring-connectors.md) —
  how to write the source `sync_loop.py` would drive
- [`docs/cookbook/code-search.md`](../../docs/cookbook/code-search.md) —
  the recipe `code_search_demo.py` implements
- [`docs/cookbook/code-and-docs-kbs.md`](../../docs/cookbook/code-and-docs-kbs.md) —
  the recipe `code_and_docs_kbs_demo.py` implements
- [`docs/AGENT_REFERENCE.md`](../../docs/AGENT_REFERENCE.md) —
  self-contained doc an LLM agent can read end-to-end
