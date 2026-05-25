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

## Intentionally out-of-scope for SP-2 (this session)

These were on the SP-2 plan but were not pulled into this session.
Each is queued for its own follow-up.

### 1. Verified OAuth-backed connectors (Task 10)

- `gdrive`, `github`, `slack` are listed in `_status.md` with
  `Status: planned` and **are not yet registered**. Implementing
  them requires per-provider OAuth modules in
  `chunkshop_connectors/oauth/{google,github,slack}.py`.
- Why deferred: the OAuth providers are a non-trivial design surface
  (token refresh, scope validation, refresh-token rotation, secure
  storage hooks). They warrant their own SP-2.1 plan rather than
  being rolled into the bulk-stub session.

Reference: chunkshop#22 (OAuth interfaces), SP-1 spec §4.3 (concrete
providers live in plugin, not core).

### 2. Per-provider OAuth mocks (Task 11)

Hermetic mocks for the OAuth flow itself (authorize → callback →
token exchange → refresh) belong in
`chunkshop_connectors/testing/mocks/oauth_<provider>.py`. Deferred
with Task 10.

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
