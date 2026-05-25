"""Bulk smoke for every experimental-tier connector.

Verifies that the remaining experimental connectors are:

1. importable as ``chunkshop_connectors.<name>``,
2. expose ``Connector`` + ``factory`` symbols,
3. mark the class as ``"experimental"`` via :func:`tier_of`,
4. registered in chunkshop's :mod:`chunkshop.sources.registry`, and
5. raise :class:`StubError` (with the connector name in the message)
   when their ``iter_documents`` is exercised.

``notion``, ``dropbox``, and ``gitlab`` were promoted to verified-tier;
their behavioural tests live in ``test_notion_connector.py``,
``test_dropbox_connector.py``, and ``test_gitlab_connector.py``.
"""
from __future__ import annotations

import importlib

import pytest

from chunkshop.sources import registry

from chunkshop_connectors._stub import StubError
from chunkshop_connectors._tier import tier_of

EXPERIMENTAL = [
    "confluence",
    "jira",
    "box",
    "bitbucket",
    "gmail",
    "imap",
    "discord",
    "airtable",
    "asana",
    "zendesk",
    "sharepoint",
    "teams",
    "r2",
    "gcs",
    "oci",
    "seafile",
    "webdav",
    "moodle",
    "dingtalk",
    "rest_api",
]


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_experimental_importable(name):
    mod = importlib.import_module(f"chunkshop_connectors.{name}")
    assert hasattr(mod, "factory")
    assert hasattr(mod, "Connector")
    assert tier_of(mod.Connector) == "experimental"


def test_experimental_all_in_registry():
    registry.clear_cache()
    avail = set(registry.available_connectors())
    for name in EXPERIMENTAL:
        assert name in avail, f"{name} not registered"


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_experimental_stub_raises_clear_error(name):
    from chunkshop.config import ConnectorSource
    from chunkshop.sources import load_source

    registry.clear_cache()
    src = load_source(
        ConnectorSource(type="connector", connector=name, config={})
    )
    with pytest.raises(StubError, match=name):
        list(src.iter_documents())
