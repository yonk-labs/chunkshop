"""Behavioural tests for the verified gitlab connector.

Hermetic — ``gitlab_mock`` is an ``httpx.MockTransport``-backed
fixture serving the GitLab v4 REST endpoints the connector
consumes. No live network egress.
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_gitlab_registered_and_verified():
    registry.clear_cache()
    assert "gitlab" in registry.available_connectors()
    from chunkshop_connectors.gitlab import Connector
    assert tier_of(Connector) == "verified"


def test_gitlab_config_validation_rejects_bad():
    from chunkshop_connectors.gitlab import ConfigModel
    # missing required `project`
    with pytest.raises(Exception):
        ConfigModel.model_validate({})
    # bad project shape
    with pytest.raises(Exception):
        ConfigModel.model_validate({"project": "no slash but with spaces"})
    # extra='forbid'
    with pytest.raises(Exception):
        ConfigModel.model_validate({"project": "acme/widgets", "typo": 1})


def test_gitlab_yields_documents_against_mock(gitlab_mock):
    from chunkshop_connectors.gitlab import factory

    src = factory(gitlab_mock.valid_config)
    src._transport = gitlab_mock.transport
    src._reset_client()

    with pytest.warns(UserWarning, match="binary"):
        docs = list(src.iter_documents())
    paths = sorted(d.id for d in docs)
    assert paths == ["README.md", "docs/b.md", "src/a.py"]
    readme = next(d for d in docs if d.id == "README.md")
    assert readme.title == "README.md"
    assert "hello chunkshop from gitlab" in readme.content
    assert readme.metadata is not None
    assert readme.metadata["path"] == "README.md"
    assert readme.metadata["branch"] == "main"
    assert readme.metadata["blob_id"] == "blob-README.md"
    assert readme.metadata["size"] > 0


def test_gitlab_incremental_via_cursor(gitlab_mock):
    from chunkshop_connectors.gitlab import factory

    src = factory(gitlab_mock.valid_config)
    src._transport = gitlab_mock.transport
    src._reset_client()

    cursor = src.empty_cursor()
    assert cursor == {}
    with pytest.warns(UserWarning, match="binary"):
        docs = list(src.iter_changes_since(cursor))
    assert len(docs) == 3
    advanced = dict(cursor)
    for d in docs:
        advanced.update(src.cursor_from(d))
    assert advanced == {"after_commit_sha": gitlab_mock.head_sha}

    # Simulate a new commit.
    gitlab_mock.add_commit(
        new_head_sha="NEW_HEAD_SHA",
        changed_files=[("notes.md", b"# Notes\nadded later")],
    )

    src2 = factory(gitlab_mock.valid_config)
    src2._transport = gitlab_mock.transport
    src2._reset_client()
    new_docs = list(src2.iter_changes_since(advanced))
    assert len(new_docs) == 1
    assert new_docs[0].id == "notes.md"
    assert "added later" in new_docs[0].content


def test_gitlab_satisfies_incremental_helpers(gitlab_mock):
    import warnings

    from chunkshop.testing import (
        assert_cursor_advances,
        assert_idempotent_on_re_emit,
    )
    from chunkshop_connectors.gitlab import factory

    src = factory(gitlab_mock.valid_config)
    src._transport = gitlab_mock.transport
    src._reset_client()
    assert isinstance(src, IncrementalSource)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert_cursor_advances(src)

    src2 = factory(gitlab_mock.valid_config)
    src2._transport = gitlab_mock.transport
    src2._reset_client()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert_idempotent_on_re_emit(src2)


def test_gitlab_paths_glob_filter(gitlab_mock):
    from chunkshop_connectors.gitlab import factory
    cfg = dict(gitlab_mock.valid_config)
    cfg["paths_glob"] = ["**/*.md", "*.md"]
    src = factory(cfg)
    src._transport = gitlab_mock.transport
    src._reset_client()
    docs = list(src.iter_documents())
    paths = sorted(d.id for d in docs)
    # src/a.py and assets/logo.png filtered out.
    assert paths == ["README.md", "docs/b.md"]


def test_gitlab_token_from_env(gitlab_mock, monkeypatch):
    """Connector without explicit token reads ``GITLAB_TOKEN``."""
    from chunkshop_connectors.gitlab import factory
    cfg = dict(gitlab_mock.valid_config)
    cfg.pop("token", None)
    monkeypatch.setenv("GITLAB_TOKEN", "env-glpat-xyz")
    src = factory(cfg)
    src._transport = gitlab_mock.transport
    src._reset_client()
    with pytest.warns(UserWarning, match="binary"):
        docs = list(src.iter_documents())
    assert len(docs) >= 1
    assert "env-glpat-xyz" in gitlab_mock.seen_tokens


def test_gitlab_handles_pagination(gitlab_mock):
    """Seed enough files to force tree pagination."""
    from chunkshop_connectors.gitlab import factory

    # Default fixture: 4 files (one binary). Add 120 more so the tree
    # endpoint spans multiple per_page=100 windows.
    for i in range(120):
        gitlab_mock.files[f"bulk/{i:03d}.md"] = f"file {i}".encode("utf-8")

    src = factory(gitlab_mock.valid_config)
    src._transport = gitlab_mock.transport
    src._reset_client()
    with pytest.warns(UserWarning, match="binary"):
        docs = list(src.iter_documents())
    bulk_count = sum(1 for d in docs if d.id.startswith("bulk/"))
    assert bulk_count == 120
