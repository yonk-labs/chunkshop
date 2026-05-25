"""Behavioural tests for the verified rss connector.

Hermetic — ``rss_mock`` swaps ``sys.modules['feedparser']`` with a
stub that returns canned entries.
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry

from chunkshop_connectors._tier import tier_of


def test_rss_registered_and_verified():
    registry.clear_cache()
    assert "rss" in registry.available_connectors()
    from chunkshop_connectors.rss import Connector
    assert tier_of(Connector) == "verified"


def test_rss_config_validation_rejects_bad():
    from chunkshop_connectors.rss import ConfigModel
    with pytest.raises(Exception):
        ConfigModel.model_validate({"url": 42})  # type-wrong


def test_rss_config_validation_rejects_extra_keys():
    from chunkshop_connectors.rss import ConfigModel
    with pytest.raises(Exception):
        ConfigModel.model_validate({"url": "https://x", "ural": "typo"})


def test_rss_yields_documents_against_mock(rss_mock):
    from chunkshop_connectors.rss import factory
    src = factory(rss_mock.valid_config)
    docs = list(src.iter_documents())
    assert len(docs) == 3
    # Entry 1 has full content; check it lands in `content`
    one = next(d for d in docs if d.id == "urn:entry:1")
    assert "Full body of post one" in one.content
    assert one.title == "First post"
    assert one.metadata and one.metadata["author"] == "alice"
    # Entry 2 has only summary — should still produce non-empty content
    two = next(d for d in docs if d.id == "urn:entry:2")
    assert "just a summary" in two.content
    # Entry 3 has empty id — falls back to the link
    three = next(d for d in docs if d.id == "https://example.com/3")
    assert three.title == "Third post"


def test_rss_resolves_via_registry(rss_mock):
    from chunkshop.config import ConnectorSource
    from chunkshop.sources import load_source
    registry.clear_cache()
    src = load_source(
        ConnectorSource(
            type="connector",
            connector="rss",
            config=rss_mock.valid_config,
        )
    )
    docs = list(src.iter_documents())
    assert len(docs) == 3
