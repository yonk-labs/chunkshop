# Google Drive connector

The `gdrive` connector walks a Google Drive folder (or runs a Drive
query) and yields one chunkshop `Document` per text-shaped file. It's
part of the verified tier — behaviourally tested against a hermetic
`httpx.MockTransport`-backed mock under
`chunkshop_connectors.testing.mocks.gdrive`.

## What you get

* One `Document` per file matching `folder_id` (or `query`) whose MIME
  type is **text-shaped** (see below).
* `Document.id` = the Drive file ID.
* `Document.title` = the file's display name.
* `Document.content` = the file body, UTF-8 decoded.
* `Document.metadata` carries `{drive_id, mime_type, modified_time, parents, next_page_token}`.

Non-text files (images, videos, Sheets, Slides, PDFs, etc.) are
**silently skipped** with a `UserWarning`. If you need PDF/DOCX
ingest, pre-process with `yonk-doctools` first, then feed the
resulting markdown via chunkshop's `files` source — not this
connector.

### Text-shaped MIME types

The connector emits `Document` content for:

| MIME type                                  | Source endpoint                          |
|--------------------------------------------|------------------------------------------|
| `application/vnd.google-apps.document`     | `GET /files/{id}/export?mimeType=text/plain` |
| `text/plain`                               | `GET /files/{id}?alt=media`              |
| `text/markdown`, `text/x-markdown`         | `GET /files/{id}?alt=media`              |
| `text/html`                                | `GET /files/{id}?alt=media`              |
| `application/json`                         | `GET /files/{id}?alt=media`              |

Everything else triggers a `warnings.warn("gdrive: skipping ...")` and
the file is dropped from the output stream.

## Authentication: OAuth 2.0

Auth is **OAuth 2.0 bearer token**. Tokens come from
`chunkshop_connectors.oauth.google.GoogleOAuthProvider` — the
connector itself doesn't run the consent flow.

### Required OAuth scopes

For read-only access (recommended), use:

```
https://www.googleapis.com/auth/drive.readonly
```

`drive.readonly` covers both `files.list`, `files.export`,
`files.get` (with `alt=media`), and the `changes` endpoints — i.e.,
everything this connector calls. No write scopes are used.

### Producing the tokens

Run the consent flow once via `GoogleOAuthProvider`:

```python
from chunkshop_connectors.oauth.google import GoogleOAuthProvider
from dataclasses import asdict
import json

prov = GoogleOAuthProvider(
    client_id="<your-cid>",
    client_secret="<your-csec>",
)
print(prov.authorization_url(
    state="csrf-nonce",
    redirect_uri="http://localhost:8765/cb",
    scopes=["https://www.googleapis.com/auth/drive.readonly"],
))
# ... user grants consent, callback receives ?code=...
tokens = prov.exchange_code(code="<from-callback>",
                            redirect_uri="http://localhost:8765/cb")
print(json.dumps(asdict(tokens), default=str, indent=2))
```

Pass the resulting dict to the connector via `config.oauth_tokens`
**or** set it as the env var `GDRIVE_OAUTH_TOKENS` (JSON-encoded).

### Token refresh

The connector does **not** auto-refresh tokens at runtime — that's the
orchestrator's job. Use
`chunkshop.oauth.proactive_refresh(tokens, provider=...)` to refresh
within the access-token's expiry window before passing the tokens to
the connector.

### Never log the tokens

`ConfigModel.__repr__` redacts `oauth_tokens` to `<redacted>`. The
connector class does the same. `OAuthTokens.__repr__` (from chunkshop
core) also redacts access/refresh tokens. Don't `print(cfg.model_dump())`.

## Configuration

```yaml
source:
  type: connector
  connector: gdrive
  config:
    folder_id: 0BabcXYZ             # OR `query` (one of the two is required)
    query: "name contains 'design'" # Drive query syntax (optional)
    scopes:
      - https://www.googleapis.com/auth/drive.readonly
    oauth_tokens: ${GDRIVE_OAUTH_TOKENS}  # optional — env fallback used if omitted
```

| Key             | Type                  | Required | Notes                                                       |
|-----------------|-----------------------|----------|-------------------------------------------------------------|
| `folder_id`     | string                | one of   | Drive folder ID. Must match `^[A-Za-z0-9_-]+$`.             |
| `query`         | string                | one of   | Drive `q=` syntax. AND'd with `folder_id` if both given.    |
| `scopes`        | list of strings       | no       | Defaults to `["drive.readonly"]`. Informational — not enforced. |
| `oauth_tokens`  | dict                  | no       | Serialised `OAuthTokens`. If omitted, reads `$GDRIVE_OAUTH_TOKENS`. |
| `drive_base_url`| string                | no       | Defaults to `https://www.googleapis.com/drive/v3`. Override for proxies. |

At least one of `folder_id` or `query` is required — `ConfigModel`
raises `ValueError` otherwise.

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is:

```json
{"page_token": "<drive-page-token>"}
```

* **First sync** (`empty_cursor() == {}`):
  1. Call `GET /drive/v3/changes/startPageToken` — seed the next-sync
     token.
  2. Paginate through `GET /drive/v3/files?q=...` and emit every
     matching text-shaped file.
  3. Every emitted `Document` carries `metadata["next_page_token"]`
     set to the start-page token; `cursor_from(doc)` returns
     `{"page_token": next_page_token}`.

* **Subsequent syncs**:
  1. Call `GET /drive/v3/changes?pageToken=<prior>&includeRemoved=false`
     — paginate through changes.
  2. For each non-removed change, look up the file via the change
     record's embedded `file` field and emit if it's text-shaped.
  3. `metadata["next_page_token"]` becomes the response's
     `newStartPageToken` — every doc in the batch carries the same
     value, so cursor merging converges regardless of iteration order.

### Prune support

Not supported in this tier. The Drive changes API does carry `removed:
true` entries for deleted files, but `PrunableSource` requires a
separate cursor surface that we haven't wired here. If you need
source-side deletion detection, run a periodic full resync.

## Rate limits

Drive's default per-project quota is 1,000 requests per 100 seconds
per user. Each file's body costs one round trip (`/export` or
`/files/{id}?alt=media`), so a 1,000-file folder costs roughly 1,000
requests. The connector does NOT batch — for very large folders you
want to be on the incremental path (`/changes`), which costs one
request per page of changes regardless of folder size.

## Testing

The connector ships with a hermetic mock at
`chunkshop_connectors.testing.mocks.gdrive.gdrive_mock`. It uses
`httpx.MockTransport` (in-process, no socket) so the autouse
loopback-only socket guard in the connectors test suite is satisfied
without any special config.

Drive API JSON shapes match the real Google reference responses
(`files.list`, `files.export`, `files.get` with `alt=media`,
`changes.list`, `changes.getStartPageToken`) — see
https://developers.google.com/drive/api/v3/reference/files for the
canonical schemas.
