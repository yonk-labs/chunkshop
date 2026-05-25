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
