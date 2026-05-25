# `rss` connector

**Module**: `chunkshop_connectors.rss`
**Type**: Source (verified-tier connector)
**Ship status**: verified
**Optional extra**: `chunkshop-connectors[rss]` (feedparser)
**Since**: 2026-05-25 (commit `667e92a`)

## Purpose

Ingest entries from a single RSS or Atom feed via `feedparser`. Each
entry becomes one chunkshop `Document` whose `fingerprint` is the
entry's GUID (or a fallback composite) — chunkshop's fingerprint sync
path then dedupes unchanged entries automatically.

For multiple feeds, use multiple connector cells (one per feed). The
config intentionally accepts a single `url`.

## Config schema

`chunkshop_connectors.rss.ConfigModel` (pydantic v2, `extra="forbid"`):

| Field        | Type    | Default | Notes |
|--------------|---------|---------|-------|
| `url`        | `str`   | **Required**, `min_length=1` | Feed URL. |
| `timeout`    | `int`   | `30`    | Reserved — feedparser ignores it today, kept for forward-compat. |
| `user_agent` | `str?`  | `None`  | Optional User-Agent override. |

## Public API

```python
class RssConnector:
    sync_mode = SyncMode.FINGERPRINT

    def __init__(self, config: dict[str, Any]) -> None: ...

    def iter_documents(self) -> Iterator[Document]: ...
```

Factory: `chunkshop_connectors.rss.factory(config: dict) -> RssConnector`.

## Behavior contract

1. **Sync mode is `FINGERPRINT`.** Every entry yields a Document; consumer
   dedupes via `fingerprint`.
2. **Fingerprint priority:** `entry.id` > `entry.guid` > `f"{link}|{updated}"` > `None`.
3. **Content priority:** Atom `entry.content` (joined `.value` fields)
   > RSS `entry.summary` > `""`.
4. **Bozo feeds don't kill the sync.** feedparser sets `bozo=1` on
   parse errors; the connector logs a warning and yields whatever
   entries parsed.
5. **feedparser is imported lazily** inside `iter_documents`.

## Inputs

- One feed URL (RSS 2.0, Atom 1.0, RDF/RSS 1.0 — anything feedparser handles).

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | `entry.id` / `entry.guid` / `entry.link` / `entry.title` |
| `content`     | Joined `entry.content[*].value` or `entry.summary` |
| `title`       | `entry.title` or `"(untitled)"` |
| `metadata`    | `{link?, published?, author?}` (omitted when absent) |
| `fingerprint` | Stable per-entry — see priority list above |

## Errors

| Exception | When |
|-----------|------|
| `pydantic.ValidationError` | At `factory()` time — extra keys, missing `url`. |
| (None at iteration time) | feedparser is intentionally tolerant; partial feeds yield entries, malformed feeds log a warning and yield zero. |

## Example: minimal

```yaml
cell_name: news_feed
source:
  type: connector
  connector: rss
  config:
    url: https://example.com/feed.xml
chunker: {type: sentence_aware, max_chars: 1500}
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: news
  table: chunks
  mode: append
  source_tag: example_feed
```

## Example: realistic

```yaml
cell_name: company_blog
source:
  type: connector
  connector: rss
  config:
    url: https://blog.company.example/feed.atom
    user_agent: chunkshop/0.5 (+https://github.com/yonk-labs/chunkshop)
  sync: {mode: fingerprint, refresh_freq_seconds: 1800}
chunker: {type: hierarchy, prefix_heading: true}
extractor:
  type: composite
  extractors:
    - type: rake_keywords
      top_k: 10
    - type: lang_detect
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: feeds
  table: chunks
  mode: append
  source_tag: company_blog
  promote_metadata:
    - {path: link, type: text}
    - {path: published, type: timestamptz}
    - {path: author, type: text}
```

## How it integrates with the pipeline

`RssConnector` is a `Source`. Combined with `mode: append`, multiple
feed cells can stream into the same chunks table side-by-side with
different `source_tag` values.

## Tests proving the contract

- `python/connectors/tests/test_rss_connector.py`:
  - registry + tier marker
  - `ConfigModel` validation
  - hermetic feed parsing via
    `chunkshop_connectors.testing.mocks.rss.FakeRssFeed` (in-memory feedparser source)
  - GUID-priority fingerprint resolution
  - Atom-content vs RSS-summary priority
  - bozo-feed warning + best-effort yield

## See also

- [`docs/connectors/_status.md`](../connectors/_status.md)
- Reference: [`source-http`](source-http.md) — for URL fetch + crawl
  (useful if you need the full page body, not just the feed entry)
