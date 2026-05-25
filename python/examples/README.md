# chunkshop examples

Copy-me reference code, **not** part of the installed `chunkshop` library.
These files show how a *consumer* drives chunkshop's primitives. Copy them into
your own service and adapt — don't import from `examples` in production.

## `sync_loop.py`

A minimal, semaphore-bounded async sync loop that drives the incremental-source
primitives (`IncrementalSource` / `PrunableSource`) across many sources
concurrently, isolating per-source failures.

It is the demoted `SourceTaskRunner` (originally numbered #21 in the SP-1
design): chunkshop intentionally ships the *contracts* (sync protocols, cursor
shapes, `StaleCursorError`) and leaves *orchestration* to the consumer. This
file is the baseline you copy and grow.

### What it does

`run_sync(sources, cursors, on_document, on_delete=None, max_concurrent_tasks=5)`:

- Bounds concurrency with an `asyncio.Semaphore`.
- For each source: if it's an `IncrementalSource`, calls
  `iter_changes_since(cursor)` (off the event loop via `asyncio.to_thread`),
  feeds each `Document` to `on_document(name, doc)`, and computes the next
  cursor with `cursor_from`. Otherwise falls back to `iter_documents()`.
- Catches exceptions **per source** so one failing source can't kill its
  siblings. Each source gets a `TaskResult` recording `success`, `docs_emitted`,
  `new_cursor`, `error`, and `elapsed_ms`.

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

### What it intentionally does NOT do

Production orchestration belongs in your service or `chunkshop_api`, not here:

- Scheduling / cron / cadence (see `SyncSettings.refresh_freq_seconds`).
- Retries and backoff.
- Durable cursor persistence (the loop returns the new cursor; *you* store it).
- Multi-tenant isolation, queues, Redis, distributed locking.
- `StaleCursorError` fallback to full resync (shown in
  `docs/cookbook/incremental-sources.md`).

## See also

- [`../../docs/cookbook/incremental-sources.md`](../../docs/cookbook/incremental-sources.md)
- [`../../docs/cookbook/authoring-connectors.md`](../../docs/cookbook/authoring-connectors.md)
