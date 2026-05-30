# `http` source — depth-bounded crawl + ETag/Last-Modified incremental

**Module**: `chunkshop.sources.http`
**Type**: Source (chunkshop core — no extras package required)
**Ship status**: verified
**Optional extra**: `chunkshop[html]` (beautifulsoup4) for HTML-to-text + link extraction
**Since**: extended 2026-05-25 (commit `fcbad65`)

## Purpose

Fetch HTTP/HTTPS URLs and produce one chunkshop `Document` per page.
This session extended the legacy list-of-URLs source into a depth-bounded
BFS web crawler with conditional-GET incremental sync. With
`crawl_depth=0` (default) the source still does the original "fetch only
the seed URLs" behavior; with `crawl_depth >= 1` it follows links from
each seed up to that many hops.

## Config schema

`chunkshop.config.HttpSource` (pydantic v2, `extra="forbid"`):

| Field                   | Type        | Default                                            | Notes |
|-------------------------|-------------|----------------------------------------------------|-------|
| `type`                  | `Literal["http"]` | **Required** | Discriminator. |
| `urls`                  | `list[str]` | `[]`                                               | Seed URLs. |
| `sitemap`               | `str?`      | `None`                                             | Optional sitemap URL — its `<loc>` entries are added to the seed set. |
| `crawl_depth`           | `int`       | `0` (legacy: fetch seeds only)                     | `ge=0, le=5`. |
| `allow_external`        | `bool`      | `False`                                            | When True, follow off-host links during crawl. |
| `request_delay_seconds` | `float`     | `0.5`                                              | `ge=0`. Per-source minimum delay between outbound requests. |
| `respect_robots`        | `bool`      | `True`                                             | When True, fetch + honor `/robots.txt` per host (cached). |
| `max_pages`             | `int`       | `1000`                                             | `ge=1`. Hard runaway cap. |
| `user_agent`            | `str`       | `"chunkshop/0.6 (+https://github.com/yonk-labs/chunkshop)"` | Sent on every request + used for robots.txt evaluation. |

## Public API

```python
from chunkshop.sources.http import HttpSource
from chunkshop.config import HttpSource as Cfg

class HttpSource:
    sync_mode = SyncMode.CURSOR

    def __init__(self, cfg: Cfg, *, transport: Optional[httpx.BaseTransport] = None) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...

    # IncrementalSource
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterator[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...
```

`transport=` is a hermetic test hook (`httpx.MockTransport`); production
leaves it None.

## Behavior contract

1. **Sync mode is `CURSOR`.** Cursor shape:
   `{url: {"etag": "...", "last_modified": "..."}}` (per-URL map, same
   shape pattern as S3's `{key: etag}`).
2. **Conditional GETs.** When a cursor entry is present, the connector
   sends `If-None-Match` + `If-Modified-Since`. 304 Not Modified is
   silently skipped (no Document emitted).
3. **BFS crawl** with `crawl_depth` decremented per hop. Discovered URLs
   are queued; same-host filter applies unless `allow_external=True`
   (seed URL hostnames pin the allowed set).
4. **URL normalization** via `_normalize_url`: lowercase scheme + host,
   strip fragment, empty path → `/`. The normalized form is the visited-set
   and cursor key.
5. **MIME allowlist:** `text/html`, `text/plain`, `text/markdown`,
   `application/json`, `application/xml`, `text/xml`. Other MIMEs are
   skipped with a warning ("use the `files` source for binaries").
6. **HTML extraction.** For `text/html` responses: `_strip_html` removes
   `<script>` / `<style>` / `<noscript>`, then `BeautifulSoup.get_text("\n",
   strip=True)`. `<title>` content goes to `Document.title`. Anchors
   become crawl frontier entries.
7. **Polite delay.** `request_delay_seconds` enforces a minimum interval
   between any two outbound requests (per source, not per host).
8. **robots.txt.** One fetch per host (cached). Missing robots.txt is
   treated as "allow everything" per RFC 9309. Disallowed URLs are
   skipped with `log.info`.
9. **Hard cap on `max_pages`.** Once reached, the crawl stops emitting
   regardless of remaining frontier.
10. **HTML link extraction** requires bs4. If `[html]` extra is missing,
    `_strip_html` raises `RuntimeError` with an install hint; link
    extraction silently returns `[]`.

## Inputs

- Seed `urls` and/or `sitemap`.
- Optional cursor `{url: {etag, last_modified}}` from a previous sync.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | URL (NOT normalized — preserves the canonical form the server uses) |
| `content`     | Plain text (HTML→text via bs4 for HTML; raw body for text/* and JSON/XML) |
| `title`       | Extracted `<title>` for HTML; `None` for other MIMEs |
| `metadata`    | `{url, status_code, content_type, etag, last_modified}` |
| `fingerprint` | ETag if present, else None |

## Errors

| Exception | When |
|-----------|------|
| `pydantic.ValidationError` | Bad `crawl_depth` (>5), bad `max_pages` (<1), `request_delay_seconds` negative. |
| `RuntimeError` | HTML body encountered but `beautifulsoup4` not installed. |
| (None at fetch) | Network errors are logged and treated as skip — the crawl continues. |

## Example: minimal

```yaml
source:
  type: http
  urls: ["https://example.com"]
  # crawl_depth defaults to 0 → just fetches example.com
```

## Example: realistic (crawl + cursor)

```yaml
cell_name: docs_crawl
source:
  type: http
  urls:
    - https://docs.example.com/
  sitemap: https://docs.example.com/sitemap.xml
  crawl_depth: 2
  allow_external: false
  respect_robots: true
  max_pages: 200
  request_delay_seconds: 1.0
  user_agent: "chunkshop-docs-crawler/1.0 (+https://example.com/contact)"
chunker:
  type: hierarchy
  prefix_heading: true
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: docs
  table: chunks
  mode: overwrite
  promote_metadata:
    - {path: url, type: text}
    - {path: status_code, type: int}
```

## How it integrates with the pipeline

`HttpSource` is loaded by `chunkshop.sources.__init__.load_source` from
the `HttpSource` config discriminator. It's NOT a connector plugin —
it ships in chunkshop core because URL fetch is too foundational for
the plugin seam.

Cursor persistence is the consumer's responsibility:

```python
from chunkshop.sources.http import HttpSource
from chunkshop.testing import merge_cursor

src = HttpSource(cfg)
cursor = load_cursor() or src.empty_cursor()
docs = list(src.iter_changes_since(cursor))
cursor = merge_cursor(src, cursor, docs)
save_cursor(cursor)
```

## Tests proving the contract

- Core suite (`tests/chunkshop/test_http_source.py` and related):
  - depth-bounded BFS frontier expansion
  - 304 Not Modified path emits no Document but doesn't crash
  - cursor merging via `chunkshop.testing.merge_cursor`
  - sitemap parsing (both well-formed XML and the regex fallback)
  - robots.txt allow/deny + missing-robots fallback
  - polite-delay timing
  - HTML title extraction + bs4 link extraction
- Demo: `python/examples/crawl_url.py` and
  `python/connectors/examples/e2e_url_crawl.py`.
- Cookbook: [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md) §URL-crawling.

## See also

- Reference: [`source-blob`](source-blob.md), [`source-github`](source-github.md), [`source-gdrive`](source-gdrive.md) — other `IncrementalSource` implementations
- Reference: [`utility-testing`](utility-testing.md) — `merge_cursor` helper
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md)
- [`docs/cookbook/authoring-connectors.md`](../cookbook/authoring-connectors.md) — Source / IncrementalSource protocol
