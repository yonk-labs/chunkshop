"""Behavioural tests for the verified gdrive connector.

Hermetic — ``gdrive_mock`` builds an ``httpx.MockTransport`` that
serves Drive v3 file-list, file-export, file-download, and changes
endpoints from in-memory state. No live network.

The autouse loopback-only socket guard in ``conftest.py`` is
satisfied because ``MockTransport`` never opens a real socket.
"""
from __future__ import annotations

import json

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_gdrive_registered_and_verified():
    registry.clear_cache()
    assert "gdrive" in registry.available_connectors()
    from chunkshop_connectors.gdrive import Connector
    assert tier_of(Connector) == "verified"


def test_gdrive_config_requires_folder_or_query():
    from chunkshop_connectors.gdrive import ConfigModel
    # Neither folder_id nor query → reject.
    with pytest.raises(Exception):
        ConfigModel.model_validate({})
    # folder_id alone is fine.
    ConfigModel.model_validate({"folder_id": "abc"})
    # query alone is fine.
    ConfigModel.model_validate({"query": "name contains 'foo'"})
    # extra='forbid' on typos.
    with pytest.raises(Exception):
        ConfigModel.model_validate({"folder_id": "abc", "extra_typo": 1})


def test_gdrive_config_redacts_oauth_tokens_in_repr():
    from chunkshop_connectors.gdrive import ConfigModel

    cfg = ConfigModel.model_validate({
        "folder_id": "abc",
        "oauth_tokens": {
            "access_token": "ya29.SECRET_AT",
            "refresh_token": "1//SECRET_RT",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "scopes": ["drive.readonly"],
            "provider": "google",
            "provider_extras": {},
        },
    })
    r = repr(cfg)
    assert "ya29.SECRET_AT" not in r
    assert "SECRET_RT" not in r


def test_gdrive_yields_documents_against_mock(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    src = factory(gdrive_mock.valid_config)
    # Pin the transport so HTTP routes through the mock.
    src._transport = gdrive_mock.transport
    src._reset_client()

    with pytest.warns(UserWarning, match="skipping"):
        docs = list(src.iter_documents())

    by_id = {d.id: d for d in docs}
    # The default fixture seeds: 1 google-doc, 1 plain-text file, 1 image.
    # Image is skipped silently with a warning.
    assert set(by_id.keys()) == {"file-doc-1", "file-txt-1"}
    doc = by_id["file-doc-1"]
    assert doc.title == "Design Notes"
    assert "exported google-doc content" in doc.content
    assert doc.metadata["mime_type"] == "application/vnd.google-apps.document"
    assert doc.metadata["drive_id"] == "file-doc-1"

    txt = by_id["file-txt-1"]
    assert "raw text content" in txt.content
    assert txt.metadata["mime_type"] == "text/plain"


def test_gdrive_incremental_via_changes_api(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    # First sync from empty cursor: all matching files.
    src1 = factory(gdrive_mock.valid_config)
    src1._transport = gdrive_mock.transport
    src1._reset_client()

    cursor = src1.empty_cursor()
    assert cursor == {}
    with pytest.warns(UserWarning, match="skipping"):
        docs1 = list(src1.iter_changes_since(cursor))
    assert {d.id for d in docs1} == {"file-doc-1", "file-txt-1"}

    advanced = dict(cursor)
    for d in docs1:
        advanced.update(src1.cursor_from(d))
    # First-sync cursor advances to the startPageToken the mock issued.
    assert advanced == {"page_token": gdrive_mock.start_page_token}

    # Simulate a new file appearing as a change with a fresh page token.
    gdrive_mock.add_file(
        file_id="file-txt-2",
        name="Late Arrival.txt",
        mime_type="text/plain",
        content=b"new file body",
    )
    gdrive_mock.add_change("file-txt-2", new_start_page_token="TOKEN_2")

    src2 = factory(gdrive_mock.valid_config)
    src2._transport = gdrive_mock.transport
    src2._reset_client()
    new_docs = list(src2.iter_changes_since(advanced))
    assert {d.id for d in new_docs} == {"file-txt-2"}
    assert "new file body" in new_docs[0].content
    advanced2 = dict(advanced)
    for d in new_docs:
        advanced2.update(src2.cursor_from(d))
    assert advanced2 == {"page_token": "TOKEN_2"}


def test_gdrive_satisfies_incremental_helpers(gdrive_mock):
    from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit
    from chunkshop_connectors.gdrive import factory

    src = factory(gdrive_mock.valid_config)
    src._transport = gdrive_mock.transport
    src._reset_client()

    assert isinstance(src, IncrementalSource)
    # Cursor advances after a first sync.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert_cursor_advances(src)

    # Second sync with the new instance behaves idempotently — no changes
    # since the last newStartPageToken means zero new documents emitted.
    src2 = factory(gdrive_mock.valid_config)
    src2._transport = gdrive_mock.transport
    src2._reset_client()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert_idempotent_on_re_emit(src2)


def test_gdrive_oauth_tokens_from_env(gdrive_mock, monkeypatch):
    """If ``oauth_tokens`` is omitted, the connector reads ``GDRIVE_OAUTH_TOKENS``."""
    from chunkshop_connectors.gdrive import factory

    cfg = dict(gdrive_mock.valid_config)
    tokens = cfg.pop("oauth_tokens")
    monkeypatch.setenv("GDRIVE_OAUTH_TOKENS", json.dumps(tokens))

    src = factory(cfg)
    src._transport = gdrive_mock.transport
    src._reset_client()

    with pytest.warns(UserWarning, match="skipping"):
        docs = list(src.iter_documents())
    assert {d.id for d in docs} == {"file-doc-1", "file-txt-1"}
    # Authorization header carried the access token from env.
    assert "fake-at" in gdrive_mock.seen_tokens


def test_gdrive_missing_oauth_tokens_raises_on_first_call(gdrive_mock, monkeypatch):
    """When neither config nor env supplies tokens, ValueError on first API call."""
    from chunkshop_connectors.gdrive import factory

    cfg = dict(gdrive_mock.valid_config)
    cfg.pop("oauth_tokens")
    monkeypatch.delenv("GDRIVE_OAUTH_TOKENS", raising=False)

    src = factory(cfg)
    # Construction does not raise — resolution is lazy.
    with pytest.raises(ValueError, match="oauth_tokens"):
        list(src.iter_documents())


def test_gdrive_image_files_skipped_with_warning(gdrive_mock):
    """The default fixture includes an image; verify it's silently dropped with a warning."""
    from chunkshop_connectors.gdrive import factory

    src = factory(gdrive_mock.valid_config)
    src._transport = gdrive_mock.transport
    src._reset_client()

    with pytest.warns(UserWarning, match="skipping"):
        docs = list(src.iter_documents())
    paths = {d.id for d in docs}
    assert "file-img-1" not in paths


def test_gdrive_sends_bearer_token(gdrive_mock):
    """All Drive API calls must include ``Authorization: Bearer <access_token>``."""
    from chunkshop_connectors.gdrive import factory

    src = factory(gdrive_mock.valid_config)
    src._transport = gdrive_mock.transport
    src._reset_client()

    with pytest.warns(UserWarning):
        list(src.iter_documents())
    assert "fake-at" in gdrive_mock.seen_tokens


# ---------------------------------------------------------------------------
# Mode B — explicit file_ids selection (single-file / multi-select ingest)
# ---------------------------------------------------------------------------
def _file_ids_config(mock, ids, *, reprocess=False):
    """Build a connector config in file_ids mode from the mock's valid_config."""
    cfg = dict(mock.valid_config)
    cfg.pop("folder_id")
    cfg["file_ids"] = list(ids)
    if reprocess:
        cfg["reprocess"] = True
    return cfg


def test_gdrive_config_accepts_file_ids():
    from chunkshop_connectors.gdrive import ConfigModel

    # file_ids alone is a valid selector.
    ConfigModel.model_validate({"file_ids": ["abc", "def"]})
    # file_ids cannot be combined with folder_id/query — distinct modes.
    with pytest.raises(Exception):
        ConfigModel.model_validate({"file_ids": ["abc"], "folder_id": "f1"})
    with pytest.raises(Exception):
        ConfigModel.model_validate({"file_ids": ["abc"], "query": "name = 'x'"})
    # Unsafe ids are rejected (same allowlist as folder_id).
    with pytest.raises(Exception):
        ConfigModel.model_validate({"file_ids": ["ok", "bad id!"]})


def test_gdrive_file_ids_yields_selected_documents(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    src = factory(_file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"]))
    src._transport = gdrive_mock.transport
    src._reset_client()

    docs = list(src.iter_documents())
    by_id = {d.id: d for d in docs}
    # Exactly the selected files — no folder walk, no image.
    assert set(by_id) == {"file-doc-1", "file-txt-1"}
    assert "exported google-doc content" in by_id["file-doc-1"].content
    assert "raw text content" in by_id["file-txt-1"].content
    assert by_id["file-doc-1"].metadata["drive_id"] == "file-doc-1"


def test_gdrive_file_ids_incremental_skips_unchanged(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    src1 = factory(_file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"]))
    src1._transport = gdrive_mock.transport
    src1._reset_client()

    cursor = src1.empty_cursor()
    assert cursor == {}
    docs1 = list(src1.iter_changes_since(cursor))
    assert {d.id for d in docs1} == {"file-doc-1", "file-txt-1"}

    advanced = dict(cursor)
    for d in docs1:
        advanced.update(src1.cursor_from(d))
    # Cursor is a {file_id: modifiedTime} map.
    assert advanced == {
        "file-doc-1": "2026-05-25T12:00:00.000Z",
        "file-txt-1": "2026-05-25T12:00:00.000Z",
    }

    # Second sync, nothing changed → zero docs re-emitted.
    src2 = factory(_file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"]))
    src2._transport = gdrive_mock.transport
    src2._reset_client()
    docs2 = list(src2.iter_changes_since(advanced))
    assert docs2 == []


def test_gdrive_file_ids_incremental_reemits_changed(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    src1 = factory(_file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"]))
    src1._transport = gdrive_mock.transport
    src1._reset_client()
    docs1 = list(src1.iter_changes_since(src1.empty_cursor()))
    advanced = {}
    for d in docs1:
        advanced.update(src1.cursor_from(d))

    # Edit file-txt-1 — bump its modifiedTime.
    gdrive_mock.add_file(
        file_id="file-txt-1",
        name="readme.txt",
        mime_type="text/plain",
        content=b"updated text content",
        modified_time="2026-05-26T09:00:00.000Z",
    )

    src2 = factory(_file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"]))
    src2._transport = gdrive_mock.transport
    src2._reset_client()
    docs2 = list(src2.iter_changes_since(advanced))
    # Only the edited file re-emits.
    assert {d.id for d in docs2} == {"file-txt-1"}
    assert "updated text content" in docs2[0].content

    advanced2 = dict(advanced)
    for d in docs2:
        advanced2.update(src2.cursor_from(d))
    # Unchanged file keeps its old timestamp; edited file advances.
    assert advanced2 == {
        "file-doc-1": "2026-05-25T12:00:00.000Z",
        "file-txt-1": "2026-05-26T09:00:00.000Z",
    }


def test_gdrive_file_ids_reprocess_reemits_unchanged(gdrive_mock):
    from chunkshop_connectors.gdrive import factory

    # Seed a populated cursor as if both files were already ingested.
    cursor = {
        "file-doc-1": "2026-05-25T12:00:00.000Z",
        "file-txt-1": "2026-05-25T12:00:00.000Z",
    }
    src = factory(
        _file_ids_config(gdrive_mock, ["file-doc-1", "file-txt-1"], reprocess=True)
    )
    src._transport = gdrive_mock.transport
    src._reset_client()

    # Nothing changed, but reprocess forces re-emit of all selected files.
    docs = list(src.iter_changes_since(cursor))
    assert {d.id for d in docs} == {"file-doc-1", "file-txt-1"}
