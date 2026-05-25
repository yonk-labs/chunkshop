# `gdrive` connector

**Module**: `chunkshop_connectors.gdrive`
**Type**: Source (verified-tier connector)
**Ship status**: verified
**Optional extra**: `chunkshop-connectors[gdrive]` (httpx)
**Since**: 2026-05-25 (commit `0126bda`)

## Purpose

Ingest text-shaped files from a Google Drive folder (or arbitrary Drive
query). Backed by raw `httpx` against the Drive v3 REST API — no
`google-api-python-client` dependency. Incremental sync uses Drive's
Changes API (`/changes`) keyed on a `pageToken` cursor so re-syncs only
re-emit changed files.

## Config schema

`chunkshop_connectors.gdrive.ConfigModel` (pydantic v2, `extra="forbid"`):

| Field            | Type            | Default                                                | Notes |
|------------------|-----------------|--------------------------------------------------------|-------|
| `folder_id`      | `str?`          | `None`                                                 | Drive folder ID. Regex-checked `^[A-Za-z0-9_-]+$`. |
| `query`          | `str?`          | `None`                                                 | Raw Drive v3 query string. |
| `scopes`         | `list[str]`     | `["https://www.googleapis.com/auth/drive.readonly"]`   | OAuth scopes. |
| `oauth_tokens`   | `dict?`         | `None` → falls back to `$GDRIVE_OAUTH_TOKENS` (JSON)   | Serialized `OAuthTokens` dict. Redacted in `__repr__`. |
| `drive_base_url` | `str`           | `"https://www.googleapis.com/drive/v3"`                | Override for tests / proxies. |

Validation:

- Either `folder_id` or `query` MUST be set (validated post-parse).
- If both are set, they are AND'd into one Drive query.

## Public API

```python
class GDriveConnector:
    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...

    # IncrementalSource
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...

    # Test hook (production leaves _transport=None)
    _transport: Optional[httpx.BaseTransport]
    def _reset_client(self) -> None: ...
```

Factory: `chunkshop_connectors.gdrive.factory(config: dict) -> GDriveConnector`.

OAuth provider: see [`oauth-google`](oauth-google.md) for the
`GoogleOAuthProvider` that mints the `oauth_tokens` dict.

## Behavior contract

1. **Sync mode is `CURSOR`.** Cursor shape: `{"page_token": "<drive_token>"}`.
2. **Empty cursor → full folder walk** via `/files?q=...&pageSize=100`, +
   seed the cursor by fetching `/changes/startPageToken`.
3. **Non-empty cursor → `/changes?pageToken=...&includeRemoved=false`**.
   Only files with `removed != true` are re-emitted. Removals are not
   surfaced — the connector does NOT implement `PrunableSource`.
4. **MIME-type allowlist (read `_TEXT_MIME_TYPES` in source):**
   `text/plain`, `text/markdown`, `text/x-markdown`, `text/html`,
   `application/json`, plus `application/vnd.google-apps.document`
   (exported as text). Everything else is skipped with `UserWarning`.
5. **OAuth resolution is lazy.** Validated at `__init__` only that the
   dict-or-env-var is present; the access token is parsed on first
   `_get_json` call. This means a config missing tokens fails on
   `iter_documents`, not on `factory`.
6. **Cursor advances monotonically** — every Document in a sync carries
   the same `metadata.next_page_token`.
7. **Token never logs.** `oauth_tokens` is redacted in `ConfigModel.__repr__`
   and `GDriveConnector.__repr__`.

## Inputs

- Google Drive v3 REST endpoints: `/files`, `/files/{id}/export`,
  `/files/{id}?alt=media`, `/changes`, `/changes/startPageToken`.
- OAuth bearer access token from a `chunkshop.oauth.OAuthTokens`-shaped dict.
- `folder_id` and/or `query` selecting which files to ingest.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | Drive file ID |
| `content`     | UTF-8 text (exported for Google Docs, raw for text/* MIMEs) |
| `title`       | Drive file `name` |
| `metadata`    | `{drive_id, mime_type, modified_time, parents, next_page_token}` |
| `fingerprint` | `None` (cursor-only) |

`metadata.next_page_token` is load-bearing — `cursor_from()` reads it.

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | `oauth_tokens` missing from config AND `$GDRIVE_OAUTH_TOKENS` env var unset. Raised on first API call. |
| `ValueError` | `$GDRIVE_OAUTH_TOKENS` not valid JSON, or no `access_token` field. |
| `httpx.HTTPStatusError` | Any non-2xx from Drive (403 quota / scope, 404 missing folder). The demos add an actionable hint that points at the Cloud Console activation URL. |
| `pydantic.ValidationError` | At `factory()` time — neither `folder_id` nor `query` set, or bad `folder_id` regex. |

Non-text MIME types yield `UserWarning` (one per file), not exceptions.

## Example: minimal

```yaml
cell_name: gdrive_docs
source:
  type: connector
  connector: gdrive
  config:
    folder_id: 0BabcDEF123
    oauth_tokens: ${GDRIVE_OAUTH_TOKENS}  # JSON-encoded dict
  sync: {mode: cursor}
chunker: {type: sentence_aware, max_chars: 2000}
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: gdrive_kb
  table: chunks
  mode: overwrite
```

## Example: realistic

```yaml
cell_name: gdrive_company_docs
source:
  type: connector
  connector: gdrive
  config:
    folder_id: 0BcompanyDocsFolderId
    scopes:
      - https://www.googleapis.com/auth/drive.readonly
    oauth_tokens: ${GDRIVE_OAUTH_TOKENS}
  sync:
    mode: cursor
    refresh_freq_seconds: 1800
chunker:
  type: hierarchy
  prefix_heading: true
  max_chars: 2000
extractor:
  type: composite
  extractors:
    - type: rake_keywords
      top_k: 8
    - type: lang_detect
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: company_kb
  table: chunks
  mode: overwrite
  source_tag: gdrive
  promote_metadata:
    - {path: drive_id,     type: text}
    - {path: mime_type,    type: text}
    - {path: modified_time, type: timestamptz}
```

## How it integrates with the pipeline

Standard `Source` — emits Documents; runner does the chunking/embedding.
The Drive Changes API is the only Google product whose incremental
semantics map cleanly to chunkshop's `IncrementalSource` protocol: one
opaque `pageToken` ↔ one chunkshop cursor dict.

For the OAuth bootstrap (consent URL + loopback callback + token
caching), see `python/connectors/examples/e2e_gdrive_real_flow.py`.

## Tests proving the contract

- `python/connectors/tests/test_gdrive_connector.py`:
  - registry + tier
  - `ConfigModel` validation: extra-key rejection, `folder_id` regex,
    `folder_id`-or-`query` requirement
  - hermetic folder walk via `httpx.MockTransport`
  - changes-API incremental sync emits only changed files
  - MIME skip with `UserWarning` for non-text files
  - Google Doc export round-trip
  - cursor monotonic / merges to single value
- `python/connectors/tests/test_google_oauth_provider.py` — Google
  OAuth provider tests (refresh-token preservation, scope validation).
- Live demo: `python/connectors/examples/e2e_gdrive_real_flow.py`
  (real OAuth loopback flow + ingest).
- Hermetic demo: `python/connectors/examples/e2e_gdrive_mocked.py`.

## See also

- [`docs/connectors/gdrive.md`](../connectors/gdrive.md) — OAuth setup walkthrough
- Reference: [`oauth-google`](oauth-google.md) — the Google OAuth provider
- Reference: [`oauth-protocols`](oauth-protocols.md) — the Protocol contract
- Reference: [`source-github`](source-github.md) — sibling verified connector
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md)
