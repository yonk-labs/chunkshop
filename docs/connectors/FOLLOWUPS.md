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

## Intentionally out-of-scope for SP-2 (this session)

These were on the SP-2 plan but were not pulled into this session.
Each is queued for its own follow-up.

### 1. Slack OAuth provider (Task 10 — remainder)

Google is done (`46b8517`), GitHub uses PAT (no OAuth needed for
verified-tier). **Slack** still needs `chunkshop_connectors.oauth.slack`
plus the hermetic mock — same pattern as Google.

Reference: chunkshop#22 (OAuth interfaces), SP-1 spec §4.3 (concrete
providers live in plugin, not core).

### 2. Per-provider OAuth mocks (Task 11) — slack

`oauth/google.py` has its mock (`tests/test_google_oauth_provider.py`).
The Slack equivalent is deferred with the Slack provider above.

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
