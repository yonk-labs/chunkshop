"""Hermetic boto3 mock for the blob connector.

Pattern adapted from ``chunkshop/tests/chunkshop/test_s3_incremental.py``'s
``_FakeS3`` shim. Monkeypatches ``sys.modules['boto3']`` with a stub
module whose ``client(...)`` returns a fake S3 client that implements
just enough of the API the connector consumes — ``get_paginator(...)``
and ``get_object(...)``.

Usage::

    from chunkshop_connectors.testing.mocks.blob import blob_mock  # noqa: F401
    def test_blob(blob_mock):
        ...
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest


class _FakeS3Client:
    """Minimal in-memory S3 client.

    ``objects`` is a list of ``(key, etag, body)`` tuples. Returned in
    list/get order; no ContinuationToken handling — chunkshop's blob
    connector doesn't depend on pagination edge cases.
    """

    def __init__(self, objects: list[tuple[str, str, bytes]]):
        self.objects = objects

    def get_paginator(self, op: str):
        objs = self.objects

        class _Paginator:
            def paginate(self, **kwargs: Any):
                # Honor a Prefix arg if passed
                prefix = kwargs.get("Prefix", "") or ""
                contents = [
                    {"Key": k, "ETag": e, "Size": len(b)}
                    for k, e, b in objs
                    if k.startswith(prefix)
                ]
                yield {"Contents": contents}

        return _Paginator()

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 -- boto3 surface
        for k, e, b in self.objects:
            if k == Key:
                return {
                    "Body": types.SimpleNamespace(read=lambda b=b: b),
                    "ETag": e,
                }
        raise KeyError(Key)


class _BlobMockHandle:
    """Returned to tests. ``valid_config`` is a ready-to-go config dict;
    ``client`` is the underlying fake — tests can swap it for a freshly
    seeded one between calls if needed."""

    def __init__(self, client: _FakeS3Client, config: dict[str, Any]):
        self.client = client
        self.valid_config = config


@pytest.fixture
def blob_mock(monkeypatch):
    """Provide a fake boto3.client + a valid_config dict for the blob connector.

    The fixture seeds three small text objects under prefix ``docs/``
    so the connector yields three Documents by default. Tests that
    need different fixtures can replace ``handle.client.objects`` in
    place — boto3's ``client(...)`` is wired to return the same
    handle each call.
    """
    objects: list[tuple[str, str, bytes]] = [
        ("docs/a.txt", '"etag-a"', b"alpha content"),
        ("docs/b.txt", '"etag-b"', b"beta content"),
        ("docs/c.md", '"etag-c"', b"gamma content"),
    ]
    fake_client = _FakeS3Client(objects)

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **k: fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    handle = _BlobMockHandle(
        client=fake_client,
        config={
            "bucket": "test-bucket",
            "prefix": "docs/",
        },
    )
    return handle
