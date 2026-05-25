# `chunkshop.testing` — connector test helpers

**Module**: `chunkshop.testing`
**Type**: Utility — reusable test helpers for `IncrementalSource` implementations
**Ship status**: verified
**Optional extra**: `pytest` (for the fixture import; the helpers themselves don't need it)
**Since**: SP-1 (`b1f218a` renamed `_merge_cursor` → public `merge_cursor`)

## Purpose

A tiny public surface so connector authors don't have to reinvent the
cursor-merge dance + cursor-advance assertions. chunkshop's own tests
import these; downstream plugin tests should too.

## Public API

```python
from chunkshop.testing import (
    merge_cursor,
    assert_cursor_advances,
    assert_idempotent_on_re_emit,
)

# pytest fixtures (opt-in via pytest_plugins)
from chunkshop.testing.fixtures import mock_oauth_provider   # fixture
```

### `merge_cursor(source, prev, docs) -> dict`

```python
def merge_cursor(
    source: IncrementalSource,
    prev: dict,
    docs: list,
) -> dict: ...
```

Build the next cursor the way a consumer must: start from `prev`, then
merge each emitted document's delta in iteration order:

```python
nxt = dict(prev)
for d in docs:
    nxt.update(source.cursor_from(d))
return nxt
```

Public helper — connector authors writing their own incremental-sync
tests should call this directly rather than reimplementing the merge.

### `assert_cursor_advances(source) -> None`

```python
def assert_cursor_advances(source: IncrementalSource) -> None: ...
```

Run a full cycle and assert the cursor moves off empty after
ingesting. Useful smoke test for any new `IncrementalSource`.

### `assert_idempotent_on_re_emit(source) -> None`

```python
def assert_idempotent_on_re_emit(source: IncrementalSource) -> None: ...
```

First sync yields docs; re-syncing from the advanced cursor yields
none. This is the canonical idempotence property — chunkshop's own
connector tests use it as a one-liner.

### `mock_oauth_provider` (pytest fixture)

```python
# in your conftest.py
pytest_plugins = ["chunkshop.testing.fixtures"]

# then in your test:
def test_my_connector(mock_oauth_provider):
    # mock_oauth_provider is a fresh MockOAuthProvider instance
    ...
```

## Behavior contract

1. **`merge_cursor` does not mutate `prev`** — it returns a new dict.
2. **The order of `docs` matters** for monotonic cursors (where
   `cursor_from` returns a single-key dict with the last-wins
   semantic). For map-style cursors (S3, HTTP), order doesn't matter
   because each delta updates a separate key.
3. **`assert_cursor_advances` requires `docs` to be non-empty** —
   "cursor advances on first sync" is the assertion. Sources with no
   initial data will fail this assertion (by design).
4. **`assert_idempotent_on_re_emit` requires `docs` on first sync and
   no `docs` on second sync** — re-emitting any document on the
   re-sync would mean the cursor wasn't really applied.

## Inputs / Outputs

- `merge_cursor`: `(source, prev_dict, list_of_documents) -> new_dict`.
- `assert_*` helpers: side-effect only (call `assert`).

## Errors

| Exception   | When |
|-------------|------|
| `AssertionError` | Cursor doesn't advance, or docs re-emit on the advanced cursor. |

## Example: testing a custom connector

```python
from chunkshop.testing import (
    merge_cursor,
    assert_cursor_advances,
    assert_idempotent_on_re_emit,
)
from chunkshop_connectors.testing.mocks.gdrive import FakeGDrive

def test_gdrive_sync(monkeypatch):
    # Bind a hermetic mock that backs the connector with two files
    src = FakeGDrive(files=[("a.md", "alpha"), ("b.md", "beta")]).build_connector()

    assert_cursor_advances(src)
    assert_idempotent_on_re_emit(src)

def test_explicit_merge():
    src = FakeGDrive(files=[("a.md", "x")]).build_connector()
    cursor = src.empty_cursor()
    docs = list(src.iter_changes_since(cursor))
    cursor = merge_cursor(src, cursor, docs)
    # cursor now reflects the first sync's pageToken
    assert "page_token" in cursor
```

## Example: testing OAuth refresh

```python
from chunkshop.oauth import proactive_refresh

def test_proactive_refresh(mock_oauth_provider):
    # mock_oauth_provider issues numbered tokens
    initial = mock_oauth_provider.exchange_code("code", "redirect")
    n_initial = initial.provider_extras["n"]

    refreshed = proactive_refresh(
        initial,
        provider=mock_oauth_provider,
        leeway_minutes=120,   # huge leeway → forces refresh
    )
    assert refreshed is not None
    assert refreshed.provider_extras["n"] > n_initial
```

## How it integrates with the pipeline

These helpers are test-only — they don't ship in the runtime path.
Their existence is to make it cheap for new connectors to verify the
two non-obvious properties of an `IncrementalSource`:

1. The cursor advances after a real sync.
2. The advanced cursor causes the next sync to emit nothing.

Most cursor bugs (the SP-1 pg_table boundary-row bug, the SP-1
merge_cursor naming bug) are caught by these two assertions.

## Tests proving the contract

- `tests/chunkshop/test_testing_helpers.py`:
  - `merge_cursor` is order-sensitive for monotonic cursors
  - `merge_cursor` is order-insensitive for map cursors
  - `merge_cursor` doesn't mutate `prev`
  - `assert_cursor_advances` fails on a non-advancing cursor
  - `assert_idempotent_on_re_emit` fails when re-emit happens

The helpers themselves are also used by every chunkshop and
chunkshop-connectors integration test that touches an
`IncrementalSource`.

## See also

- Reference: [`source-http`](source-http.md), [`source-github`](source-github.md), [`source-gdrive`](source-gdrive.md), [`source-pg-table`](source-pg-table.md), [`source-blob`](source-blob.md) — all use these helpers in their tests
- Reference: [`oauth-protocols`](oauth-protocols.md) — `MockOAuthProvider`
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md)
