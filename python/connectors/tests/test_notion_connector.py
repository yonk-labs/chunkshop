"""Behavioural tests for the verified notion connector.

Hermetic — ``notion_mock`` builds an ``httpx.MockTransport`` that
serves Notion v1 database-query, page-fetch, and block-children
endpoints from in-memory state. No live network.

The autouse loopback-only socket guard in ``conftest.py`` is
satisfied because ``MockTransport`` never opens a real socket.
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_notion_registered_and_verified():
    registry.clear_cache()
    assert "notion" in registry.available_connectors()
    from chunkshop_connectors.notion import Connector
    assert tier_of(Connector) == "verified"


def test_notion_config_validation_rejects_bad():
    from chunkshop_connectors.notion import ConfigModel
    # Neither database_id nor page_ids → reject.
    with pytest.raises(Exception):
        ConfigModel.model_validate({})
    # Both database_id AND page_ids → reject (must be one or the other).
    with pytest.raises(Exception):
        ConfigModel.model_validate({
            "database_id": "00112233-4455-6677-8899-aabbccddeeff",
            "page_ids": ["00112233-4455-6677-8899-aabbccddee00"],
        })
    # Malformed Notion ID
    with pytest.raises(Exception):
        ConfigModel.model_validate({"database_id": "not-a-uuid"})
    # extra='forbid'
    with pytest.raises(Exception):
        ConfigModel.model_validate({
            "database_id": "00112233-4455-6677-8899-aabbccddeeff",
            "typo": 1,
        })


def test_notion_yields_documents_against_mock(notion_mock):
    from chunkshop_connectors.notion import factory

    src = factory(notion_mock.valid_config)
    src._transport = notion_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    by_id = {d.id: d for d in docs}
    assert set(by_id.keys()) == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }
    first = by_id["11111111-1111-1111-1111-111111111111"]
    assert first.title == "First Page"
    assert "Hello chunkshop" in first.content
    assert "second paragraph" in first.content
    assert first.metadata is not None
    assert first.metadata["notion_id"] == "11111111-1111-1111-1111-111111111111"
    assert first.metadata["last_edited_time"] == "2026-05-25T12:00:00.000Z"
    assert first.metadata["parent_type"] == "database_id"


def test_notion_incremental_via_cursor(notion_mock):
    from chunkshop_connectors.notion import factory

    src = factory(notion_mock.valid_config)
    src._transport = notion_mock.transport
    src._reset_client()

    cursor = src.empty_cursor()
    assert cursor == {}
    docs1 = list(src.iter_changes_since(cursor))
    assert {d.id for d in docs1} == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }

    advanced = dict(cursor)
    for d in docs1:
        advanced.update(src.cursor_from(d))
    # Cursor advances to the max last_edited_time across the two pages.
    assert advanced == {"after_last_edited_time": "2026-05-25T13:00:00.000Z"}

    # Simulate an edit on page 1 with a later timestamp; add a new page too.
    notion_mock.touch_page(
        "11111111-1111-1111-1111-111111111111",
        last_edited_time="2026-05-25T14:00:00.000Z",
    )
    notion_mock.add_page(
        page_id="33333333-3333-3333-3333-333333333333",
        title="Late Arrival",
        body_blocks=["Brand new page"],
        last_edited_time="2026-05-25T15:00:00.000Z",
        database_id=notion_mock.valid_config["database_id"],
    )

    src2 = factory(notion_mock.valid_config)
    src2._transport = notion_mock.transport
    src2._reset_client()
    new_docs = list(src2.iter_changes_since(advanced))
    new_ids = {d.id for d in new_docs}
    # Both the touched page (edited > cursor) and the new page should appear.
    assert new_ids == {
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    }


def test_notion_satisfies_incremental_helpers(notion_mock):
    from chunkshop.testing import (
        assert_cursor_advances,
        assert_idempotent_on_re_emit,
    )
    from chunkshop_connectors.notion import factory

    src = factory(notion_mock.valid_config)
    src._transport = notion_mock.transport
    src._reset_client()

    assert isinstance(src, IncrementalSource)
    assert_cursor_advances(src)

    src2 = factory(notion_mock.valid_config)
    src2._transport = notion_mock.transport
    src2._reset_client()
    assert_idempotent_on_re_emit(src2)


def test_notion_page_ids_mode(notion_mock):
    """When ``page_ids`` is configured, fetch each page individually (no database)."""
    from chunkshop_connectors.notion import factory

    cfg = dict(notion_mock.valid_config)
    cfg.pop("database_id")
    cfg["page_ids"] = ["22222222-2222-2222-2222-222222222222"]

    src = factory(cfg)
    src._transport = notion_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].id == "22222222-2222-2222-2222-222222222222"
    assert docs[0].title == "Second Page"
    assert "Notion content here" in docs[0].content


def test_notion_token_from_env(notion_mock, monkeypatch):
    """Connector without explicit token reads ``NOTION_TOKEN``."""
    from chunkshop_connectors.notion import factory

    cfg = dict(notion_mock.valid_config)
    cfg.pop("token", None)
    monkeypatch.setenv("NOTION_TOKEN", "env-notion-secret")

    src = factory(cfg)
    src._transport = notion_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    assert len(docs) >= 1
    assert "env-notion-secret" in notion_mock.seen_tokens


def test_notion_handles_pagination(notion_mock):
    """Add many pages to force database-query pagination."""
    from chunkshop_connectors.notion import factory

    # Default config carries 2 pages — add enough more to span >1 page
    # at our default page_size (the connector requests 100).
    database_id = notion_mock.valid_config["database_id"]
    # We'll lie about ``page_size`` by hacking the mock to honour a small
    # page_size from the request body. Simpler: just make sure the
    # request handler correctly chains start_cursor across calls when
    # we add >100 pages. 105 is enough to force a 2nd round-trip.
    for i in range(105):
        notion_mock.add_page(
            page_id=f"aaaaaaaa-aaaa-aaaa-aaaa-{i:012d}",
            title=f"Page {i}",
            body_blocks=[f"body {i}"],
            last_edited_time="2026-05-25T10:00:00.000Z",
            database_id=database_id,
        )

    src = factory(notion_mock.valid_config)
    src._transport = notion_mock.transport
    src._reset_client()
    docs = list(src.iter_documents())
    # 2 originals + 105 added
    assert len(docs) == 107
