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
