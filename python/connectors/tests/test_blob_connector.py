"""Behavioural tests for the verified blob connector.

These tests are hermetic — no network egress. The ``blob_mock``
fixture (defined in ``tests/conftest.py`` and re-exported from
``chunkshop_connectors.testing.mocks.blob``) monkeypatches
``sys.modules['boto3']`` so ``boto3.client(...)`` returns an
in-memory fake.
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry

from chunkshop_connectors._tier import tier_of


def test_blob_registered_and_verified():
    registry.clear_cache()
    assert "blob" in registry.available_connectors()
    from chunkshop_connectors.blob import Connector
    assert tier_of(Connector) == "verified"


def test_blob_config_validation_rejects_bad():
    from chunkshop_connectors.blob import ConfigModel
    with pytest.raises(Exception):
        ConfigModel.model_validate({"bucket": 42})  # type-wrong


def test_blob_config_validation_rejects_extra_keys():
    from chunkshop_connectors.blob import ConfigModel
    # extra='forbid' should catch typos
    with pytest.raises(Exception):
        ConfigModel.model_validate({"bucket": "b", "buckett": "typo"})


def test_blob_yields_documents_against_mock(blob_mock):
    from chunkshop_connectors.blob import factory
    src = factory(blob_mock.valid_config)
    docs = list(src.iter_documents())
    assert len(docs) == 3
    assert all(d.content for d in docs)
    # Fingerprint propagation: ETags survive into chunkshop Documents.
    fingerprints = {d.fingerprint for d in docs}
    assert fingerprints == {'"etag-a"', '"etag-b"', '"etag-c"'}
    # IDs include the bucket and full key.
    ids = {d.id for d in docs}
    assert ids == {
        "s3://test-bucket/docs/a.txt",
        "s3://test-bucket/docs/b.txt",
        "s3://test-bucket/docs/c.md",
    }


def test_blob_resolves_via_registry(blob_mock):
    """End-to-end: dispatch via chunkshop.sources.load_source(ConnectorSource(...))."""
    from chunkshop.config import ConnectorSource
    from chunkshop.sources import load_source
    registry.clear_cache()
    src = load_source(
        ConnectorSource(
            type="connector",
            connector="blob",
            config=blob_mock.valid_config,
        )
    )
    docs = list(src.iter_documents())
    assert len(docs) == 3


def test_blob_skips_directory_markers(blob_mock):
    """Pseudo-directory keys (trailing slash) are silently skipped."""
    from chunkshop_connectors.blob import factory
    # Inject a fake directory marker into the mock
    blob_mock.client.objects.append(("docs/subdir/", '"etag-dir"', b""))
    src = factory(blob_mock.valid_config)
    docs = list(src.iter_documents())
    assert len(docs) == 3  # directory marker filtered out
