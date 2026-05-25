"""Behavioural tests for the verified github connector.

Hermetic — ``github_mock`` spins up a ``pytest_httpserver`` HTTP
server on localhost and pre-wires the GitHub REST endpoints the
connector consumes. The connector's ``base_url`` is pointed at the
local server, so no live network egress happens.
"""
from __future__ import annotations

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def test_github_registered_and_verified():
    registry.clear_cache()
    assert "github" in registry.available_connectors()
    from chunkshop_connectors.github import Connector
    assert tier_of(Connector) == "verified"


def test_github_config_validation_rejects_bad():
    from chunkshop_connectors.github import ConfigModel
    # missing required `owner` and `repo`
    with pytest.raises(Exception):
        ConfigModel.model_validate({})
    # type-wrong owner
    with pytest.raises(Exception):
        ConfigModel.model_validate({"owner": 42, "repo": "r"})
    # extra='forbid'
    with pytest.raises(Exception):
        ConfigModel.model_validate({"owner": "o", "repo": "r", "ownr": "typo"})


def test_github_yields_documents_against_mock(github_mock):
    from chunkshop_connectors.github import factory
    src = factory(github_mock.valid_config)
    docs = list(src.iter_documents())
    # default fixture: README.md, src/a.py, docs/b.md  (binary skipped)
    paths = sorted(d.id for d in docs)
    assert paths == ["README.md", "docs/b.md", "src/a.py"]
    # Document shape — all use file path as title and id
    readme = next(d for d in docs if d.id == "README.md")
    assert readme.title == "README.md"
    assert "hello chunkshop" in readme.content
    assert readme.metadata is not None
    assert readme.metadata["path"] == "README.md"
    assert readme.metadata["branch"] == "main"
    assert readme.metadata["sha"] == "sha-README.md"
    assert readme.metadata["size"] > 0


def test_github_incremental_via_commit_sha(github_mock):
    from chunkshop_connectors.github import factory
    src = factory(github_mock.valid_config)

    # First sync — empty cursor → all files
    cursor = src.empty_cursor()
    assert cursor == {}
    docs = list(src.iter_changes_since(cursor))
    assert len(docs) == 3  # README.md, src/a.py, docs/b.md
    # Build the advanced cursor the way the consumer would.
    advanced = dict(cursor)
    for d in docs:
        advanced.update(src.cursor_from(d))
    assert advanced == {"after_commit_sha": github_mock.head_sha}

    # Simulate a new commit landing on the branch with a new file.
    github_mock.add_commit(
        new_head_sha="NEW_HEAD_SHA",
        changed_files=[("notes.md", b"# Notes\nadded later")],
    )

    # Second sync — should pick up just the new file.
    src2 = factory(github_mock.valid_config)
    new_docs = list(src2.iter_changes_since(advanced))
    assert len(new_docs) == 1
    assert new_docs[0].id == "notes.md"
    assert "added later" in new_docs[0].content


def test_github_satisfies_incremental_helpers(github_mock):
    from chunkshop.testing import (
        assert_cursor_advances,
        assert_idempotent_on_re_emit,
    )
    from chunkshop_connectors.github import factory

    src = factory(github_mock.valid_config)
    assert isinstance(src, IncrementalSource)
    assert_cursor_advances(src)

    src2 = factory(github_mock.valid_config)
    assert_idempotent_on_re_emit(src2)


def test_github_paths_glob_filter(github_mock):
    from chunkshop_connectors.github import factory
    cfg = dict(github_mock.valid_config)
    cfg["paths_glob"] = ["**/*.md", "*.md"]
    src = factory(cfg)
    docs = list(src.iter_documents())
    paths = sorted(d.id for d in docs)
    assert paths == ["README.md", "docs/b.md"]  # src/a.py filtered out


def test_github_binary_files_skipped(github_mock):
    """The default fixture already includes a binary file; verify it's silently dropped."""
    from chunkshop_connectors.github import factory
    src = factory(github_mock.valid_config)
    with pytest.warns(UserWarning, match="binary"):
        docs = list(src.iter_documents())
    paths = {d.id for d in docs}
    assert "assets/logo.png" not in paths


def test_github_pat_from_env(github_mock, monkeypatch):
    """Connector without explicit token reads ${GITHUB_TOKEN}."""
    from chunkshop_connectors.github import factory
    cfg = dict(github_mock.valid_config)
    cfg.pop("token", None)
    monkeypatch.setenv("GITHUB_TOKEN", "env-pat-xyz")
    src = factory(cfg)
    docs = list(src.iter_documents())
    # iter succeeded → token was resolved from env
    assert len(docs) >= 1
    # And the mock saw the env token in Authorization header
    assert "env-pat-xyz" in github_mock.seen_tokens
