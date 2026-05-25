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
