"""Hermetic GitLab REST v4 mock for the gitlab connector.

Uses ``httpx.MockTransport`` (in-process, no socket) so connector
tests never touch the live GitLab API.

Endpoints stubbed
-----------------
* ``GET /projects/{project}/repository/commits/{branch}`` — head SHA.
* ``GET /projects/{project}/repository/tree`` — paginated file list.
* ``GET /projects/{project}/repository/files/{path}`` — file content.
* ``GET /projects/{project}/repository/compare`` — diff between SHAs.
"""
from __future__ import annotations

import base64
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest


_DEFAULT_FILES: dict[str, bytes] = {
    "README.md": b"# repo\n\nhello chunkshop from gitlab",
    "src/a.py": b"print('hello from a')\n",
    "docs/b.md": b"# Section B\nbody body body",
    # Binary blob; connector skips it on UTF-8 decode failure.
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff\xfe",
}


class _GitLabMockHandle:
    """In-memory GitLab repo + the ``httpx.MockTransport`` driving it."""

    def __init__(
        self, *, project: str = "acme/widgets", branch: str = "main"
    ) -> None:
        self.project = project
        self.branch = branch
        self.head_sha = "HEAD_SHA_0"
        self.files: dict[str, bytes] = dict(_DEFAULT_FILES)
        # parent_sha → (new_head_sha, [changed_paths])
        self.commit_history: dict[str, tuple[str, list[str]]] = {}
        self.seen_tokens: set[str] = set()

        self.transport = httpx.MockTransport(self._dispatch)

        self.valid_config: dict[str, Any] = {
            "project": project,
            "branch": branch,
            "token": "glpat-test-pat",
            "base_url": "https://gitlab.mock/api/v4",
        }

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def add_commit(
        self, *, new_head_sha: str, changed_files: list[tuple[str, bytes]]
    ) -> None:
        parent = self.head_sha
        paths: list[str] = []
        for path, body in changed_files:
            self.files[path] = body
            paths.append(path)
        self.commit_history[parent] = (new_head_sha, paths)
        self.head_sha = new_head_sha

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------
    def _record_token(self, request: httpx.Request) -> None:
        priv = request.headers.get("private-token") or request.headers.get(
            "PRIVATE-TOKEN"
        )
        if priv:
            self.seen_tokens.add(priv)

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self._record_token(request)
        url = urlparse(str(request.url))
        path = url.path
        qs = parse_qs(url.query)

        # /projects/{project_enc}/repository/commits/{branch}
        if "/repository/commits/" in path and request.method == "GET":
            branch = path.rsplit("/", 1)[-1]
            if branch != self.branch:
                return httpx.Response(404, json={"message": "404 Branch Not Found"})
            return httpx.Response(
                200,
                json={"id": self.head_sha, "short_id": self.head_sha[:8]},
            )

        # /projects/{project_enc}/repository/tree
        if path.endswith("/repository/tree") and request.method == "GET":
            return self._handle_tree(qs)

        # /projects/{project_enc}/repository/files/{path_enc}
        if "/repository/files/" in path and request.method == "GET":
            return self._handle_file(path, qs)

        # /projects/{project_enc}/repository/compare
        if path.endswith("/repository/compare") and request.method == "GET":
            return self._handle_compare(qs)

        return httpx.Response(404, text=f"no mock for {request.method} {path}")

    # ---- handlers -----------------------------------------------------
    def _handle_tree(self, qs: dict[str, list[str]]) -> httpx.Response:
        page = int(qs.get("page", ["1"])[0])
        per_page = int(qs.get("per_page", ["100"])[0])
        # Flat list of every file (recursive=true is implied; we don't
        # bother modelling directory-only entries).
        all_items = [
            {
                "id": f"blob-{path}",
                "name": path.rsplit("/", 1)[-1],
                "type": "blob",
                "path": path,
                "mode": "100644",
            }
            for path in self.files.keys()
        ]
        start = (page - 1) * per_page
        end = start + per_page
        chunk = all_items[start:end]
        next_page = page + 1 if end < len(all_items) else ""
        return httpx.Response(
            200,
            json=chunk,
            headers={"X-Next-Page": str(next_page)} if next_page else {},
        )

    def _handle_file(
        self, path: str, qs: dict[str, list[str]]
    ) -> httpx.Response:
        # path looks like /projects/{enc}/repository/files/{enc_path}
        file_path_enc = path.split("/repository/files/", 1)[1]
        file_path = unquote(file_path_enc)
        body = self.files.get(file_path)
        if body is None:
            return httpx.Response(404, json={"message": "404 File Not Found"})
        return httpx.Response(
            200,
            json={
                "file_name": file_path.rsplit("/", 1)[-1],
                "file_path": file_path,
                "size": len(body),
                "encoding": "base64",
                "content_sha256": "fake-sha256",
                "ref": qs.get("ref", [""])[0],
                "blob_id": f"blob-{file_path}",
                "commit_id": self.head_sha,
                "last_commit_id": self.head_sha,
                "content": base64.b64encode(body).decode("ascii"),
            },
        )

    def _handle_compare(self, qs: dict[str, list[str]]) -> httpx.Response:
        base_sha = qs.get("from", [""])[0]
        if base_sha not in self.commit_history:
            return httpx.Response(200, json={"commits": [], "diffs": []})
        _, paths = self.commit_history[base_sha]
        diffs = [
            {
                "old_path": p,
                "new_path": p,
                "new_file": True,
                "renamed_file": False,
                "deleted_file": False,
            }
            for p in paths
        ]
        return httpx.Response(200, json={"commits": [], "diffs": diffs})


def make_gitlab_mock() -> _GitLabMockHandle:
    handle = _GitLabMockHandle()
    return handle


@pytest.fixture
def gitlab_mock():
    return make_gitlab_mock()


__all__ = ["gitlab_mock", "make_gitlab_mock"]
