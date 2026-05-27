"""Behavioural tests for the verified github connector.

Hermetic — ``github_mock`` spins up a ``pytest_httpserver`` HTTP
server on localhost and pre-wires the GitHub REST endpoints the
connector consumes. The connector's ``base_url`` is pointed at the
local server, so no live network egress happens.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from chunkshop.sources import registry
from chunkshop.sources.base import IncrementalSource

from chunkshop_connectors._tier import tier_of


def _make_local_repo(tmp_path, files: dict[str, bytes], branch: str = "main"):
    """Build a real local git repo to serve as a clone remote (hermetic)."""
    repo = tmp_path / "remote"
    repo.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    run("init")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    for path, body in files.items():
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
    run("add", "-A")
    run("commit", "-m", "init")
    run("branch", "-M", branch)
    return repo


requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary not available"
)


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


def test_github_auto_detects_default_branch(github_mock):
    """No branch pinned → connector resolves the repo's default_branch (#27)."""
    from chunkshop_connectors.github import factory
    github_mock.default_branch = "master"  # repo default is NOT 'main'
    cfg = dict(github_mock.valid_config)
    cfg.pop("branch", None)  # caller didn't think about the branch
    src = factory(cfg)
    docs = list(src.iter_documents())
    assert sorted(d.id for d in docs) == ["README.md", "docs/b.md", "src/a.py"]
    # Resolved branch is baked into Document metadata.
    assert docs[0].metadata["branch"] == "master"


def test_github_pinned_branch_404_falls_back_to_default(github_mock):
    """A wrong pinned branch 404s, then falls back to the repo default (#27)."""
    from chunkshop_connectors.github import factory
    github_mock.default_branch = "main"  # real default
    cfg = dict(github_mock.valid_config)
    cfg["branch"] = "does-not-exist"  # caller guessed wrong
    src = factory(cfg)
    docs = list(src.iter_documents())  # would 404 without fallback
    assert sorted(d.id for d in docs) == ["README.md", "docs/b.md", "src/a.py"]
    assert docs[0].metadata["branch"] == "main"


def test_github_branch_strict_raises_on_missing(github_mock):
    """branch_strict=True turns a missing pinned branch into a hard error (#27)."""
    import httpx
    from chunkshop_connectors.github import factory
    cfg = dict(github_mock.valid_config)
    cfg["branch"] = "does-not-exist"
    cfg["branch_strict"] = True
    src = factory(cfg)
    with pytest.raises(httpx.HTTPStatusError):
        list(src.iter_documents())


@requires_git
def test_github_clone_walk_yields_documents(github_mock, tmp_path, monkeypatch):
    """clone=True walks a shallow clone locally instead of per-file API calls (#28)."""
    from chunkshop_connectors.github import factory
    remote = _make_local_repo(tmp_path, {
        "README.md": b"# repo\nhello chunkshop",
        "src/a.py": b"print('hello from a')\n",
        "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff\xfe",  # binary
    })
    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    src = factory(cfg)
    monkeypatch.setattr(src, "_clone_url", lambda: remote.as_uri())
    docs = list(src.iter_documents())
    # Binary file skipped; tracked text files yielded with parity metadata.
    assert sorted(d.id for d in docs) == ["README.md", "src/a.py"]
    readme = next(d for d in docs if d.id == "README.md")
    assert "hello chunkshop" in readme.content
    assert readme.metadata["branch"] == "main"
    assert readme.metadata["sha"]  # git blob sha from ls-tree
    assert readme.metadata["size"] > 0


@requires_git
def test_github_clone_respects_paths_glob(github_mock, tmp_path, monkeypatch):
    from chunkshop_connectors.github import factory
    remote = _make_local_repo(tmp_path, {
        "README.md": b"# readme",
        "src/a.py": b"x = 1\n",
        "docs/b.md": b"# b",
    })
    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    cfg["paths_glob"] = ["**/*.md", "*.md"]
    src = factory(cfg)
    monkeypatch.setattr(src, "_clone_url", lambda: remote.as_uri())
    paths = sorted(d.id for d in src.iter_documents())
    assert paths == ["README.md", "docs/b.md"]


@requires_git
def test_github_clone_default_branch_fallback(github_mock, tmp_path, monkeypatch):
    """A wrong pinned branch retries cloning the default branch (#27 + #28)."""
    from chunkshop_connectors.github import factory
    remote = _make_local_repo(tmp_path, {"README.md": b"# r"}, branch="master")
    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    cfg["branch"] = "does-not-exist"
    src = factory(cfg)
    monkeypatch.setattr(src, "_clone_url", lambda: remote.as_uri())
    docs = list(src.iter_documents())
    assert [d.id for d in docs] == ["README.md"]


@requires_git
def test_github_clone_size_limit(github_mock, tmp_path, monkeypatch):
    from chunkshop_connectors.github import factory
    remote = _make_local_repo(tmp_path, {"README.md": b"# r\nbody"})
    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    cfg["max_clone_mb"] = 0  # anything exceeds → refuse
    src = factory(cfg)
    monkeypatch.setattr(src, "_clone_url", lambda: remote.as_uri())
    with pytest.raises(RuntimeError, match="max_clone_mb"):
        list(src.iter_documents())


def test_github_clone_falls_back_to_rest_without_git(github_mock, monkeypatch):
    """clone=True but no git binary → warn and use the REST walk."""
    import chunkshop_connectors.github.connector as conn_mod
    from chunkshop_connectors.github import factory
    monkeypatch.setattr(conn_mod.shutil, "which", lambda _name: None)
    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    src = factory(cfg)
    with pytest.warns(UserWarning, match="git.*unavailable"):
        docs = list(src.iter_documents())
    assert sorted(d.id for d in docs) == ["README.md", "docs/b.md", "src/a.py"]


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


def test_scrub_url_token_replaces_inlined_credentials():
    """The scrubber redacts inlined https credentials, keeping the rest intact."""
    from chunkshop_connectors.github.connector import _scrub_url_token
    fake = "ghp_FAKE0000TESTTOKEN"
    out = _scrub_url_token(
        f"fatal: unable to access 'https://{fake}@github.com/o/r.git': 401"
    )
    assert fake not in out
    assert "https://***@github.com/o/r.git" in out


def test_git_failure_scrubs_pat_from_exception(github_mock, monkeypatch):
    """A failing git call must not leak the inlined PAT via CalledProcessError.

    Regression for #31 (PAT observed in container logs after a clone failure).
    Hermetic: subprocess.run is stubbed to return a failing result whose argv
    and stderr carry a fake token; the raised exception must be scrubbed.
    """
    from chunkshop_connectors.github import factory
    import chunkshop_connectors.github.connector as conn_mod

    src = factory(github_mock.valid_config)
    fake = "ghp_FAKE0000TESTTOKEN"
    url = f"https://{fake}@github.com/o/r.git"

    def _fake_run(argv, *a, **kw):
        return subprocess.CompletedProcess(
            argv, returncode=128, stdout="",
            stderr=f"fatal: unable to access '{url}': The requested URL returned error: 401",
        )

    monkeypatch.setattr(conn_mod.subprocess, "run", _fake_run)

    with pytest.raises(subprocess.CalledProcessError) as ei:
        src._git("clone", url, "/tmp/does-not-matter")
    exc = ei.value
    # Token gone from every surface CalledProcessError / loggers expose.
    assert fake not in str(exc)
    assert fake not in " ".join(str(a) for a in exc.cmd)
    assert fake not in (exc.stderr or "")
    # …and the sanitized placeholder is present so the error stays diagnosable.
    assert "***" in " ".join(str(a) for a in exc.cmd)
    assert "***" in (exc.stderr or "")
