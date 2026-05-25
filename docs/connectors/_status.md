# Connectors — Status

Snapshot of every `chunkshop_connectors.*` connector, its tier, and
its current implementation status.

**Tier legend**

- **verified** — behaviourally tested against hermetic per-provider
  mocks; cursor / fingerprint semantics exercised; ready for
  production use.
- **experimental** — registered name, importable, in
  `registry.available_connectors()`, but not yet behaviourally
  certified. Stub connectors raise `StubError` on `iter_documents()`
  with a clear "real impl pending" message.

**Status legend**

- **implemented** — full behavioural surface; tests against mock.
- **stub** — registered with `make_stub(name)`; raises `StubError`.
- **planned** — neither registered nor stubbed yet.

| Connector | Tier         | Status      | Auth                       | Notes |
|-----------|--------------|-------------|----------------------------|-------|
| blob      | verified     | implemented | access-key (boto3)         | S3 / R2 / GCS-interop / MinIO via `endpoint_url`. ETag → fingerprint. |
| rss       | verified     | implemented | none                       | feedparser-backed. GUID → fingerprint. |
| gdrive    | verified     | implemented | OAuth (google)             | httpx-backed Drive v3 walker. Cursor = `{page_token}` via `/changes` API. Text-shaped MIMEs + Google Docs (exported as text); others skipped with `UserWarning`. See `docs/connectors/gdrive.md`. |
| github    | verified     | implemented | PAT (classic or fine-grained) | httpx-backed REST walker. Cursor = `{after_commit_sha}`. See `docs/connectors/github.md`. |
| notion    | verified     | implemented | integration token          | httpx-backed v1 REST walker. Cursor = `{after_last_edited_time}` via database `last_edited_time` filter. Block-tree walked to plain text. See `docs/connectors/notion.md`. |
| dropbox   | verified     | implemented | OAuth bearer / PAT         | httpx-backed v2 REST walker. Cursor = Dropbox's own `{cursor}` via `/files/list_folder/continue`. Text-extension allow-list. See `docs/connectors/dropbox.md`. |
| gitlab    | verified     | implemented | PAT / project token        | httpx-backed v4 REST walker. Cursor = `{after_commit_sha}` via `/repository/compare`. Mirrors the github connector. See `docs/connectors/gitlab.md`. |
| slack     | verified     | planned     | OAuth (slack)              | Needs `oauth/slack.py` provider (Task 10). |
| confluence| experimental | stub        | OAuth or API token         | Real impl deferred. |
| jira      | experimental | stub        | OAuth or API token         | Real impl deferred. |
| box       | experimental | stub        | OAuth                      | Real impl deferred. |
| bitbucket | experimental | stub        | OAuth or app password      | Real impl deferred. |
| gmail     | experimental | stub        | OAuth (google)             | Real impl deferred. |
| imap      | experimental | stub        | basic / app-password       | Real impl deferred. |
| discord   | experimental | stub        | bot token                  | Real impl deferred. |
| airtable  | experimental | stub        | API key                    | Real impl deferred. |
| asana     | experimental | stub        | OAuth or PAT               | Real impl deferred. |
| zendesk   | experimental | stub        | API token                  | Real impl deferred. |
| sharepoint| experimental | stub        | OAuth (microsoft)          | Real impl deferred. |
| teams     | experimental | stub        | OAuth (microsoft)          | Real impl deferred. |
| r2        | experimental | stub        | access-key                 | Cloudflare R2; can be served by `blob` with `endpoint_url`. |
| gcs       | experimental | stub        | access-key or OAuth        | Google Cloud Storage interop endpoint can be served by `blob`. |
| oci       | experimental | stub        | access-key                 | Oracle Cloud Storage; can be served by `blob`. |
| seafile   | experimental | stub        | username / token           | Real impl deferred. |
| webdav    | experimental | stub        | basic                      | Real impl deferred. |
| moodle    | experimental | stub        | web service token          | Real impl deferred. |
| dingtalk  | experimental | stub        | OAuth (dingtalk)           | Real impl deferred. |
| rest_api  | experimental | stub        | varies                     | Generic JSON-paginated REST connector. |

## OAuth providers

OAuth providers live under `chunkshop_connectors.oauth.*` and
implement `chunkshop.oauth.OAuthProvider` (Protocol). They wrap the
per-vendor quirks (Google's `access_type=offline` requirement,
Slack's v2 token endpoint, etc.) so connectors stay vendor-agnostic.

| Provider | Status      | Used by | Notes |
|----------|-------------|---------|-------|
| google   | implemented | gdrive  | `httpx`-backed. Codifies `access_type=offline` + `prompt=consent` on consent URL; preserves prior refresh_token if Google omits one on refresh. |
| slack    | planned     | slack   | Needs `oauth/slack.py` provider before slack connector lands. |
| github   | n/a         | github  | github connector uses PAT auth only — no OAuth provider needed. |

## Re-syncing this table

When you implement an experimental stub, update the row's **Status**
to `implemented` and add a one-line note describing its sync mode +
fingerprint strategy. When you start a planned verified connector
(gdrive / github / slack), promote it from `planned` to `implemented`
and document the OAuth provider it relies on.

## Roadmap

- **Task 10 (partially done):** OAuth provider implementations in
  `chunkshop_connectors/oauth/`. `google.py` shipped alongside the
  `gdrive` verified connector. `slack.py` + `microsoft.py` still
  deferred. `github.py` is intentionally out of scope — the
  `github` connector uses PAT auth.
- **Task 11 (deferred):** Per-provider hermetic mocks for the OAuth
  flow (token refresh, scope validation, refresh-token rotation).
- **Behavioural lifts** of each experimental stub one at a time;
  each lift gets its own test file under
  `python/connectors/tests/test_<name>_connector.py` plus a mock
  under `chunkshop_connectors/testing/mocks/<name>.py`.
