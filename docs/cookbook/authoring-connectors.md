# Authoring a connector plugin

chunkshop core ships **no** connectors. New source types register from a
separate plugin package via the `chunkshop.sources` entry-point group, and are
resolved at config-load time through a generic `type: connector` source block.
Adding a connector requires **no edit to chunkshop core**.

This page walks through building a minimal connector plugin end to end.

## How discovery works

A connector is registered as an entry point in the `chunkshop.sources` group.
The entry-point value is a **factory**: a callable with the signature

```python
factory(config: dict) -> Source
```

where the returned object satisfies the `Source` protocol (has a
`sync_mode` attribute and an `iter_documents()` method), and optionally
`IncrementalSource` / `PrunableSource` (see
[`incremental-sources.md`](incremental-sources.md)).

`chunkshop.sources.registry` discovers these lazily and caches them:

```python
from chunkshop.sources import registry

registry.available_connectors()       # -> sorted list of registered names
src = registry.load_connector("gdrive", {"folder_id": "abc"})  # -> Source
```

If the name isn't registered, `load_connector` raises
`registry.UnknownConnectorError` (a `KeyError` subclass) whose message lists
the installed connectors and points back to this guide.

## The `type: connector` config block

Users wire a connector into a chunkshop YAML through the generic
`ConnectorSource` config (`chunkshop.config.ConnectorSource`):

```yaml
source:
  type: connector          # the discriminator — routes to the registry
  connector: gdrive        # entry-point name; ^[a-z_][a-z0-9_]*$
  config:                  # opaque to core; YOUR plugin validates it
    folder_id: abc123
    recursive: true
  sync:                    # optional — informs the consumer's scheduler
    mode: cursor
    refresh_freq_seconds: 3600
    prune_freq_seconds: 86400
  raw_store:               # optional — stage original bytes (see below)
    type: local
    root: /var/lib/chunkshop/raw
```

Important behaviors:

- **`config` is an open dict.** Core does not validate its contents — that's
  the plugin's job (validate it with your own pydantic model in the factory).
- **The top-level keys are still `extra="forbid"`.** A typo at the
  `type`/`connector`/`config`/`sync`/`raw_store` level raises a validation
  error, not a silent ignore.
- **`connector` is regex-checked** (`^[a-z_][a-z0-9_]*$`) at config-load.

When `chunkshop.sources.load_source(cfg)` sees a `ConnectorSource`, it calls
`registry.load_connector(cfg.connector, cfg.config)` for you.

## A complete minimal plugin

Project layout for a plugin package `chunkshop-connectors-hello`:

```
chunkshop-connectors-hello/
├── pyproject.toml
└── src/
    └── chunkshop_connectors_hello/
        ├── __init__.py
        └── hello.py
```

### `pyproject.toml`

The entry-point group is the only wiring chunkshop needs:

```toml
[project]
name = "chunkshop-connectors-hello"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["chunkshop", "pydantic>=2.7"]

[project.entry-points."chunkshop.sources"]
hello = "chunkshop_connectors_hello.hello:factory"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

The entry-point name (`hello`) is what users put in `connector:`. The value
(`...hello:factory`) is the importable `module:callable` path to the factory.

### `src/chunkshop_connectors_hello/hello.py`

```python
from __future__ import annotations
from typing import Iterator

from pydantic import BaseModel
from chunkshop.sources.base import Document, SyncMode


class HelloConfig(BaseModel):
    """Plugin-side validation of the opaque `config` blob.

    extra='forbid' here gives YOUR users the same typo protection chunkshop
    gives at the top level.
    """
    model_config = {"extra": "forbid"}
    greeting: str = "hello"
    names: list[str] = ["world"]


class HelloSource:
    sync_mode = SyncMode.FULL_RESYNC

    def __init__(self, config: HelloConfig):
        self.config = config

    def iter_documents(self) -> Iterator[Document]:
        for name in self.config.names:
            yield Document(
                id=f"hello::{name}",
                content=f"{self.config.greeting}, {name}!",
                title=name,
                metadata={"name": name},
            )


def factory(config: dict) -> HelloSource:
    """The (config: dict) -> Source callable registered as the entry point."""
    return HelloSource(HelloConfig(**config))
```

That's a complete, installable connector. After `pip install -e .` of the
plugin, this YAML works with no change to chunkshop:

```yaml
source:
  type: connector
  connector: hello
  config:
    greeting: hi
    names: [alice, bob]
```

## Adding incremental sync

To support cursor-based sync, implement the three `IncrementalSource` methods
(see [`incremental-sources.md`](incremental-sources.md) for the full contract
and cursor-shape guidance). Set `sync_mode = SyncMode.CURSOR` and carry your
change token in `Document.fingerprint`:

```python
class HelloSource:
    sync_mode = SyncMode.CURSOR

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        for doc in self.iter_documents():
            if cursor.get(doc.id) != doc.fingerprint:
                yield doc

    def cursor_from(self, last_document: Document) -> dict:
        return {last_document.id: last_document.fingerprint}
```

To support deletion detection, also implement `PrunableSource`'s
`empty_prune_cursor()` and `iter_deleted_since(cursor)` (which returns source
IDs, not Documents).

## Optional: a `raw_store` for original bytes

If your connector fetches binary originals (PDFs, images) and you want them
staged so re-processing doesn't re-fetch, the consumer can configure a
`raw_store:` block. chunkshop provides the `RawStore` protocol
(`chunkshop.raw_store.RawStore`) and two backends — `local` and `s3` —
resolved by `load_raw_store(cfg)`:

```python
from chunkshop.raw_store import load_raw_store

store = load_raw_store(connector_cfg.raw_store)   # LocalRawStore or S3RawStore
ref = store.put(doc.id, raw_bytes, content_type="application/pdf",
                meta={"fingerprint": doc.fingerprint})
if store.exists(doc.id, fingerprint=doc.fingerprint):
    ...  # short-circuit: already staged at this fingerprint
```

The S3 backend requires the `[s3]` extra (`pip install chunkshop[s3]`).

## Testing your connector

`chunkshop.testing` ships drop-in assertions for `IncrementalSource`
implementations:

```python
from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit

def test_my_connector_cursor():
    src = HelloSource(HelloConfig(names=["x"]))
    assert_cursor_advances(src)           # cursor moves off empty after first sync
    assert_idempotent_on_re_emit(src)     # re-syncing from advanced cursor yields nothing
```

For OAuth-backed connectors, add the fixtures plugin to your conftest and use
the `mock_oauth_provider` fixture (a no-network `MockOAuthProvider` with
predictable, monotonically-increasing tokens):

```python
# conftest.py
pytest_plugins = ["chunkshop.testing.fixtures"]
```

```python
def test_auth(mock_oauth_provider):
    tokens = mock_oauth_provider.exchange_code("code", "https://cb")
    assert tokens.provider == "mock"
```

See `chunkshop.oauth` (`OAuthProvider`, `OAuthTokenStorage`,
`proactive_refresh`) for the OAuth contracts your connector can build on —
chunkshop never persists tokens; storage is the consumer's job.

## See also

- [`incremental-sources.md`](incremental-sources.md) — the sync protocols and
  cursor shapes in depth.
- `examples/sync_loop.py` — a copy-me consumer loop that drives connectors.
