# Incremental sources

chunkshop sources can do more than a full re-read on every run. When a source
knows how to detect *what changed* since the last run, it implements the
`IncrementalSource` protocol — and optionally `PrunableSource` for detecting
source-side deletions.

The contract is deliberately thin: **chunkshop computes deltas; the consumer
persists cursors and schedules runs.** chunkshop never stores a cursor, never
runs a scheduler, and never owns a queue. Those are your service's job (or
`chunkshop_api`'s). This page documents the primitives and a worked consumer
loop.

## SyncMode

Every `Source` carries a `sync_mode` class attribute declaring how it detects
change between runs (`chunkshop.sources.base.SyncMode`):

| Value | Meaning |
|---|---|
| `SyncMode.FULL_RESYNC` | No change detection — re-read everything every run. The default. |
| `SyncMode.CURSOR` | Cursor-based: ask "what changed since this opaque cursor?" |
| `SyncMode.FINGERPRINT` | Per-document fingerprint comparison (an ETag/hash map). In chunkshop this is surfaced *as* a cursor so the consumer persists one opaque dict. |

```python
from chunkshop.sources.base import SyncMode

SyncMode.FULL_RESYNC == "full_resync"   # it's a str Enum
SyncMode.CURSOR == "cursor"
SyncMode.FINGERPRINT == "fingerprint"
```

## The `IncrementalSource` protocol

```python
@runtime_checkable
class IncrementalSource(Protocol):
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...
```

`runtime_checkable`, so you can probe any source:

```python
from chunkshop.sources.base import IncrementalSource
isinstance(src, IncrementalSource)   # True if it has the three methods
```

The three methods:

- **`empty_cursor()`** → the starting cursor for a never-before-synced source.
  Conventionally `{}`.
- **`iter_changes_since(cursor)`** → yields the `Document`s that changed since
  `cursor`. Passing `empty_cursor()` yields everything (a full resync).
- **`cursor_from(last_document)`** → derive the *next* cursor from the last
  emitted document. The shape is source-specific (see below); the consumer
  treats it as opaque and persists it.

### The `Document` shape

```python
@dataclass(frozen=True)
class Document:
    id: str
    content: str
    title: Optional[str] = None
    metadata: Optional[dict] = None
    fingerprint: Optional[str] = None
```

`fingerprint` is the field incremental sources use to carry an ETag / content
hash / version stamp through to the cursor logic. It defaults to `None` for
sources that don't track it.

## Cursor shapes are source-specific

The cursor is an **opaque dict** to the consumer — but each source defines its
own shape. Two concrete shapes ship today:

### S3: a `{key: etag}` map (`chunkshop.sources.s3.S3Source`)

`sync_mode = SyncMode.CURSOR`. The canonical cursor is the **full map of object
key → ETag**. `iter_changes_since(cursor)` lists the bucket+prefix and yields
only objects whose current ETag differs from the one in the cursor:

```python
src = S3Source(cfg)                       # cfg is config.S3Source
cursor = src.empty_cursor()               # {}
first = list(src.iter_changes_since(cursor))   # everything; each Document.fingerprint == its ETag

# persist the full key->etag map as the cursor:
cursor = {d.metadata["key"]: d.fingerprint for d in first}

# next run: unchanged ETags are skipped, only changed/new keys re-emit
changed = list(src.iter_changes_since(cursor))
```

Note the asymmetry: `S3Source.cursor_from(last_document)` returns a **single**
`{key: etag}` entry — a per-document checkpoint helper. The consumer **merges**
each returned single-key dict into the running map. For S3 the authoritative
cursor is the accumulated map, not any single `cursor_from` result. (See the
docstring on `S3Source.cursor_from`.)

### Postgres table: a `{"after": "<iso-timestamp>"}` cursor (`chunkshop.sources.pg_table.PgTableSource`)

`sync_mode = SyncMode.CURSOR`, but **only effective when the config sets
`updated_at_column`**. Without it, `iter_changes_since` falls back to a full
resync (`iter_documents`).

When set, `iter_changes_since({"after": ts})` runs
`... WHERE <updated_at_column> > %s ORDER BY <updated_at_column>` and stamps
each row's timestamp into `metadata["_updated_at"]`. `cursor_from` reads that
back:

```python
cfg = PgTableSource(type="pg_table", dsn=DSN, database="public", table="docs",
                    id_column="id", content_column="body",
                    updated_at_column="updated_at")
src = PgTableSource(cfg)
first = list(src.iter_changes_since(src.empty_cursor()))   # {} -> all rows, oldest first
cursor = src.cursor_from(first[-1])                        # {"after": "<iso ts of last row>"}
# later: only rows with updated_at > cursor["after"]
again = list(src.iter_changes_since(cursor))
```

### HTTP: `{url: {etag, last_modified}}` + optional depth-bounded crawl (`chunkshop.sources.http.HttpSource`)

`sync_mode = SyncMode.CURSOR`. The cursor is a **per-URL** map of conditional-GET
headers. On each sync, every URL is re-fetched with `If-None-Match: <etag>` and
`If-Modified-Since: <last_modified>`; a `304 Not Modified` response is skipped
silently. New ETags / Last-Modified values are emitted as a per-doc cursor delta
that consumers merge into a running map (same shape as S3).

```python
from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.http import HttpSource

cfg = Cfg(
    type="http",
    urls=["https://docs.example.com/index"],
    crawl_depth=1,            # follow <a href> one hop
    allow_external=False,     # default — same-host only
    request_delay_seconds=0.5,
    respect_robots=True,
    max_pages=200,
    user_agent="myorg-bot/1.0 (+https://myorg.example/bot)",
)
src = HttpSource(cfg)

cursor = src.empty_cursor()                          # {}
first  = list(src.iter_changes_since(cursor))        # everything
# Each Document carries metadata["etag"] / ["last_modified"] when the server
# sends them. cursor_from(doc) returns the per-URL delta; merge into running map:
new_cursor = dict(cursor)
for d in first:
    new_cursor.update(src.cursor_from(d))
# new_cursor == {url: {"etag": "...", "last_modified": "..."}, ...}

# Next sync: unchanged URLs return 304 and are skipped.
again  = list(src.iter_changes_since(new_cursor))
```

**Crawl semantics.** `crawl_depth=0` is the legacy behavior (fetch only the
URLs in `cfg.urls` / `cfg.sitemap`). `crawl_depth>=1` does a BFS:

- Extracts `<a href>` links from `text/html` bodies via `beautifulsoup4`
  (`[html]` extra).
- Skips `mailto:`, `javascript:`, `tel:`, and fragment-only links.
- Resolves relative hrefs with `urljoin` against the page URL.
- Normalizes URLs (lowercase scheme/host, strip fragment) before the
  visited-set check — so `http://a.test`, `http://A.TEST/`, and
  `http://a.test/#section` collapse to one fetch.
- Filters out off-host links unless `allow_external=True`.
- Caps the total crawl at `max_pages` (default 1000) — a defensive belt
  against runaway link graphs.

**Politeness.** A minimum delay of `request_delay_seconds` (default 0.5s) is
enforced between outbound requests; `respect_robots=True` fetches each host's
`/robots.txt` once and honors `Disallow:` rules; the `User-Agent` is
configurable so target sites can identify the crawler.

**Non-text MIMEs.** Bodies with a Content-Type that isn't `text/*` or
`application/json` / `application/xml` are skipped with a warning. To ingest
PDFs/DOCX/etc., download with `yonk-doctools` and feed via the `files` source.

**Cookbook example.** `python/examples/crawl_url.py` is a runnable demo:

```bash
python examples/crawl_url.py https://example.com 2
```

prints one line per fetched URL with byte count.

## Stale cursors

A cursor can outlive what the source can honor — an API page token expires, a
WAL position is pruned, a `since` token is rejected. The source raises
`StaleCursorError` (`chunkshop.sources.base.StaleCursorError`):

```python
from chunkshop.sources.base import StaleCursorError

try:
    docs = list(src.iter_changes_since(saved_cursor))
except StaleCursorError:
    # fall back to a full resync
    docs = list(src.iter_changes_since(src.empty_cursor()))
```

The consumer treats this as a signal to fall back to a full resync — call
`iter_changes_since(empty_cursor())` (or `iter_documents()`).

## Pruning: detecting source-side deletions

Change-detection finds *new and modified* documents. It does not find
*deletions* — a document removed at the source won't appear in
`iter_changes_since`. Sources that can enumerate deletions implement
`PrunableSource`:

```python
@runtime_checkable
class PrunableSource(Protocol):
    def empty_prune_cursor(self) -> dict: ...
    def iter_deleted_since(self, cursor: dict) -> Iterable[str]: ...
```

`iter_deleted_since` returns **source IDs** (the `Document.id` values), not
`Document` objects — the consumer uses them to delete the corresponding rows
from its vector table.

### Freshness vs prune cadence

Prune detection often requires walking the **full** source manifest (to see
what's *missing*), which is far more expensive than an incremental "what
changed" query. So the two run at different cadences. `SyncSettings`
(`chunkshop.config.SyncSettings`) declares both for the consumer's scheduler:

```python
class SyncSettings(_Base):
    mode: Literal["full_resync", "cursor", "fingerprint"] = "full_resync"
    refresh_freq_seconds: Optional[int] = None   # how often to iter_changes_since
    prune_freq_seconds: Optional[int] = None      # how often to iter_deleted_since
```

In YAML, on a `connector` source:

```yaml
source:
  type: connector
  connector: gdrive
  config:
    folder_id: abc123
  sync:
    mode: cursor
    refresh_freq_seconds: 3600     # check for changes hourly
    prune_freq_seconds: 86400      # walk the manifest for deletions daily
```

chunkshop does not act on these values — it surfaces them so your orchestrator
knows the source's intended cadence.

## A worked consumer loop

The loop a consumer drives: `empty_cursor → iter_changes_since → cursor_from →
persist`. Cursors come from *your* durable store (a DB row, Redis, a file) —
chunkshop never persists them.

```python
from chunkshop.sources.base import IncrementalSource, StaleCursorError

def sync_one(name, source, load_cursor, save_cursor, ingest):
    """Drive a single incremental source one cycle.

    load_cursor()  -> the persisted cursor dict (or empty_cursor() if never run)
    save_cursor(c) -> persist the new cursor to your durable store
    ingest(doc)    -> hand the Document to your chunk -> embed -> sink pipeline
    """
    if not isinstance(source, IncrementalSource):
        # full-resync source: just re-read everything
        for doc in source.iter_documents():
            ingest(doc)
        return

    cursor = load_cursor() or source.empty_cursor()
    try:
        docs = list(source.iter_changes_since(cursor))
    except StaleCursorError:
        # cursor too old — fall back to full resync from empty
        cursor = source.empty_cursor()
        docs = list(source.iter_changes_since(cursor))

    if not docs:
        return  # nothing changed; cursor unchanged

    for doc in docs:
        ingest(doc)

    # advance + persist the cursor. For sources whose canonical cursor is a
    # full map (S3), accumulate cursor_from results into the running cursor:
    new_cursor = dict(cursor)
    for doc in docs:
        new_cursor.update(source.cursor_from(doc))
    save_cursor(new_cursor)
```

For a runnable, semaphore-bounded version that runs many sources concurrently
and isolates per-source failures, copy `examples/sync_loop.py`. That file is a
copy-me starting point, **not** part of the installed library — production
scheduling, retries, and durable cursor storage are your service's
responsibility.

## See also

- [`authoring-connectors.md`](authoring-connectors.md) — write a connector
  plugin that implements these protocols.
- `chunkshop.testing.assert_cursor_advances` /
  `assert_idempotent_on_re_emit` — drop-in tests for your `IncrementalSource`.
- `examples/sync_loop.py` — the copy-me consumer loop.
