# SP-2 Follow-ups

SP-2 (chunkshop-connectors bulk port) shipped:

- `_base/` infrastructure lift from RAGFlow (config, exceptions,
  file_types, interfaces, models, rate_limit, retry, runner —
  `utils.py` deferred per `_PROVENANCE.md`).
- `_adapt.py` (RAGFlow Document → chunkshop Document mapper).
- `_tier.py` (verified / experimental decorators).
- **verified `blob` connector** (S3-compatible, boto3, ETag
  fingerprint) + hermetic mock.
- **verified `rss` connector** (feedparser, GUID fingerprint) +
  hermetic mock.
- **23 experimental stubs** registered against
  `chunkshop.sources` so `available_connectors()` returns the full
  intended surface today; each stub raises a clear `StubError`
  pointing at this doc.
- Attribution preserved (NOTICE, THIRD-PARTY-LICENSES.md,
  `_PROVENANCE.md` recording the upstream RAGFlow SHA).
- Status + author docs (`docs/connectors/_status.md`,
  `docs/connectors/README.md`).
- Attribution CI guard (`tests/test_attribution.py`).

## Done in this session

- **verified `github` connector** (`74d51f3`) — PAT auth, cursor on
  branch HEAD SHA, `/compare` for incremental diffs, `StaleCursorError`
  on 422. Hermetic mock via `pytest_httpserver`.
- **`GoogleOAuthProvider`** (`46b8517`) — first concrete OAuth provider
  module under `chunkshop_connectors.oauth.google`. Hermetic tests
  cover authorize URL, code exchange, refresh, scope validation.
- **verified `gdrive` connector** (`0126bda`) — OAuth bearer auth,
  cursor via Drive v3 changes API (`page_token`), text-shaped MIME
  filter with UserWarning skips. Hermetic mock via `httpx.MockTransport`.
- **end-to-end user-expectation tests + demos** (this session) —
  `python/connectors/tests/test_e2e_user_expectations.py` (5 sections,
  15 tests covering gdrive / github / S3 / URL-depth / DB) plus six
  runnable demo scripts in `python/connectors/examples/`. Each demo
  verifies one user expectation against the live or mocked surface.
  `make_gdrive_mock()` factored out of the pytest fixture so demos
  can drive the Drive mock outside pytest.
- **`SlackOAuthProvider` + verified `slack` connector** (this session)
  — `chunkshop_connectors.oauth.slack.SlackOAuthProvider` codifies
  Slack OAuth v2 quirks (comma-separated scope lists, bot vs user
  tokens, `ok: false` error surfacing, rotating refresh tokens). The
  `slack` connector replaces the stub with a real Web-API walker that
  yields one Document per message, fans out thread replies via
  `conversations.replies`, and uses a per-channel `{channel_id: ts}`
  merge-delta cursor. Hermetic mock at
  `chunkshop_connectors.testing.mocks.slack.slack_mock` via
  `httpx.MockTransport`. 20 new tests (9 OAuth + 11 connector).

## Intentionally out-of-scope for SP-2 (this session)

These were on the SP-2 plan but were not pulled into this session.
Each is queued for its own follow-up.

### 1. Remaining OAuth providers (Task 10 — remainder)

Google and Slack are done. GitHub uses PAT (no OAuth needed for
verified-tier). Five OAuth providers still pending, ordered by how
many stub connectors depend on each:

- **Confluence** — used by the experimental `confluence` connector.
- **Dropbox** — used by the experimental `dropbox` connector.
- **Box** — used by the experimental `box` connector.
- **Gmail** — used by the experimental `gmail` connector (Google's
  OAuth provider can be reused; Gmail-specific is mostly about scopes
  + base URL).
- **Jira** — used by the experimental `jira` connector (Atlassian's
  OAuth is shared with Confluence).

Reference: chunkshop#22 (OAuth interfaces), SP-1 spec §4.3 (concrete
providers live in plugin, not core).

### 2. Per-provider OAuth mocks (Task 11) — remaining

Google and Slack have their hermetic mocks
(`tests/test_google_oauth_provider.py`,
`tests/test_slack_oauth_provider.py`). Each new OAuth provider lands
with its mock + tests in the same `MockTransport`-based pattern.

### 3. Behavioural lifts for experimental tier

Every experimental connector currently raises `StubError`. The
behavioural lift of each is a separate small unit of work:

- pick one stub,
- replace `make_stub("<name>")` with a real implementation (lifted
  or clean-room),
- add `tests/test_<name>_connector.py` + mock,
- promote `_status.md` to `Status: implemented`.

Recommended priority order based on the SP-2 plan's value-density
read: `notion`, `confluence`, `jira`, `rest_api`, then the
remaining 19 in any order.

### 4. `_base/utils.py` lift

Still stubbed (see `_PROVENANCE.md` §"Deferred lifts"). Pull in the
helpers each verified connector actually needs at lift time, not
the full 1.3kloc upstream module.

### 5. `r2` / `gcs` / `oci` as `blob` aliases

These three are registered as experimental stubs today but can be
served by the verified `blob` connector with the right
`endpoint_url`. Worth deciding whether to keep them as
distinct-name aliases (more discoverable) or fold them into
`blob`'s ConfigModel docs (less surface).
