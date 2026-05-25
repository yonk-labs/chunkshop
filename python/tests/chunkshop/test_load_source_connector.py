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
