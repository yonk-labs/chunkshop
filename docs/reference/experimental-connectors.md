# Experimental-tier connectors (stubs)

**Module**: `chunkshop_connectors.<name>` (one subpackage per connector)
**Type**: Source (experimental-tier connector — STUB)
**Ship status**: experimental (NOT IMPLEMENTED)
**Since**: 2026-05-25 (commit `e869aa7`)

## TL;DR

These 23 connectors are **registered names with stub bodies**. They
exist in the registry, they pass tier-marker tests, and a typo in
your YAML will fail at config-validation. But calling
`iter_documents()` on any of them raises `StubError` — there is no
real implementation behind them yet.

If you put one in a real YAML and try to ingest, you will get:

```
chunkshop_connectors._stub.StubError: connector 'notion' is registered
as experimental but not yet implemented. See docs/connectors/_status.md.
```

That's by design. The goal of the bulk-register pass (SP-2 Task 9) was
to lock in the *names* + the registry seam without committing to ports.
Each one becomes a verified-tier connector in a follow-up session.

For status updates, see [`docs/connectors/_status.md`](../connectors/_status.md).

## The full list

23 stubs registered in `python/connectors/pyproject.toml` under
`[project.entry-points."chunkshop.sources"]`. Each subpackage is a
1-file shim: `chunkshop_connectors/<name>/__init__.py` calls
`make_stub("<name>")`.

| Name        | Auth                       | Notes |
|-------------|----------------------------|-------|
| `notion`     | OAuth (notion)            | Real impl deferred. |
| `confluence` | OAuth or API token         | Real impl deferred. |
| `jira`       | OAuth or API token         | Real impl deferred. |
| `dropbox`    | OAuth                      | Real impl deferred. |
| `box`        | OAuth                      | Real impl deferred. |
| `gitlab`     | OAuth or PAT               | Real impl deferred. |
| `bitbucket`  | OAuth or app password      | Real impl deferred. |
| `gmail`      | OAuth (google)             | Real impl deferred. |
| `imap`       | basic / app-password       | Real impl deferred. |
| `discord`    | bot token                  | Real impl deferred. |
| `airtable`   | API key                    | Real impl deferred. |
| `asana`      | OAuth or PAT               | Real impl deferred. |
| `zendesk`    | API token                  | Real impl deferred. |
| `sharepoint` | OAuth (microsoft)          | Real impl deferred. |
| `teams`      | OAuth (microsoft)          | Real impl deferred. |
| `r2`         | access-key                 | Use `blob` with `endpoint_url` for now. |
| `gcs`        | access-key or OAuth        | Use `blob` with `endpoint_url` for now. |
| `oci`        | access-key                 | Use `blob` with `endpoint_url` for now. |
| `seafile`    | username / token           | Real impl deferred. |
| `webdav`     | basic                      | Real impl deferred. |
| `moodle`     | web service token          | Real impl deferred. |
| `dingtalk`   | OAuth (dingtalk)           | Real impl deferred. |
| `rest_api`   | varies                     | Generic JSON-paginated REST connector. |

## Stub contract

Every stub:

1. **Imports clean.** `from chunkshop_connectors.notion import factory`
   works. The `Connector` class is built by `make_stub(name)`.
2. **Is in the registry.** `chunkshop.sources.registry.available_connectors()`
   includes the name.
3. **Tier marker is `experimental`.** `tier_of(NotionConnector) ==
   "experimental"`.
4. **`factory(config)` accepts any dict.** Stubs do NOT validate config
   (no `ConfigModel`) — they have no contract yet.
5. **`sync_mode = SyncMode.FULL_RESYNC`** as a placeholder.
6. **`iter_documents()` raises `StubError`** (a `NotImplementedError`
   subclass) with a message pointing at `docs/connectors/_status.md`.

Implementation:

```python
# chunkshop_connectors/notion/__init__.py
from chunkshop_connectors._stub import make_stub
Connector, factory = make_stub("notion")
__all__ = ["Connector", "factory"]
```

The shared factory lives at `chunkshop_connectors._stub.make_stub`:

```python
def make_stub(name: str) -> tuple[type, Callable[[dict], Any]]:
    @experimental
    class StubConnector:
        sync_mode = SyncMode.FULL_RESYNC
        def __init__(self, config): self.config = config
        def iter_documents(self):
            raise StubError(
                f"connector {name!r} is registered as experimental "
                f"but not yet implemented. See docs/connectors/_status.md."
            )
    StubConnector.__name__ = f"{name.capitalize()}StubConnector"
    return StubConnector, lambda config: StubConnector(config)
```

## Best-effort config shape (informational only)

These shapes are educated guesses based on the upstream RAGFlow
configurations and the auth method each provider uses. **They have no
runtime contract.** When the real connector lands, its `ConfigModel`
will define what's actually accepted.

```yaml
# notion (illustrative — does not actually work)
source:
  type: connector
  connector: notion
  config:
    integration_token: ${NOTION_TOKEN}
    root_page_id: 0123abc
    recursive: true

# confluence
source:
  type: connector
  connector: confluence
  config:
    base_url: https://my-org.atlassian.net/wiki
    space_key: TEAM
    api_token: ${CONFLUENCE_TOKEN}
    email: alice@example.com

# jira
source:
  type: connector
  connector: jira
  config:
    base_url: https://my-org.atlassian.net
    jql: "project = ENG AND updated >= -30d"
    api_token: ${JIRA_TOKEN}

# dropbox / box / sharepoint — OAuth folder walkers
source:
  type: connector
  connector: dropbox
  config:
    folder_path: /Engineering Docs
    oauth_tokens: ${DROPBOX_OAUTH_TOKENS}

# gitlab / bitbucket — repo walkers
source:
  type: connector
  connector: gitlab
  config:
    project_id: 12345
    branch: main
    token: ${GITLAB_TOKEN}

# gmail
source:
  type: connector
  connector: gmail
  config:
    query: "from:noreply@github.com newer_than:7d"
    oauth_tokens: ${GMAIL_OAUTH_TOKENS}

# imap
source:
  type: connector
  connector: imap
  config:
    host: imap.example.com
    user: ingest@example.com
    password: ${IMAP_APP_PASSWORD}
    folder: INBOX

# discord (bot token)
source:
  type: connector
  connector: discord
  config:
    bot_token: ${DISCORD_BOT_TOKEN}
    channel_id: 0123456789

# airtable
source:
  type: connector
  connector: airtable
  config:
    base_id: appXXXX
    table: Records
    api_key: ${AIRTABLE_KEY}

# zendesk
source:
  type: connector
  connector: zendesk
  config:
    subdomain: mycompany
    email: ingest@mycompany.com
    api_token: ${ZENDESK_TOKEN}

# rest_api — generic JSON paginator
source:
  type: connector
  connector: rest_api
  config:
    base_url: https://api.example.com/v1
    endpoint: /documents
    auth_header: "Bearer ${API_TOKEN}"
    pagination: {style: page, param: page, start: 1}
```

Again: **none of these work today.** They illustrate the *shape* the
real implementations will likely take.

## Workarounds for the "use `blob` instead" connectors

The S3-compatible stubs (`r2`, `gcs`, `oci`) can be served by the
verified `blob` connector with an `endpoint_url`:

```yaml
# Cloudflare R2
source:
  type: connector
  connector: blob          # NOT r2
  config:
    bucket: my-bucket
    endpoint_url: https://<account_id>.r2.cloudflarestorage.com
    access_key: ${R2_ACCESS_KEY}
    secret_key: ${R2_SECRET_KEY}

# Google Cloud Storage (interop endpoint)
source:
  type: connector
  connector: blob          # NOT gcs
  config:
    bucket: my-bucket
    endpoint_url: https://storage.googleapis.com
    access_key: ${GCS_HMAC_KEY}
    secret_key: ${GCS_HMAC_SECRET}
```

See [`source-blob`](source-blob.md) for the full blob connector
reference.

## How to graduate a stub to verified

1. Implement `chunkshop_connectors/<name>/connector.py` with a class
   decorated `@verified` that satisfies the `Source` protocol (and
   optionally `IncrementalSource` / `PrunableSource`).
2. Replace the stub in `chunkshop_connectors/<name>/__init__.py`:
   ```python
   from chunkshop_connectors.<name>.connector import MyConnector as Connector
   class ConfigModel(BaseModel): ...
   def factory(config): return Connector(ConfigModel.model_validate(config).model_dump())
   ```
3. Add a hermetic mock under
   `chunkshop_connectors/testing/mocks/<name>.py`.
4. Add `python/connectors/tests/test_<name>_connector.py` covering at
   minimum: registry membership, tier marker, config validation
   (`extra='forbid'` typo case), at-least-one-document yield, cursor
   advance + idempotence (for `IncrementalSource`).
5. Update `docs/connectors/_status.md` Status column from `stub` to
   `implemented`.

The `gdrive`, `github`, `rss`, `blob` connectors are reference
implementations of this pattern.

## See also

- [`docs/connectors/_status.md`](../connectors/_status.md) — per-connector status table
- [`docs/connectors/FOLLOWUPS.md`](../connectors/FOLLOWUPS.md) — SP-2 deferred-work list
- [`docs/cookbook/authoring-connectors.md`](../cookbook/authoring-connectors.md) — how to write a connector
- Reference: [`source-github`](source-github.md), [`source-gdrive`](source-gdrive.md), [`source-blob`](source-blob.md), [`source-rss`](source-rss.md) — the four verified examples to model after
