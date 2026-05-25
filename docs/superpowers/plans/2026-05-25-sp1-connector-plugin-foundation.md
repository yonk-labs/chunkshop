# SP-1 Connector Plugin Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the dependency-light primitives + entry-point seam that turn chunkshop sources into a plugin architecture: sync Protocols, a connector registry, OAuth interfaces, a RawStore, test helpers, and a copy-me example sync loop.

**Architecture:** All production code lands in core `chunkshop` and *runs nothing on its own* — these are contracts + pure helpers a consumer (or `chunkshop_api`) drives. New source types register via the `chunkshop.sources` entry-point group through a generic `type: connector` config; no core edit is needed to add a connector. The Protocols are proven against the existing `s3` and `pg_table` loaders before any connector is built on them.

**Tech Stack:** Python 3.11+, pydantic v2 (`extra="forbid"`), `importlib.metadata` entry points, pytest / pytest-asyncio, boto3 (optional `[s3]`), psycopg (existing).

**Spec:** `docs/superpowers/specs/2026-05-25-chunkshop-connector-plugin-foundation-design.md`

**Working dir:** all paths relative to `python/`. Run tests with `uv run pytest`. The git repo root is the parent of `python/`.

---

## File structure

| File | Responsibility | New/Mod |
|---|---|---|
| `src/chunkshop/sources/base.py` | `Document` (+`fingerprint`), `Source` (+`sync_mode`), `SyncMode`, `IncrementalSource`, `PrunableSource`, `StaleCursorError` | Mod |
| `src/chunkshop/sources/registry.py` | entry-point discovery, `UnknownConnectorError`, `load_connector` | New |
| `src/chunkshop/sources/__init__.py` | add `connector` branch to `load_source` | Mod |
| `src/chunkshop/config.py` | `SyncSettings`, `ConnectorSource`, `RawStoreConfig` union; add to `SourceConfig` | Mod |
| `src/chunkshop/raw_store/base.py` | `RawStore` Protocol | New |
| `src/chunkshop/raw_store/local.py` | filesystem backend | New |
| `src/chunkshop/raw_store/s3.py` | S3 backend (`[s3]`) | New |
| `src/chunkshop/raw_store/__init__.py` | `load_raw_store` factory | New |
| `src/chunkshop/oauth/tokens.py` | `OAuthTokens` dataclass | New |
| `src/chunkshop/oauth/base.py` | `OAuthProvider` Protocol | New |
| `src/chunkshop/oauth/storage.py` | `OAuthTokenStorage` Protocol | New |
| `src/chunkshop/oauth/refresh.py` | `proactive_refresh` | New |
| `src/chunkshop/oauth/_mock.py` | `MockOAuthProvider` | New |
| `src/chunkshop/oauth/__init__.py` | re-exports | New |
| `src/chunkshop/testing/__init__.py` | `assert_cursor_advances`, `assert_idempotent_on_re_emit` | New |
| `src/chunkshop/testing/fixtures.py` | `mock_oauth_provider` pytest fixture | New |
| `src/chunkshop/sources/s3.py` | implement `IncrementalSource` (ETag cursor) | Mod |
| `src/chunkshop/sources/pg_table.py` | implement `IncrementalSource` (`updated_at` cursor) | Mod |
| `examples/sync_loop.py` | SP-1b copy-me semaphore-bounded loop | New |
| `docs/cookbook/incremental-sources.md` | Protocol usage docs | New |
| `docs/cookbook/authoring-connectors.md` | plugin-authoring guide | New |
| `pyproject.toml` | document `chunkshop.sources` entry-point group | Mod |
| tests under `tests/chunkshop/` | one test module per task | New |

---

## Task 1: SyncMode enum + Document.fingerprint + Source.sync_mode + StaleCursorError

**Files:**
- Modify: `src/chunkshop/sources/base.py`
- Test: `tests/chunkshop/test_sync_primitives.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_sync_primitives.py
from chunkshop.sources.base import Document, SyncMode, StaleCursorError


def test_syncmode_values():
    assert SyncMode.FULL_RESYNC == "full_resync"
    assert SyncMode.CURSOR == "cursor"
    assert SyncMode.FINGERPRINT == "fingerprint"


def test_document_fingerprint_optional_default_none():
    d = Document(id="a", content="x")
    assert d.fingerprint is None
    d2 = Document(id="a", content="x", fingerprint="etag-123")
    assert d2.fingerprint == "etag-123"


def test_stale_cursor_error_is_exception():
    with __import__("pytest").raises(StaleCursorError):
        raise StaleCursorError("cursor expired")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_sync_primitives.py -v`
Expected: FAIL — `ImportError: cannot import name 'SyncMode'`.

- [ ] **Step 3: Implement in `base.py`**

```python
# src/chunkshop/sources/base.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Optional, Protocol, runtime_checkable


class SyncMode(str, Enum):
    """How a Source detects changes between runs."""
    FULL_RESYNC = "full_resync"
    CURSOR = "cursor"
    FINGERPRINT = "fingerprint"


class StaleCursorError(Exception):
    """Raised by iter_changes_since when a cursor is too old to honor.

    Consumers should treat this as a signal to fall back to a full resync
    (call iter_documents / iter_changes_since(empty_cursor())).
    """


@dataclass(frozen=True)
class Document:
    id: str
    content: str
    title: Optional[str] = None
    metadata: Optional[dict] = None
    fingerprint: Optional[str] = None


class Source(Protocol):
    sync_mode: SyncMode = SyncMode.FULL_RESYNC

    def iter_documents(self) -> Iterator[Document]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_sync_primitives.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify nothing else broke** (Document is widely constructed)

Run: `uv run pytest -q`
Expected: same pass/skip counts as before this task (the new `fingerprint` field has a default, so existing construction sites are unaffected).

- [ ] **Step 6: Commit**

```bash
git -C .. add python/src/chunkshop/sources/base.py python/tests/chunkshop/test_sync_primitives.py
git -C .. commit -m "feat(sources): add SyncMode, Document.fingerprint, StaleCursorError"
```

---

## Task 2: IncrementalSource + PrunableSource Protocols

**Files:**
- Modify: `src/chunkshop/sources/base.py`
- Test: `tests/chunkshop/test_sync_protocols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_sync_protocols.py
from chunkshop.sources.base import (
    Document, IncrementalSource, PrunableSource, SyncMode,
)


class _Inc:
    sync_mode = SyncMode.CURSOR

    def empty_cursor(self): return {}

    def iter_changes_since(self, cursor):
        if not cursor:
            yield Document(id="a", content="x")

    def cursor_from(self, last_document): return {"after": last_document.id}


class _Prune:
    def empty_prune_cursor(self): return {}
    def iter_deleted_since(self, cursor): return iter([])


def test_incremental_runtime_checkable():
    assert isinstance(_Inc(), IncrementalSource)
    assert not isinstance(object(), IncrementalSource)


def test_prunable_runtime_checkable():
    assert isinstance(_Prune(), PrunableSource)
    assert not isinstance(object(), PrunableSource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_sync_protocols.py -v`
Expected: FAIL — `ImportError: cannot import name 'IncrementalSource'`.

- [ ] **Step 3: Append Protocols to `base.py`**

```python
@runtime_checkable
class IncrementalSource(Protocol):
    """Sources that support cursor-based incremental sync implement this.

    The cursor shape is source-specific (ETag map for S3, timestamp for DB
    tables, HEAD-SHA for git, opaque page token for APIs). Consumers treat it
    as an opaque dict and persist it between calls. chunkshop never stores it.
    """
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...


@runtime_checkable
class PrunableSource(Protocol):
    """Sources that can enumerate source-side deletions implement this.

    Typically called at a lower cadence than iter_changes_since because prune
    detection often requires walking the full source manifest. Returns
    source-IDs (the Document.id field), not Document objects.
    """
    def empty_prune_cursor(self) -> dict: ...
    def iter_deleted_since(self, cursor: dict) -> Iterable[str]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_sync_protocols.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git -C .. add python/src/chunkshop/sources/base.py python/tests/chunkshop/test_sync_protocols.py
git -C .. commit -m "feat(sources): add IncrementalSource and PrunableSource protocols"
```

---

## Task 3: SyncSettings config model

**Files:**
- Modify: `src/chunkshop/config.py` (add near the Source classes, before `SourceConfig`)
- Test: `tests/chunkshop/test_sync_settings_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_sync_settings_config.py
import pytest
from pydantic import ValidationError
from chunkshop.config import SyncSettings


def test_defaults():
    s = SyncSettings()
    assert s.mode == "full_resync"
    assert s.refresh_freq_seconds is None
    assert s.prune_freq_seconds is None


def test_mode_validated():
    s = SyncSettings(mode="cursor", refresh_freq_seconds=3600, prune_freq_seconds=86400)
    assert s.mode == "cursor"
    assert s.refresh_freq_seconds == 3600


def test_rejects_unknown_field():
    with pytest.raises(ValidationError):
        SyncSettings(mode="cursor", bogus=1)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_sync_settings_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'SyncSettings'`.

- [ ] **Step 3: Implement (add to `config.py` just above the `SourceConfig = Annotated[...]` block)**

```python
class SyncSettings(_Base):
    """Declares how a connector source detects changes. Consumer-driven —
    chunkshop does not schedule; these values inform the consumer's orchestrator."""
    mode: Literal["full_resync", "cursor", "fingerprint"] = "full_resync"
    refresh_freq_seconds: Optional[int] = Field(default=None, ge=1)
    prune_freq_seconds: Optional[int] = Field(default=None, ge=1)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_sync_settings_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git -C .. add python/src/chunkshop/config.py python/tests/chunkshop/test_sync_settings_config.py
git -C .. commit -m "feat(config): add SyncSettings model"
```

---

## Task 4: Connector registry (entry-point discovery)

**Files:**
- Create: `src/chunkshop/sources/registry.py`
- Test: `tests/chunkshop/test_connector_registry.py`

- [ ] **Step 1: Write the failing test** (uses a monkeypatched entry-point list — no real plugin needed)

```python
# tests/chunkshop/test_connector_registry.py
import pytest
from chunkshop.sources import registry
from chunkshop.sources.base import Document


class _DummyConnector:
    sync_mode = "full_resync"
    def __init__(self, config): self.config = config
    def iter_documents(self):
        yield Document(id=self.config.get("id", "d1"), content="hello")


def _dummy_factory(config: dict):
    return _DummyConnector(config)


class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj
    def load(self):
        return self._obj


@pytest.fixture
def fake_eps(monkeypatch):
    eps = [_FakeEP("dummy", _dummy_factory)]
    monkeypatch.setattr(registry, "_iter_entry_points", lambda: eps)
    registry.clear_cache()
    yield
    registry.clear_cache()


def test_load_connector_resolves_factory(fake_eps):
    src = registry.load_connector("dummy", {"id": "x"})
    docs = list(src.iter_documents())
    assert docs[0].id == "x"


def test_unknown_connector_lists_installed(fake_eps):
    with pytest.raises(registry.UnknownConnectorError) as ei:
        registry.load_connector("nope", {})
    assert "dummy" in str(ei.value)


def test_available_connectors(fake_eps):
    assert registry.available_connectors() == ["dummy"]
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_connector_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.sources.registry`.

- [ ] **Step 3: Implement `registry.py`**

```python
# src/chunkshop/sources/registry.py
"""Entry-point discovery for connector sources.

Connectors register against the ``chunkshop.sources`` entry-point group:

    [project.entry-points."chunkshop.sources"]
    gdrive = "chunkshop_connectors.gdrive:factory"

A factory is a callable ``(config: dict) -> Source``. Discovery is lazy and
cached; adding a connector requires NO edit to chunkshop core.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Callable

ENTRY_POINT_GROUP = "chunkshop.sources"

_cache: dict[str, Callable] | None = None


class UnknownConnectorError(KeyError):
    """Requested connector name is not registered by any installed plugin."""


def _iter_entry_points():
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _registry() -> dict[str, Callable]:
    global _cache
    if _cache is None:
        _cache = {ep.name: ep.load() for ep in _iter_entry_points()}
    return _cache


def clear_cache() -> None:
    global _cache
    _cache = None


def available_connectors() -> list[str]:
    return sorted(_registry().keys())


def load_connector(name: str, config: dict):
    reg = _registry()
    try:
        factory = reg[name]
    except KeyError:
        installed = ", ".join(available_connectors()) or "(none installed)"
        raise UnknownConnectorError(
            f"unknown connector {name!r}; install a plugin that registers it. "
            f"Installed connectors: {installed}. "
            f"See docs/cookbook/authoring-connectors.md."
        ) from None
    return factory(config)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_connector_registry.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git -C .. add python/src/chunkshop/sources/registry.py python/tests/chunkshop/test_connector_registry.py
git -C .. commit -m "feat(sources): add connector entry-point registry"
```

---

## Task 5: ConnectorSource config + union member

**Files:**
- Modify: `src/chunkshop/config.py`
- Test: `tests/chunkshop/test_connector_source_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_connector_source_config.py
import pytest
from pydantic import TypeAdapter, ValidationError
from chunkshop.config import SourceConfig, ConnectorSource


def _parse(d):
    return TypeAdapter(SourceConfig).validate_python(d)


def test_connector_source_parses_via_union():
    cfg = _parse({"type": "connector", "connector": "gdrive",
                  "config": {"folder_id": "abc"},
                  "sync": {"mode": "cursor", "refresh_freq_seconds": 3600}})
    assert isinstance(cfg, ConnectorSource)
    assert cfg.connector == "gdrive"
    assert cfg.config["folder_id"] == "abc"
    assert cfg.sync.mode == "cursor"


def test_connector_config_blob_is_open():
    # config is intentionally an open dict — the plugin validates it.
    cfg = _parse({"type": "connector", "connector": "x", "config": {"any": 1, "thing": [2]}})
    assert cfg.config["thing"] == [2]


def test_connector_top_level_still_forbids_extra():
    with pytest.raises(ValidationError):
        _parse({"type": "connector", "connector": "x", "bogus_top_level": 1})
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_connector_source_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConnectorSource'`.

- [ ] **Step 3: Implement — add the class and extend the union in `config.py`**

```python
class ConnectorSource(_Base):
    """Generic plugin-source kind. Resolved at load time against the
    ``chunkshop.sources`` entry-point registry. The ``config`` dict is opaque
    to core — the plugin validates it. ``extra='forbid'`` still applies to the
    top-level keys here (type/connector/config/sync/raw_store)."""
    type: Literal["connector"]
    connector: str
    config: dict = Field(default_factory=dict)
    sync: Optional[SyncSettings] = None
    raw_store: Optional["RawStoreConfig"] = None

    @field_validator("connector")
    @classmethod
    def _safe_name(cls, v):
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"connector name must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v
```

Then extend the union (note: `RawStoreConfig` is defined in Task 9; add `ConnectorSource` to the union now and add a forward-ref rebuild at the bottom of the module after `RawStoreConfig` exists):

```python
SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, SessionStagingSource, PgTableSource, SqliteTableSource,
          MariaDbTableSource, ClickhouseTableSource, HttpSource, S3Source, InlineSource,
          ConnectorSource],
    Field(discriminator="type"),
]
```

For this task, temporarily type `raw_store` as `Optional[dict]` so the module imports before Task 9 lands `RawStoreConfig`. Task 9 replaces it with `Optional["RawStoreConfig"]` and adds `ConnectorSource.model_rebuild()`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_connector_source_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite still green**

Run: `uv run pytest -q`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git -C .. add python/src/chunkshop/config.py python/tests/chunkshop/test_connector_source_config.py
git -C .. commit -m "feat(config): add ConnectorSource union member"
```

---

## Task 6: Wire `connector` branch into load_source

**Files:**
- Modify: `src/chunkshop/sources/__init__.py`
- Test: `tests/chunkshop/test_load_source_connector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_load_source_connector.py
import pytest
from chunkshop.config import ConnectorSource
from chunkshop.sources import load_source
from chunkshop.sources import registry
from chunkshop.sources.base import Document


class _Dummy:
    def __init__(self, config): self.config = config
    def iter_documents(self): yield Document(id="z", content="zz")


@pytest.fixture
def fake_eps(monkeypatch):
    class _EP:
        name = "dummy"
        def load(self): return lambda config: _Dummy(config)
    monkeypatch.setattr(registry, "_iter_entry_points", lambda: [_EP()])
    registry.clear_cache()
    yield
    registry.clear_cache()


def test_load_source_resolves_connector(fake_eps):
    cfg = ConnectorSource(type="connector", connector="dummy", config={"k": 1})
    src = load_source(cfg)
    assert list(src.iter_documents())[0].id == "z"


def test_load_source_unknown_connector_raises(fake_eps):
    cfg = ConnectorSource(type="connector", connector="missing", config={})
    with pytest.raises(registry.UnknownConnectorError):
        load_source(cfg)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_load_source_connector.py -v`
Expected: FAIL — `ValueError: unknown source type: ConnectorSource`.

- [ ] **Step 3: Implement — add import + branch in `sources/__init__.py`**

Add to the config imports block: `ConnectorSource as ConnectorCfg,`. Add this branch immediately before the `InlineCfg` branch in `load_source`:

```python
    if isinstance(cfg, ConnectorCfg):
        from chunkshop.sources.registry import load_connector
        return load_connector(cfg.connector, cfg.config)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_load_source_connector.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git -C .. add python/src/chunkshop/sources/__init__.py python/tests/chunkshop/test_load_source_connector.py
git -C .. commit -m "feat(sources): resolve type=connector via registry in load_source"
```

---

## Task 7: RawStore Protocol

**Files:**
- Create: `src/chunkshop/raw_store/base.py`
- Test: `tests/chunkshop/test_raw_store_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_raw_store_protocol.py
from chunkshop.raw_store.base import RawStore


class _Impl:
    def put(self, doc_id, data, *, content_type, meta=None): return "ref"
    def get(self, ref): return b""
    def exists(self, doc_id, fingerprint=None): return False
    def delete(self, doc_id): ...


def test_runtime_checkable():
    assert isinstance(_Impl(), RawStore)
    assert not isinstance(object(), RawStore)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_raw_store_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.raw_store`.

- [ ] **Step 3: Implement `raw_store/base.py`**

```python
# src/chunkshop/raw_store/base.py
"""RawStore: pluggable storage for raw source artifacts (the original bytes a
connector or upload fetched), parallel to chunkshop's vector Sink.

Opt-in: connectors still just yield Documents. When a `raw_store:` block is
configured, the connector/upload path stages bytes here so re-processing
doesn't require re-fetching, deltas can short-circuit via exists(), and the
original can be served/audited.
"""
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class RawStore(Protocol):
    def put(self, doc_id: str, data: bytes, *, content_type: str,
            meta: Optional[dict] = None) -> str:
        """Store raw bytes for doc_id. Returns an opaque ref usable by get()."""
        ...

    def get(self, ref: str) -> bytes: ...

    def exists(self, doc_id: str, fingerprint: Optional[str] = None) -> bool:
        """True if doc_id is stored; if fingerprint is given, True only when the
        stored artifact matches that fingerprint (enables delta short-circuit)."""
        ...

    def delete(self, doc_id: str) -> None: ...
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_raw_store_protocol.py -v` → PASS.

```bash
git -C .. add python/src/chunkshop/raw_store/base.py python/tests/chunkshop/test_raw_store_protocol.py
git -C .. commit -m "feat(raw_store): add RawStore protocol"
```

---

## Task 8: Local filesystem RawStore backend

**Files:**
- Create: `src/chunkshop/raw_store/local.py`
- Test: `tests/chunkshop/test_raw_store_local.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_raw_store_local.py
import pytest
from chunkshop.raw_store.local import LocalRawStore


def test_put_get_roundtrip(tmp_path):
    store = LocalRawStore(root=str(tmp_path))
    ref = store.put("doc::1", b"hello", content_type="text/plain", meta={"fingerprint": "fp1"})
    assert store.get(ref) == b"hello"


def test_exists_with_and_without_fingerprint(tmp_path):
    store = LocalRawStore(root=str(tmp_path))
    store.put("doc::1", b"hello", content_type="text/plain", meta={"fingerprint": "fp1"})
    assert store.exists("doc::1") is True
    assert store.exists("doc::1", fingerprint="fp1") is True
    assert store.exists("doc::1", fingerprint="other") is False
    assert store.exists("missing") is False


def test_delete(tmp_path):
    store = LocalRawStore(root=str(tmp_path))
    store.put("doc::1", b"x", content_type="text/plain")
    store.delete("doc::1")
    assert store.exists("doc::1") is False


def test_doc_id_with_path_separators_is_safe(tmp_path):
    store = LocalRawStore(root=str(tmp_path))
    # ids like "s3://bucket/key" must not escape root
    ref = store.put("s3://b/k/../../etc", b"x", content_type="text/plain")
    assert store.get(ref) == b"x"
    assert store.exists("s3://b/k/../../etc")
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_raw_store_local.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `local.py`** (hash the doc_id so arbitrary ids — including `s3://…` and `../` — can never escape root)

```python
# src/chunkshop/raw_store/local.py
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Optional


class LocalRawStore:
    """Filesystem RawStore. Layout: <root>/<sha256(doc_id)>/{blob,meta.json}.

    doc_id is hashed so arbitrary ids (s3://…, paths with ../) cannot traverse
    outside root. The original doc_id is recorded in meta.json.
    """

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, doc_id: str) -> Path:
        h = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()
        return self.root / h

    def put(self, doc_id: str, data: bytes, *, content_type: str,
            meta: Optional[dict] = None) -> str:
        d = self._dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "blob").write_bytes(data)
        record = {"doc_id": doc_id, "content_type": content_type, **(meta or {})}
        (d / "meta.json").write_text(json.dumps(record))
        return str(d / "blob")

    def get(self, ref: str) -> bytes:
        return Path(ref).read_bytes()

    def exists(self, doc_id: str, fingerprint: Optional[str] = None) -> bool:
        d = self._dir(doc_id)
        if not (d / "blob").exists():
            return False
        if fingerprint is None:
            return True
        try:
            meta = json.loads((d / "meta.json").read_text())
        except FileNotFoundError:
            return False
        return meta.get("fingerprint") == fingerprint

    def delete(self, doc_id: str) -> None:
        d = self._dir(doc_id)
        for f in ("blob", "meta.json"):
            (d / f).unlink(missing_ok=True)
        if d.exists():
            try:
                d.rmdir()
            except OSError:
                pass
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_raw_store_local.py -v` → PASS (4 tests).

```bash
git -C .. add python/src/chunkshop/raw_store/local.py python/tests/chunkshop/test_raw_store_local.py
git -C .. commit -m "feat(raw_store): add LocalRawStore filesystem backend"
```

---

## Task 9: RawStoreConfig union + load_raw_store factory

**Files:**
- Modify: `src/chunkshop/config.py` (add `RawStoreConfig`, finalize `ConnectorSource.raw_store` type)
- Create: `src/chunkshop/raw_store/__init__.py`
- Test: `tests/chunkshop/test_raw_store_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_raw_store_factory.py
from chunkshop.config import LocalRawStoreConfig, RawStoreConfig, ConnectorSource
from chunkshop.raw_store import load_raw_store
from chunkshop.raw_store.local import LocalRawStore
from pydantic import TypeAdapter


def test_local_factory(tmp_path):
    cfg = LocalRawStoreConfig(type="local", root=str(tmp_path))
    store = load_raw_store(cfg)
    assert isinstance(store, LocalRawStore)


def test_connector_source_accepts_raw_store_block(tmp_path):
    src = ConnectorSource(type="connector", connector="gdrive",
                          raw_store={"type": "local", "root": str(tmp_path)})
    assert src.raw_store.type == "local"


def test_raw_store_union_discriminates(tmp_path):
    cfg = TypeAdapter(RawStoreConfig).validate_python({"type": "local", "root": str(tmp_path)})
    assert isinstance(cfg, LocalRawStoreConfig)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_raw_store_factory.py -v`
Expected: FAIL — `ImportError: cannot import name 'LocalRawStoreConfig'`.

- [ ] **Step 3: Implement config models** (add to `config.py`, after the Source classes; place before `ConnectorSource` or rebuild model after)

```python
class LocalRawStoreConfig(_Base):
    type: Literal["local"]
    root: str


class S3RawStoreConfig(_Base):
    type: Literal["s3"]
    bucket: str
    prefix: str = ""
    endpoint_url: Optional[str] = None


RawStoreConfig = Annotated[
    Union[LocalRawStoreConfig, S3RawStoreConfig],
    Field(discriminator="type"),
]
```

Now change `ConnectorSource.raw_store` from the temporary `Optional[dict]` to `Optional[RawStoreConfig]`. Because `ConnectorSource` is defined before `RawStoreConfig` in module order, either (a) move the RawStore config classes above `ConnectorSource`, or (b) keep the forward ref `Optional["RawStoreConfig"]` and add at the very bottom of the module:

```python
ConnectorSource.model_rebuild()
```

Pick (a) if it keeps the file readable; (b) is the low-churn option.

- [ ] **Step 4: Implement the factory `raw_store/__init__.py`**

```python
# src/chunkshop/raw_store/__init__.py
"""RawStore factory — dispatch on the config discriminator, mirroring load_sink."""
from chunkshop.raw_store.base import RawStore
from chunkshop.raw_store.local import LocalRawStore


def load_raw_store(cfg) -> RawStore:
    if cfg.type == "local":
        return LocalRawStore(root=cfg.root)
    if cfg.type == "s3":
        from chunkshop.raw_store.s3 import S3RawStore
        return S3RawStore(bucket=cfg.bucket, prefix=cfg.prefix, endpoint_url=cfg.endpoint_url)
    raise ValueError(f"unknown raw_store type: {cfg.type!r}")


__all__ = ["RawStore", "LocalRawStore", "load_raw_store"]
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_raw_store_factory.py -v` and `uv run pytest -q`
Expected: new tests PASS; full suite no new failures.

- [ ] **Step 6: Commit**

```bash
git -C .. add python/src/chunkshop/config.py python/src/chunkshop/raw_store/__init__.py python/tests/chunkshop/test_raw_store_factory.py
git -C .. commit -m "feat(raw_store): add RawStoreConfig union and load_raw_store factory"
```

---

## Task 10: S3 RawStore backend

**Files:**
- Create: `src/chunkshop/raw_store/s3.py`
- Test: `tests/chunkshop/test_raw_store_s3.py` (mocked boto3; no network)

- [ ] **Step 1: Write the failing test** (mock the boto3 client so no creds/network needed)

```python
# tests/chunkshop/test_raw_store_s3.py
import sys
import types
import pytest


class _FakeS3Client:
    def __init__(self): self.store = {}
    def put_object(self, Bucket, Key, Body, ContentType=None, Metadata=None):
        self.store[(Bucket, Key)] = (Body, Metadata or {})
    def get_object(self, Bucket, Key):
        body, _ = self.store[(Bucket, Key)]
        return {"Body": types.SimpleNamespace(read=lambda: body)}
    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.store:
            from botocore.exceptions import ClientError  # type: ignore
            raise ClientError({"Error": {"Code": "404"}}, "head_object")
        _, md = self.store[(Bucket, Key)]
        return {"Metadata": md}
    def delete_object(self, Bucket, Key):
        self.store.pop((Bucket, Key), None)


@pytest.fixture
def fake_boto3(monkeypatch):
    client = _FakeS3Client()
    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: client
    monkeypatch.setitem(sys.modules, "boto3", fake)
    # minimal botocore.exceptions for the 404 path
    botocore = types.ModuleType("botocore")
    exc = types.ModuleType("botocore.exceptions")
    class ClientError(Exception):
        def __init__(self, error_response, op): self.response = error_response
    exc.ClientError = ClientError
    botocore.exceptions = exc
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exc)
    return client


def test_put_get_exists_delete(fake_boto3):
    from chunkshop.raw_store.s3 import S3RawStore
    store = S3RawStore(bucket="b", prefix="raw/")
    store.put("doc::1", b"hello", content_type="text/plain", meta={"fingerprint": "fp1"})
    ref = store.put("doc::2", b"world", content_type="text/plain")
    assert store.get(ref) == b"world"
    assert store.exists("doc::1") is True
    assert store.exists("doc::1", fingerprint="fp1") is True
    assert store.exists("doc::1", fingerprint="nope") is False
    assert store.exists("missing") is False
    store.delete("doc::1")
    assert store.exists("doc::1") is False
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_raw_store_s3.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.raw_store.s3`.

- [ ] **Step 3: Implement `s3.py`**

```python
# src/chunkshop/raw_store/s3.py
"""S3 RawStore backend (optional [s3] extra). Key layout: <prefix><sha256(doc_id)>.
Fingerprint is stored in object metadata for exists(doc_id, fingerprint) checks."""
from __future__ import annotations
import hashlib
from typing import Optional


class S3RawStore:
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: Optional[str] = None):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 raw_store requires boto3. Install with `pip install chunkshop[s3]`."
            ) from exc
        self.bucket = bucket
        self.prefix = prefix
        self._client = boto3.client("s3", endpoint_url=endpoint_url)

    def _key(self, doc_id: str) -> str:
        return self.prefix + hashlib.sha256(doc_id.encode("utf-8")).hexdigest()

    def put(self, doc_id, data, *, content_type, meta=None):
        md = {"doc_id": doc_id}
        if meta and "fingerprint" in meta:
            md["fingerprint"] = str(meta["fingerprint"])
        key = self._key(doc_id)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type, Metadata=md)
        return f"s3://{self.bucket}/{key}"

    def get(self, ref):
        _, _, rest = ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def exists(self, doc_id, fingerprint=None):
        from botocore.exceptions import ClientError
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=self._key(doc_id))
        except ClientError:
            return False
        if fingerprint is None:
            return True
        return resp.get("Metadata", {}).get("fingerprint") == fingerprint

    def delete(self, doc_id):
        self._client.delete_object(Bucket=self.bucket, Key=self._key(doc_id))
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_raw_store_s3.py -v` → PASS.

```bash
git -C .. add python/src/chunkshop/raw_store/s3.py python/tests/chunkshop/test_raw_store_s3.py
git -C .. commit -m "feat(raw_store): add S3RawStore backend"
```

---

## Task 11: OAuthTokens + OAuthProvider + OAuthTokenStorage

**Files:**
- Create: `src/chunkshop/oauth/tokens.py`, `src/chunkshop/oauth/base.py`, `src/chunkshop/oauth/storage.py`, `src/chunkshop/oauth/__init__.py`
- Test: `tests/chunkshop/test_oauth_contracts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_oauth_contracts.py
from datetime import datetime, timedelta, timezone
from chunkshop.oauth import OAuthTokens, OAuthProvider, OAuthTokenStorage


def test_tokens_dataclass():
    t = OAuthTokens(access_token="a", refresh_token="r",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    scopes=["read"], provider="google", provider_extras={})
    assert t.provider == "google"
    assert "read" in t.scopes


class _Prov:
    def authorization_url(self, state, redirect_uri, scopes): return "https://x"
    def exchange_code(self, code, redirect_uri): ...
    def refresh_token(self, refresh_token): ...
    def validate_scopes(self, tokens, required): return True


class _Store:
    async def get(self, user_id, provider): ...
    async def put(self, user_id, provider, tokens): ...
    async def delete(self, user_id, provider): ...


def test_provider_and_storage_runtime_checkable():
    assert isinstance(_Prov(), OAuthProvider)
    assert isinstance(_Store(), OAuthTokenStorage)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_oauth_contracts.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.oauth`.

- [ ] **Step 3: Implement the four files**

```python
# src/chunkshop/oauth/tokens.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]
    provider: str
    provider_extras: dict = field(default_factory=dict)
```

```python
# src/chunkshop/oauth/base.py
from __future__ import annotations
from typing import Protocol, runtime_checkable
from chunkshop.oauth.tokens import OAuthTokens


@runtime_checkable
class OAuthProvider(Protocol):
    def authorization_url(self, state: str, redirect_uri: str, scopes: list[str]) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...
    def refresh_token(self, refresh_token: str) -> OAuthTokens: ...
    def validate_scopes(self, tokens: OAuthTokens, required: list[str]) -> bool: ...
```

```python
# src/chunkshop/oauth/storage.py
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable
from chunkshop.oauth.tokens import OAuthTokens


@runtime_checkable
class OAuthTokenStorage(Protocol):
    """Interface only — storage is tenancy-scoped, so consumers own the impl
    (PG table, Vault, KMS, …). chunkshop never persists tokens."""
    async def get(self, user_id: str, provider: str) -> Optional[OAuthTokens]: ...
    async def put(self, user_id: str, provider: str, tokens: OAuthTokens) -> None: ...
    async def delete(self, user_id: str, provider: str) -> None: ...
```

```python
# src/chunkshop/oauth/__init__.py
from chunkshop.oauth.tokens import OAuthTokens
from chunkshop.oauth.base import OAuthProvider
from chunkshop.oauth.storage import OAuthTokenStorage
from chunkshop.oauth.refresh import proactive_refresh
from chunkshop.oauth._mock import MockOAuthProvider

__all__ = ["OAuthTokens", "OAuthProvider", "OAuthTokenStorage",
           "proactive_refresh", "MockOAuthProvider"]
```

Note: `__init__.py` imports `refresh` and `_mock` which land in Task 12. To keep this task's test green standalone, create stub `refresh.py` and `_mock.py` now with the real implementations from Task 12, OR temporarily trim the `__init__` re-exports to the three present symbols and expand in Task 12. Recommended: trim now, expand in Task 12 (keeps each task independently green).

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_oauth_contracts.py -v` → PASS.

```bash
git -C .. add python/src/chunkshop/oauth/ python/tests/chunkshop/test_oauth_contracts.py
git -C .. commit -m "feat(oauth): add OAuthTokens, OAuthProvider, OAuthTokenStorage"
```

---

## Task 12: proactive_refresh + MockOAuthProvider

**Files:**
- Create: `src/chunkshop/oauth/refresh.py`, `src/chunkshop/oauth/_mock.py`
- Modify: `src/chunkshop/oauth/__init__.py` (expand re-exports)
- Test: `tests/chunkshop/test_oauth_refresh.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_oauth_refresh.py
from datetime import datetime, timedelta, timezone
from chunkshop.oauth import OAuthTokens, MockOAuthProvider, proactive_refresh


def _tok(minutes):
    return OAuthTokens(access_token="a", refresh_token="r",
                       expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
                       scopes=["read"], provider="mock", provider_extras={})


def test_refresh_when_within_leeway():
    prov = MockOAuthProvider()
    out = proactive_refresh(_tok(2), provider=prov, leeway_minutes=5)
    assert out is not None
    assert out.access_token != "a"  # mock issues a fresh token


def test_no_refresh_when_outside_leeway():
    prov = MockOAuthProvider()
    assert proactive_refresh(_tok(60), provider=prov, leeway_minutes=5) is None


def test_mock_provider_predictable_tokens():
    prov = MockOAuthProvider()
    t = prov.exchange_code("code", "https://cb")
    assert t.provider == "mock"
    assert t.access_token.startswith("mock-access-")
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_oauth_refresh.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

```python
# src/chunkshop/oauth/refresh.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from chunkshop.oauth.tokens import OAuthTokens
from chunkshop.oauth.base import OAuthProvider


def proactive_refresh(tokens: OAuthTokens, *, provider: OAuthProvider,
                      leeway_minutes: int = 5) -> Optional[OAuthTokens]:
    """Refresh tokens if they expire within leeway_minutes, else return None.

    Avoids the reactive-401 refresh race where two concurrent callers both see
    a 401 and both try to refresh, one losing its refresh token."""
    if tokens.refresh_token is None:
        return None
    now = datetime.now(timezone.utc)
    if tokens.expires_at - now <= timedelta(minutes=leeway_minutes):
        return provider.refresh_token(tokens.refresh_token)
    return None
```

```python
# src/chunkshop/oauth/_mock.py
from __future__ import annotations
import itertools
from datetime import datetime, timedelta, timezone
from chunkshop.oauth.tokens import OAuthTokens


class MockOAuthProvider:
    """Predictable provider for tests — no network. Each issued token has a
    monotonically increasing suffix so refreshes are observably different."""
    def __init__(self):
        self._counter = itertools.count(1)

    def _issue(self) -> OAuthTokens:
        n = next(self._counter)
        return OAuthTokens(
            access_token=f"mock-access-{n}",
            refresh_token=f"mock-refresh-{n}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["read"], provider="mock", provider_extras={"n": n})

    def authorization_url(self, state, redirect_uri, scopes):
        return f"https://mock/auth?state={state}"

    def exchange_code(self, code, redirect_uri):
        return self._issue()

    def refresh_token(self, refresh_token):
        return self._issue()

    def validate_scopes(self, tokens, required):
        return set(required).issubset(set(tokens.scopes))
```

Then expand `oauth/__init__.py` to re-export all five symbols (as shown in Task 11 Step 3).

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_oauth_refresh.py tests/chunkshop/test_oauth_contracts.py -v` → PASS.

```bash
git -C .. add python/src/chunkshop/oauth/ python/tests/chunkshop/test_oauth_refresh.py
git -C .. commit -m "feat(oauth): add proactive_refresh and MockOAuthProvider"
```

---

## Task 13: Test helpers + mock_oauth fixture

**Files:**
- Create: `src/chunkshop/testing/__init__.py`, `src/chunkshop/testing/fixtures.py`
- Test: `tests/chunkshop/test_testing_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_testing_helpers.py
import pytest
from chunkshop.sources.base import Document, SyncMode
from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit


class _GoodInc:
    sync_mode = SyncMode.CURSOR
    def empty_cursor(self): return {"seq": 0}
    def iter_changes_since(self, cursor):
        if cursor.get("seq", 0) < 1:
            yield Document(id="a", content="x")
    def cursor_from(self, last_document): return {"seq": 1}


class _BadInc(_GoodInc):
    # never advances — always re-emits
    def cursor_from(self, last_document): return {"seq": 0}


def test_assert_cursor_advances_passes_for_good():
    assert_cursor_advances(_GoodInc())


def test_assert_cursor_advances_fails_for_bad():
    with pytest.raises(AssertionError):
        assert_cursor_advances(_BadInc())


def test_idempotent_on_re_emit_passes_for_good():
    assert_idempotent_on_re_emit(_GoodInc())


def test_idempotent_fails_when_re_emits():
    with pytest.raises(AssertionError):
        assert_idempotent_on_re_emit(_BadInc())
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_testing_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.testing`.

- [ ] **Step 3: Implement**

```python
# src/chunkshop/testing/__init__.py
"""Reusable connector test helpers. Importable by chunkshop's own tests and by
downstream plugins to validate their IncrementalSource implementations."""
from __future__ import annotations
from chunkshop.sources.base import IncrementalSource


def assert_cursor_advances(source: IncrementalSource) -> None:
    """Run a full cycle and assert the cursor moves off empty after ingesting."""
    cursor = source.empty_cursor()
    docs = list(source.iter_changes_since(cursor))
    assert docs, "expected at least one document on first sync"
    new_cursor = source.cursor_from(docs[-1])
    assert new_cursor != cursor, (
        f"cursor did not advance: {cursor!r} == {new_cursor!r}")


def assert_idempotent_on_re_emit(source: IncrementalSource) -> None:
    """First sync yields docs; re-syncing from the advanced cursor yields none."""
    cursor = source.empty_cursor()
    docs = list(source.iter_changes_since(cursor))
    assert docs, "expected documents on first sync"
    advanced = source.cursor_from(docs[-1])
    again = list(source.iter_changes_since(advanced))
    assert not again, f"expected no re-emit after cursor advance, got {len(again)} docs"
```

```python
# src/chunkshop/testing/fixtures.py
"""pytest fixtures for connector testing. Consumers add
`pytest_plugins = ["chunkshop.testing.fixtures"]` to their conftest."""
import pytest
from chunkshop.oauth import MockOAuthProvider


@pytest.fixture
def mock_oauth_provider():
    return MockOAuthProvider()
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_testing_helpers.py -v` → PASS (4 tests).

```bash
git -C .. add python/src/chunkshop/testing/ python/tests/chunkshop/test_testing_helpers.py
git -C .. commit -m "feat(testing): add connector test helpers and mock_oauth fixture"
```

---

## Task 14: Prove the Protocols — s3 source ETag cursor

**Files:**
- Modify: `src/chunkshop/sources/s3.py`
- Test: `tests/chunkshop/test_s3_incremental.py` (mocked boto3, no network)

- [ ] **Step 1: Write the failing test** (reuse the fake-boto3 pattern from Task 10; here a list+get fake)

```python
# tests/chunkshop/test_s3_incremental.py
import sys, types, pytest
from chunkshop.config import S3Source as Cfg
from chunkshop.sources.base import IncrementalSource, SyncMode


class _FakeS3:
    def __init__(self, objs): self.objs = objs  # list of (key, etag, body)
    def get_paginator(self, _):
        objs = self.objs
        class _P:
            def paginate(self, **kw):
                yield {"Contents": [{"Key": k, "ETag": e, "Size": len(b)} for k, e, b in objs]}
        return _P()
    def get_object(self, Bucket, Key):
        for k, e, b in self.objs:
            if k == Key:
                return {"Body": types.SimpleNamespace(read=lambda b=b: b), "ETag": e}
        raise KeyError(Key)


@pytest.fixture
def fake_boto3(monkeypatch):
    holder = {}
    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: holder["client"]
    monkeypatch.setitem(sys.modules, "boto3", fake)
    return holder


def test_s3_is_incremental(fake_boto3):
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one")])
    src = __import__("chunkshop.sources.s3", fromlist=["S3Source"]).S3Source(Cfg(type="s3", bucket="b"))
    assert isinstance(src, IncrementalSource)
    assert src.sync_mode == SyncMode.CURSOR


def test_s3_cursor_skips_unchanged_etags(fake_boto3):
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one"), ("k2", '"e2"', b"two")])
    from chunkshop.sources.s3 import S3Source
    src = S3Source(Cfg(type="s3", bucket="b"))
    cursor = src.empty_cursor()
    first = list(src.iter_changes_since(cursor))
    assert {d.id for d in first} == {"s3://b/k1", "s3://b/k2"}
    cursor = src.cursor_from(first[-1]) if False else {"k1": '"e1"', "k2": '"e2"'}
    # nothing changed → no re-emit
    assert list(src.iter_changes_since(cursor)) == []
    # change k2's etag → only k2 re-emitted
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one"), ("k2", '"e2x"', b"two!")])
    changed = list(src.iter_changes_since(cursor))
    assert {d.id for d in changed} == {"s3://b/k2"}
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_s3_incremental.py -v`
Expected: FAIL — `S3Source` is not an `IncrementalSource` / no `empty_cursor`.

- [ ] **Step 3: Implement — add cursor methods to `S3Source`** (cursor = `{key: etag}` map; FINGERPRINT-style but exposed as CURSOR)

Add `sync_mode = SyncMode.CURSOR` as a class attribute, and these methods. Keep the existing `iter_documents` for full-resync callers. Refactor the listing into a helper so both paths share it.

```python
from chunkshop.sources.base import Document, SyncMode

class S3Source:
    sync_mode = SyncMode.CURSOR

    def __init__(self, cfg): self.cfg = cfg

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 source requires boto3. pip install chunkshop[s3].") from exc
        return boto3.client("s3", endpoint_url=self.cfg.endpoint_url)

    def _list(self, client):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.cfg.bucket, Prefix=self.cfg.prefix):
            for obj in page.get("Contents") or []:
                yield obj["Key"], obj.get("ETag", ""), int(obj.get("Size", 0))

    def _fetch(self, client, key, etag, size) -> Document:
        body = client.get_object(Bucket=self.cfg.bucket, Key=key)["Body"].read()
        return Document(
            id=f"s3://{self.cfg.bucket}/{key}",
            content=body.decode("utf-8", errors="replace"),
            metadata={"bucket": self.cfg.bucket, "key": key, "size": size, "etag": etag},
            fingerprint=etag,
        )

    def iter_documents(self):
        client = self._client()
        for key, etag, size in list(self._list(client)):
            yield self._fetch(client, key, etag, size)

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict):
        client = self._client()
        for key, etag, size in list(self._list(client)):
            if cursor.get(key) != etag:
                yield self._fetch(client, key, etag, size)

    def cursor_from(self, last_document: Document) -> dict:
        # Cursor is the full key→etag map; callers persist the returned dict.
        # For S3 the authoritative cursor is built by the consumer accumulating
        # fingerprints; we expose a single-doc advance for batch checkpointing.
        meta = last_document.metadata or {}
        return {meta.get("key", last_document.id): last_document.fingerprint}
```

NOTE for the implementer: the test persists the full `{key: etag}` map as the cursor (that is the canonical S3 cursor shape). `cursor_from` is a per-doc helper for batch checkpointing; the consumer merges these into the running map. Document this clearly in the docstring and in `docs/cookbook/incremental-sources.md` (Task 16).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/chunkshop/test_s3_incremental.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm full-resync path unbroken**

Run: `uv run pytest tests/chunkshop/ -k s3 -v`
Expected: existing s3 tests still pass.

- [ ] **Step 6: Commit**

```bash
git -C .. add python/src/chunkshop/sources/s3.py python/tests/chunkshop/test_s3_incremental.py
git -C .. commit -m "feat(sources): s3 implements IncrementalSource via ETag cursor"
```

---

## Task 15: Prove the Protocols — pg_table updated_at cursor

**Files:**
- Modify: `src/chunkshop/config.py` (add optional `updated_at_column` to `PgTableSource`)
- Modify: `src/chunkshop/sources/pg_table.py`
- Test: `tests/chunkshop/test_pg_table_incremental.py` (DB-backed; skips if `$CHUNKSHOP_TEST_DSN` unreachable)

- [ ] **Step 1: Write the failing test** (follows the existing skip-if-no-DSN convention)

```python
# tests/chunkshop/test_pg_table_incremental.py
import os, pytest
psycopg = pytest.importorskip("psycopg")
from chunkshop.config import PgTableSource
from chunkshop.sources.base import IncrementalSource

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")


def _reachable():
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="CHUNKSHOP_TEST_DSN unreachable")


@pytest.fixture
def table():
    schema = "public"
    name = "chunkshop_test_inc"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
        cur.execute(f"CREATE TABLE {schema}.{name} (id text primary key, body text, updated_at timestamptz)")
        cur.execute(f"INSERT INTO {schema}.{name} VALUES ('a','aa', now() - interval '2 hours'),"
                    f"('b','bb', now() - interval '1 hour')")
        conn.commit()
    yield schema, name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
        conn.commit()


def _cfg(schema, name):
    return PgTableSource(type="pg_table", dsn=DSN, database=schema, table=name,
                         id_column="id", content_column="body", updated_at_column="updated_at")


def test_pg_table_is_incremental(table):
    src = __import__("chunkshop.sources.pg_table", fromlist=["PgTableSource"]).PgTableSource(_cfg(*table))
    assert isinstance(src, IncrementalSource)


def test_pg_table_cursor_only_returns_newer_rows(table):
    from chunkshop.sources.pg_table import PgTableSource as Src
    src = Src(_cfg(*table))
    first = list(src.iter_changes_since(src.empty_cursor()))
    assert {d.id for d in first} == {"a", "b"}
    cur = src.cursor_from(first[-1])
    # insert a newer row, re-sync → only the new one
    schema, name = table
    with psycopg.connect(DSN) as conn, c := conn.cursor():
        c.execute(f"INSERT INTO {schema}.{name} VALUES ('c','cc', now())")
        conn.commit()
    again = list(src.iter_changes_since(cur))
    assert {d.id for d in again} == {"c"}
```

- [ ] **Step 2: Run to verify fail** (start DB first: `docker compose -f docker-compose.test.yaml up -d`)

Run: `uv run pytest tests/chunkshop/test_pg_table_incremental.py -v`
Expected: FAIL — `updated_at_column` not a field / `PgTableSource` not `IncrementalSource`.

- [ ] **Step 3: Implement**

Add to `PgTableSource` config class: `updated_at_column: Optional[str] = None`.

Add cursor methods to the `PgTableSource` source. Cursor = `{"after": "<iso timestamp>"}`. When `updated_at_column` is set, `iter_changes_since` appends `WHERE <col> > %s ORDER BY <col>`; `cursor_from` returns the last row's timestamp.

```python
from chunkshop.sources.base import Document, SyncMode, IncrementalSource

class PgTableSource:
    sync_mode = SyncMode.CURSOR  # effective only when updated_at_column is set

    # ... existing __init__/iter_documents unchanged ...

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict):
        if not self.cfg.updated_at_column:
            # no cursor column → behave as full resync
            yield from self.iter_documents()
            return
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols); cols.append(self.cfg.title_column)
        ua_idx = len(cols); cols.append(self.cfg.updated_at_column)
        meta_start = len(cols); cols.extend(self.cfg.metadata_columns)
        ident = [sql.Identifier(c) for c in cols]
        q = sql.SQL("SELECT {c} FROM {s}.{t}").format(
            c=sql.SQL(", ").join(ident), s=sql.Identifier(self.cfg.database_name),
            t=sql.Identifier(self.cfg.table))
        params = []
        after = cursor.get("after")
        if after is not None:
            q = q + sql.SQL(" WHERE ") + sql.Identifier(self.cfg.updated_at_column) + sql.SQL(" > %s")
            params.append(after)
        q = q + sql.SQL(" ORDER BY ") + sql.Identifier(self.cfg.updated_at_column)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(q, params)
            for row in cur:
                meta = {self.cfg.metadata_columns[i]: _json_safe(row[meta_start + i])
                        for i in range(len(self.cfg.metadata_columns))}
                meta["_updated_at"] = _json_safe(row[ua_idx])
                yield Document(id=str(row[0]), content=row[1],
                               title=row[title_idx] if title_idx is not None else None,
                               metadata=meta)

    def cursor_from(self, last_document: Document) -> dict:
        ua = (last_document.metadata or {}).get("_updated_at")
        return {"after": ua}
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_pg_table_incremental.py -v` → PASS (or SKIP if no DB — verify PASS locally with DB up).

```bash
git -C .. add python/src/chunkshop/config.py python/src/chunkshop/sources/pg_table.py python/tests/chunkshop/test_pg_table_incremental.py
git -C .. commit -m "feat(sources): pg_table implements IncrementalSource via updated_at cursor"
```

---

## Task 16: Docs — incremental-sources + authoring-connectors

**Files:**
- Create: `docs/cookbook/incremental-sources.md`, `docs/cookbook/authoring-connectors.md`

- [ ] **Step 1: Write `incremental-sources.md`** covering: `SyncMode`, the two Protocols, `StaleCursorError`, the S3 `{key:etag}` cursor shape, the pg_table `{after: ts}` cursor shape, the freshness-vs-prune cadence split, and a worked consumer loop showing `empty_cursor → iter_changes_since → cursor_from → persist`. Include the explicit note: *chunkshop computes deltas; the consumer persists cursors and schedules runs.*

- [ ] **Step 2: Write `authoring-connectors.md`** covering: the `chunkshop.sources` entry-point group, the factory signature `(config: dict) -> Source`, registering in a plugin's `pyproject.toml`, the generic `type: connector` YAML block, validating `config` with a plugin-side pydantic model, optionally implementing `IncrementalSource`/`PrunableSource`/`RawStore`, and using `chunkshop.testing` helpers + `mock_oauth_provider`. Include a complete minimal example plugin (`pyproject.toml` + a `factory`).

- [ ] **Step 3: Commit**

```bash
git -C .. add docs/cookbook/incremental-sources.md docs/cookbook/authoring-connectors.md
git -C .. commit -m "docs: incremental sources + connector authoring guides"
```

---

## Task 17: pyproject — document the entry-point group + raw_store extras note

**Files:**
- Modify: `python/pyproject.toml`

- [ ] **Step 1: Add a commented entry-point group stub + a note** so plugin authors and `[s3]` raw_store users have a reference. Under a new comment block near `[project.entry-points]` (create the table if absent), document:

```toml
# Connector plugins register source types here. chunkshop core ships none;
# install a plugin (e.g. chunkshop-connectors) to add sources.
# [project.entry-points."chunkshop.sources"]
# gdrive = "chunkshop_connectors.gdrive:factory"
```

(Do NOT add real connector entry points to core — connectors live in the plugin package per the spec.)

- [ ] **Step 2: Verify packaging still builds**

Run: `uv build` (or `python -m build`) — expected: wheel + sdist build with no entry-point errors.

- [ ] **Step 3: Commit**

```bash
git -C .. add python/pyproject.toml
git -C .. commit -m "docs(pyproject): document chunkshop.sources entry-point group"
```

---

## Task 18 (SP-1b): Example sync loop

**Files:**
- Create: `examples/sync_loop.py` (repo root `examples/`, NOT under `python/src` — this is reference code, not shipped lib)
- Create: `examples/README.md`
- Test: `tests/chunkshop/test_example_sync_loop.py` (imports the example via path; runs it against an in-memory fake source + LocalRawStore + a stub sink)

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_example_sync_loop.py
import asyncio, importlib.util, pathlib, pytest
from chunkshop.sources.base import Document, SyncMode

EXAMPLE = pathlib.Path(__file__).parents[2] / "examples" / "sync_loop.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_loop_example", EXAMPLE)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class _Src:
    sync_mode = SyncMode.CURSOR
    def __init__(self): self._n = 0
    def empty_cursor(self): return {"seq": 0}
    def iter_changes_since(self, cursor):
        if cursor.get("seq", 0) < 1:
            yield Document(id="a", content="hello", fingerprint="fp1")
    def cursor_from(self, last_document): return {"seq": 1}


def test_sync_loop_runs_and_advances_cursor():
    mod = _load_module()
    seen = []
    result = asyncio.run(mod.run_sync(
        sources={"s1": _Src()},
        cursors={"s1": {"seq": 0}},
        on_document=lambda src_name, doc: seen.append((src_name, doc.id)),
        max_concurrent_tasks=2,
    ))
    assert seen == [("s1", "a")]
    assert result["s1"].docs_emitted == 1
    assert result["s1"].new_cursor == {"seq": 1}
    assert result["s1"].success is True


def test_sync_loop_isolates_failures():
    mod = _load_module()
    class _Boom(_Src):
        def iter_changes_since(self, cursor): raise RuntimeError("boom")
    result = asyncio.run(mod.run_sync(
        sources={"ok": _Src(), "bad": _Boom()},
        cursors={"ok": {"seq": 0}, "bad": {"seq": 0}},
        on_document=lambda *a: None, max_concurrent_tasks=2))
    assert result["ok"].success is True
    assert result["bad"].success is False
    assert isinstance(result["bad"].error, RuntimeError)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/chunkshop/test_example_sync_loop.py -v`
Expected: FAIL — example file missing.

- [ ] **Step 3: Implement `examples/sync_loop.py`** (the demoted #21 — a copy-me semaphore-bounded loop; explicitly NOT part of the installed package)

```python
# examples/sync_loop.py
"""COPY-ME EXAMPLE — not part of the chunkshop library.

A minimal semaphore-bounded sync loop showing how a CONSUMER drives chunkshop's
incremental primitives. Production orchestration (scheduling, retries, durable
cursor persistence, multi-tenant isolation, Redis) belongs in your service /
chunkshop_api — NOT here. This file is the baseline connector test harness and
a starting point to copy into your own code.
"""
from __future__ import annotations
import asyncio, time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from chunkshop.sources.base import Document, IncrementalSource, PrunableSource


class SourceTaskType(str, Enum):
    SYNC = "sync"
    PRUNE = "prune"


@dataclass
class TaskResult:
    task_type: SourceTaskType
    success: bool
    docs_emitted: int
    deletes_emitted: int
    new_cursor: Optional[dict]
    error: Optional[Exception]
    elapsed_ms: int


async def _run_one(name, source, cursor, on_document, on_delete, sem) -> TaskResult:
    async with sem:
        start = time.time()
        try:
            if isinstance(source, IncrementalSource):
                docs = await asyncio.to_thread(lambda: list(source.iter_changes_since(cursor)))
                for d in docs:
                    on_document(name, d)
                new_cursor = source.cursor_from(docs[-1]) if docs else cursor
                return TaskResult(SourceTaskType.SYNC, True, len(docs), 0, new_cursor, None,
                                  int((time.time() - start) * 1000))
            else:
                docs = await asyncio.to_thread(lambda: list(source.iter_documents()))
                for d in docs:
                    on_document(name, d)
                return TaskResult(SourceTaskType.SYNC, True, len(docs), 0, cursor, None,
                                  int((time.time() - start) * 1000))
        except Exception as exc:  # isolate per-source failure
            return TaskResult(SourceTaskType.SYNC, False, 0, 0, None, exc,
                              int((time.time() - start) * 1000))


async def run_sync(sources: dict, cursors: dict, on_document: Callable[[str, Document], None],
                   on_delete: Optional[Callable[[str, str], None]] = None,
                   max_concurrent_tasks: int = 5) -> dict[str, TaskResult]:
    sem = asyncio.Semaphore(max_concurrent_tasks)
    on_delete = on_delete or (lambda n, i: None)
    tasks = {name: asyncio.create_task(
                _run_one(name, src, cursors.get(name, {}), on_document, on_delete, sem))
             for name, src in sources.items()}
    results = {}
    for name, task in tasks.items():
        results[name] = await task
    return results
```

- [ ] **Step 4: Run to verify pass / Step 5: Commit**

Run: `uv run pytest tests/chunkshop/test_example_sync_loop.py -v` → PASS (2 tests).

```bash
git -C .. add examples/sync_loop.py examples/README.md python/tests/chunkshop/test_example_sync_loop.py
git -C .. commit -m "feat(examples): add copy-me sync loop (demoted #21) + connector test baseline"
```

---

## Task 19: Full-suite gate + cross-backend matrix

- [ ] **Step 1:** `uv run pytest -q` — expected: all SP-1 tests pass; pre-existing pass/skip counts otherwise unchanged.
- [ ] **Step 2:** `uv run pytest tests/chunkshop/test_cross_backend_matrix.py -q` — expected: still green (no connector cells added).
- [ ] **Step 3:** `ruff check src/chunkshop && ruff format --check src/chunkshop` — expected: clean.
- [ ] **Step 4: Commit any lint fixes**, then tag the SP-1 completion:

```bash
git -C .. commit -am "chore: SP-1 lint pass" || true
git -C .. tag sp1-foundation-complete
```

---

## Self-review (run before handing off to execution)

**Spec coverage** — every SP-1 spec section maps to a task: §4.1 Protocols/SyncMode/fingerprint/StaleCursorError → T1,T2; §4.2 registry+generic connector → T3,T4,T5,T6; §4.3 OAuth interfaces → T11,T12; §4.4 RawStore → T7,T8,T9,T10; §4.5 test helpers → T13; §4.6 proof on s3/pg_table → T14,T15; §5 SP-1b example loop → T18; docs → T16; packaging → T17; gates → T19. No gaps.

**Placeholder scan** — every code step contains complete code; no TBD/TODO. The only deferred linkage is the Task 5 → Task 9 `raw_store` type (temporary `Optional[dict]` → `Optional[RawStoreConfig]`), explicitly documented in both tasks.

**Type consistency** — `Document(... fingerprint=...)`, `SyncMode.CURSOR`, `empty_cursor`/`iter_changes_since`/`cursor_from`, `empty_prune_cursor`/`iter_deleted_since`, `RawStore.put/get/exists/delete`, `load_connector(name, config)`, `load_raw_store(cfg)`, `proactive_refresh(tokens, *, provider, leeway_minutes)` are used identically across all tasks and match §4 of the spec.

**Out of scope confirmed absent** — no connector implementations, no scheduler/queue/Redis, no Rust, no files.py parsers.
