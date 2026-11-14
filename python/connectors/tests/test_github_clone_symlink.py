"""Clone mode must never read symlinks (host-file disclosure, PYS-04)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary not available"
)


@requires_git
def test_github_clone_skips_symlinks(github_mock, tmp_path, monkeypatch):
    from chunkshop_connectors.github import factory

    outside = tmp_path / "outside.txt"
    outside.write_text("HOST SECRET")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "leak.txt").write_text("HOST DIR SECRET")

    repo = tmp_path / "remote"
    repo.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    run("init")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "README.md").write_text("regular repo content")
    (repo / "link.txt").symlink_to(outside)
    (repo / "linkdir").symlink_to(outside_dir)
    run("add", "-A")
    run("commit", "-m", "init")
    run("branch", "-M", "main")

    cfg = dict(github_mock.valid_config)
    cfg["clone"] = True
    src = factory(cfg)
    monkeypatch.setattr(src, "_clone_url", lambda: repo.as_uri())
    docs = list(src.iter_documents())

    assert [d.id for d in docs] == ["README.md"]
    blob = "\n".join(d.content for d in docs)
    assert "regular repo content" in blob
    assert "HOST SECRET" not in blob
    assert "HOST DIR SECRET" not in blob
