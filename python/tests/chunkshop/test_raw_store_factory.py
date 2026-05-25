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
