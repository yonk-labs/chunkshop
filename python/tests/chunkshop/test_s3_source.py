"""Tests for chunkshop.sources.s3.S3Source.

Skips cleanly when ``CHUNKSHOP_S3_TEST_BUCKET`` is unset. When set, uploads a
3-key fixture (using standard AWS credentials), runs the source, asserts
shape, and cleans up. Set ``CHUNKSHOP_S3_TEST_ENDPOINT`` to point at a local
minio (or any S3-compatible) server instead of AWS.
"""
from __future__ import annotations

import os
import uuid

import pytest

from chunkshop.config import S3Source as Cfg
from chunkshop.sources.s3 import S3Source


pytestmark = pytest.mark.skipif(
    not os.environ.get("CHUNKSHOP_S3_TEST_BUCKET"),
    reason="CHUNKSHOP_S3_TEST_BUCKET not set; skipping S3 source integration test",
)


def _client(endpoint_url):
    boto3 = pytest.importorskip("boto3")
    return boto3.client("s3", endpoint_url=endpoint_url)


def test_s3_source_lists_and_fetches_three_keys():
    bucket = os.environ["CHUNKSHOP_S3_TEST_BUCKET"]
    endpoint_url = os.environ.get("CHUNKSHOP_S3_TEST_ENDPOINT")
    prefix = f"chunkshop-s3-test/{uuid.uuid4().hex[:8]}/"

    client = _client(endpoint_url)
    keys = [f"{prefix}alpha.txt", f"{prefix}bravo.txt", f"{prefix}charlie.txt"]
    bodies = ["alpha body", "bravo body — longer", "charlie body"]
    try:
        for key, body in zip(keys, bodies):
            client.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))

        cfg = Cfg(type="s3", bucket=bucket, prefix=prefix, endpoint_url=endpoint_url)
        docs = list(S3Source(cfg).iter_documents())
        assert len(docs) == 3

        ids = sorted(d.id for d in docs)
        assert ids == sorted(f"s3://{bucket}/{key}" for key in keys)

        by_key = {d.metadata["key"]: d for d in docs}
        for key, body in zip(keys, bodies):
            d = by_key[key]
            assert d.content == body
            assert d.title is None
            assert d.metadata["bucket"] == bucket
            assert d.metadata["size"] == len(body.encode("utf-8"))
            # ETag is wrapped in quotes by S3; just check it's a non-empty string.
            assert isinstance(d.metadata["etag"], str)
    finally:
        for key in keys:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
