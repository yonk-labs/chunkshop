# chunkshop connectors

`chunkshop-connectors` is the first-party plugin package that
registers RAGFlow- and Onyx-derived connectors against chunkshop's
`chunkshop.sources` entry-point group. Connectors are configured via
the generic `ConnectorSource` YAML type:

```yaml
source:
  type: connector
  connector: blob
  config:
    bucket: my-bucket
    prefix: docs/
```

When chunkshop loads a `ConnectorSource`, it dispatches via the
entry-point registry to the named factory, which validates `config`
against its `ConfigModel` and returns a `Source`.

## Tier model

Every connector belongs to one of two tiers:

- **verified** — full behavioural surface, hermetic mock under
  `chunkshop_connectors.testing.mocks.<name>`, integration test
  under `python/connectors/tests/test_<name>_connector.py`. Safe to
  use in production.
- **experimental** — name registered, factory returns a stub that
  raises `StubError` on `iter_documents`. Useful for discovering the
  set of *intended* connectors; do **not** rely on iteration behaviour.

Tier is readable at runtime via `chunkshop_connectors._tier.tier_of(cls)`.

See [`_status.md`](./_status.md) for the per-connector table and
[`FOLLOWUPS.md`](./FOLLOWUPS.md) for the SP-2 deferred-work list.

## Authoring a new connector

1. Create `chunkshop_connectors/<name>/connector.py` with a class
   decorated `@verified` (or `@experimental`) and an `iter_documents`
   method yielding `chunkshop.sources.base.Document`s.
2. Create `chunkshop_connectors/<name>/__init__.py` exporting
   `Connector`, `ConfigModel`, `factory`.
3. Add an entry-point line to `python/connectors/pyproject.toml`:
   `<name> = "chunkshop_connectors.<name>:factory"`.
4. Add a hermetic mock under
   `chunkshop_connectors/testing/mocks/<name>.py` and re-export
   the fixture from `tests/conftest.py`.
5. Add `python/connectors/tests/test_<name>_connector.py` covering:
   registry membership, tier marker, config validation
   (`extra='forbid'` typo case), and at-least-one-document yield
   against the mock.
6. Re-install the package (`uv pip install -e
   python/connectors`) so the new entry point is discoverable.
