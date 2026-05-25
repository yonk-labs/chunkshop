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
| gdrive    | verified     | planned     | OAuth (google)             | Needs `oauth/google.py` provider (Task 10). |
| github    | verified     | planned     | OAuth (github) / PAT       | Needs `oauth/github.py` provider (Task 10). |
| slack     | verified     | planned     | OAuth (slack)              | Needs `oauth/slack.py` provider (Task 10). |
| notion    | experimental | stub        | OAuth (notion)             | Real impl deferred. |
| confluence| experimental | stub        | OAuth or API token         | Real impl deferred. |
| jira      | experimental | stub        | OAuth or API token         | Real impl deferred. |
| dropbox   | experimental | stub        | OAuth                      | Real impl deferred. |
| box       | experimental | stub        | OAuth                      | Real impl deferred. |
| gitlab    | experimental | stub        | OAuth or PAT               | Real impl deferred. |
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

## Re-syncing this table

When you implement an experimental stub, update the row's **Status**
to `implemented` and add a one-line note describing its sync mode +
fingerprint strategy. When you start a planned verified connector
(gdrive / github / slack), promote it from `planned` to `implemented`
and document the OAuth provider it relies on.

## Roadmap

- **Task 10 (deferred):** OAuth provider implementations in
  `chunkshop_connectors/oauth/{google,github,slack,microsoft,...}.py`
  and the three verified OAuth-backed connectors (gdrive / github /
  slack).
- **Task 11 (deferred):** Per-provider hermetic mocks for the OAuth
  flow (token refresh, scope validation, refresh-token rotation).
- **Behavioural lifts** of each experimental stub one at a time;
  each lift gets its own test file under
  `python/connectors/tests/test_<name>_connector.py` plus a mock
  under `chunkshop_connectors/testing/mocks/<name>.py`.
