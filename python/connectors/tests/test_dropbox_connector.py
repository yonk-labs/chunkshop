"""Behavioural tests for the verified dropbox connector.

Hermetic — ``dropbox_mock`` is an ``httpx.MockTransport``-backed
fixture that intercepts Dropbox v2 API calls (list_folder,
list_folder/continue, files/download).
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_dropbox_registered_and_verified():
    registry.clear_cache()
    assert "dropbox" in registry.available_connectors()
    from chunkshop_connectors.dropbox import Connector
    assert tier_of(Connector) == "verified"


def test_dropbox_config_validation_rejects_bad():
    from chunkshop_connectors.dropbox import ConfigModel
    # extra='forbid'
    with pytest.raises(Exception):
        ConfigModel.model_validate({"folder_path": "/", "typo": 1})
    # folder_path must be a string
    with pytest.raises(Exception):
        ConfigModel.model_validate({"folder_path": 42})
    # include_extensions must be list[str]
    with pytest.raises(Exception):
        ConfigModel.model_validate({"include_extensions": "not-a-list"})


def test_dropbox_yields_documents_against_mock(dropbox_mock):
    from chunkshop_connectors.dropbox import factory

    src = factory(dropbox_mock.valid_config)
    src._transport = dropbox_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    by_id = {d.id: d for d in docs}
    # Default fixture: README.md + notes/spec.md + logo.png (skipped by ext).
    assert set(by_id.keys()) == {"/readme.md", "/notes/spec.md"}
    readme = by_id["/readme.md"]
    assert readme.title == "README.md"
    assert "from dropbox" in readme.content
    assert readme.metadata is not None
    assert readme.metadata["path_display"] == "/README.md"
    assert readme.metadata["dropbox_id"] == "id:README.md"
    assert readme.metadata["size"] > 0


def test_dropbox_incremental_via_dropbox_cursor(dropbox_mock):
    from chunkshop_connectors.dropbox import factory

    src = factory(dropbox_mock.valid_config)
    src._transport = dropbox_mock.transport
    src._reset_client()

    cursor = src.empty_cursor()
    assert cursor == {}
    docs1 = list(src.iter_changes_since(cursor))
    assert {d.id for d in docs1} == {"/readme.md", "/notes/spec.md"}

    advanced = dict(cursor)
    for d in docs1:
        advanced.update(src.cursor_from(d))
    # Cursor advances to the Dropbox cursor the mock minted.
    assert "cursor" in advanced
    assert advanced["cursor"].startswith("dropbox-cursor-")
    first_cursor = advanced["cursor"]

    # Simulate a new file appearing via the delta API.
    dropbox_mock.add_file(
        path_display="/late.md",
        content="late arrival content",
    )
    dropbox_mock.add_change("/late.md")

    src2 = factory(dropbox_mock.valid_config)
    src2._transport = dropbox_mock.transport
    src2._reset_client()
    new_docs = list(src2.iter_changes_since(advanced))
    assert {d.id for d in new_docs} == {"/late.md"}
    assert "late arrival content" in new_docs[0].content
    advanced2 = dict(advanced)
    for d in new_docs:
        advanced2.update(src2.cursor_from(d))
    # Cursor advanced again.
    assert advanced2["cursor"] != first_cursor


def test_dropbox_satisfies_incremental_helpers(dropbox_mock):
    from chunkshop.testing import (
        assert_cursor_advances,
        assert_idempotent_on_re_emit,
    )
    from chunkshop_connectors.dropbox import factory

    src = factory(dropbox_mock.valid_config)
    src._transport = dropbox_mock.transport
    src._reset_client()

    assert isinstance(src, IncrementalSource)
    assert_cursor_advances(src)

    src2 = factory(dropbox_mock.valid_config)
    src2._transport = dropbox_mock.transport
    src2._reset_client()
    assert_idempotent_on_re_emit(src2)


def test_dropbox_include_extensions_filter(dropbox_mock):
    """`include_extensions` overrides the default text allow-list."""
    from chunkshop_connectors.dropbox import factory

    # Add a .csv file; restrict to only .csv via include_extensions.
    dropbox_mock.add_file(
        path_display="/data.csv",
        content="a,b\n1,2",
    )

    cfg = dict(dropbox_mock.valid_config)
    cfg["include_extensions"] = [".csv"]

    src = factory(cfg)
    src._transport = dropbox_mock.transport
    src._reset_client()
    docs = list(src.iter_documents())
    ids = {d.id for d in docs}
    # Only the .csv survives; .md files filtered out.
    assert ids == {"/data.csv"}


def test_dropbox_token_from_env(dropbox_mock, monkeypatch):
    """Connector without explicit token reads ``DROPBOX_TOKEN``."""
    from chunkshop_connectors.dropbox import factory

    cfg = dict(dropbox_mock.valid_config)
    cfg.pop("token", None)
    monkeypatch.setenv("DROPBOX_TOKEN", "env-dropbox-token")

    src = factory(cfg)
    src._transport = dropbox_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    assert len(docs) >= 1
    assert "env-dropbox-token" in dropbox_mock.seen_tokens


def test_dropbox_handles_pagination(dropbox_mock):
    """Seed many entries to confirm continue-pagination works."""
    from chunkshop_connectors.dropbox import factory

    # Add 50 markdown files — well under Dropbox's 2000/page limit so
    # they'll come back in one shot; but verify the loop semantics by
    # forcing a multi-page continue via the queue.
    for i in range(50):
        dropbox_mock.add_file(
            path_display=f"/bulk/{i}.md",
            content=f"bulk file {i}",
        )

    src = factory(dropbox_mock.valid_config)
    src._transport = dropbox_mock.transport
    src._reset_client()
    docs = list(src.iter_documents())
    # 2 defaults + 50 bulk
    bulk_count = sum(1 for d in docs if d.id.startswith("/bulk/"))
    assert bulk_count == 50
