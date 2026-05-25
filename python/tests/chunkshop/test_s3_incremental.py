# tests/chunkshop/test_s3_incremental.py
import sys, types, pytest
from chunkshop.config import S3Source as Cfg
from chunkshop.sources.base import IncrementalSource, SyncMode
from chunkshop.testing import (assert_cursor_advances,
                               assert_idempotent_on_re_emit, merge_cursor)


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
    # Build the next cursor via the REAL merge of cursor_from over emitted docs.
    cursor = merge_cursor(src, cursor, first)
    assert cursor == {"k1": '"e1"', "k2": '"e2"'}, "merge must accumulate full manifest"
    # nothing changed → no re-emit
    assert list(src.iter_changes_since(cursor)) == []
    # change k2's etag → only k2 re-emitted
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one"), ("k2", '"e2x"', b"two!")])
    changed = list(src.iter_changes_since(cursor))
    assert {d.id for d in changed} == {"s3://b/k2"}
    # merging the changed delta into the running cursor preserves k1 (unchanged)
    cursor = merge_cursor(src, cursor, changed)
    assert cursor == {"k1": '"e1"', "k2": '"e2x"'}


def test_real_s3_passes_cursor_advances(fake_boto3):
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one"), ("k2", '"e2"', b"two")])
    from chunkshop.sources.s3 import S3Source
    src = S3Source(Cfg(type="s3", bucket="b"))
    assert_cursor_advances(src)


def test_real_s3_passes_idempotent_on_re_emit(fake_boto3):
    fake_boto3["client"] = _FakeS3([("k1", '"e1"', b"one"), ("k2", '"e2"', b"two")])
    from chunkshop.sources.s3 import S3Source
    src = S3Source(Cfg(type="s3", bucket="b"))
    assert_idempotent_on_re_emit(src)
