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
