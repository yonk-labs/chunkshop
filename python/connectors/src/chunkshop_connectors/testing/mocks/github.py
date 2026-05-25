"""Hermetic GitHub REST mock for the github connector.

Uses ``pytest_httpserver`` (already a dev dep of the connectors
package) to spin up a local HTTP server on a random port and wire
just enough of the GitHub REST API to drive the connector through
its happy paths:

  * ``GET /repos/{owner}/{repo}/branches/{branch}``
      → ``{"commit": {"sha": HEAD_SHA}}``
  * ``GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1``
      → tree of all seeded files
  * ``GET /repos/{owner}/{repo}/contents/{path}?ref={ref}``
      → ``{"content": base64(body), "encoding": "base64",
            "sha": "sha-<path>", "size": N, "type": "file"}``
  * ``GET /repos/{owner}/{repo}/compare/{old}...{new}``
      → ``{"files": [{"filename": p, "status": "added|modified|removed"}]}``

The connector consumes these endpoints via ``base_url`` in its
config, which the fixture overrides to point at the local
``httpserver``.

No real network — the bound interface is loopback only.
"""
from __future__ import annotations

import base64
import re
from typing import Any

# pytest + werkzeug are gated so this module imports cleanly on a fresh
# `pip install chunkshop-connectors[github]` (without dev extras). The
# bare classes/handlers below are usable from non-pytest contexts; only
# the `github_mock` fixture itself needs the test deps.
try:
    import pytest  # noqa: F401
    from werkzeug.wrappers import Request, Response  # noqa: F401
    _HAS_PYTEST = True
except ImportError:
    pytest = None  # type: ignore[assignment]
    Request = Response = None  # type: ignore[assignment,misc]
    _HAS_PYTEST = False


# Default repo fixture content. Keys are file paths, values are raw bytes.
_DEFAULT_FILES: dict[str, bytes] = {
    "README.md": b"# repo\n\nhello chunkshop",
    "src/a.py": b"print('hello from a')\n",
    "docs/b.md": b"# Section B\nbody body body",
    # Binary blob — invalid UTF-8 (PNG header bytes). Connector skips it.
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff\xfe",
}


class _GitHubMockHandle:
    """Test-facing handle for the mock GitHub server.

    Attributes
    ----------
    valid_config
        Connector config dict ready to feed ``factory()``. ``base_url``
        is set to the local ``httpserver`` base URL.
    head_sha
        Current head SHA the mock will report for the branch.
    files
        ``{path: bytes}`` map of tracked files. Mutating it post-init
        immediately affects subsequent connector calls.
    commit_history
        List of ``(parent_sha, head_sha, [changed_paths])`` for the
        ``/compare`` endpoint to consult.
    seen_tokens
        Set of bearer tokens the connector has presented in the
        ``Authorization`` header — tests can assert env-token plumbing.
    """

    def __init__(self, httpserver, owner: str, repo: str, branch: str):
        self.httpserver = httpserver
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.head_sha = "HEAD_SHA_0"
        self.files: dict[str, bytes] = dict(_DEFAULT_FILES)
        # parent_sha → (new_head_sha, [changed_paths])
        self.commit_history: dict[str, tuple[str, list[str]]] = {}
        self.seen_tokens: set[str] = set()

        self.valid_config: dict[str, Any] = {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "token": "test-pat-abc",
            "base_url": httpserver.url_for("").rstrip("/"),
        }

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def add_commit(self, *, new_head_sha: str, changed_files: list[tuple[str, bytes]]):
        """Simulate a new commit landing on the branch.

        Records the diff between the prior head and ``new_head_sha``
        so the ``/compare`` endpoint can serve it, then advances
        ``head_sha``.
        """
        parent = self.head_sha
        paths: list[str] = []
        for path, body in changed_files:
            self.files[path] = body
            paths.append(path)
        self.commit_history[parent] = (new_head_sha, paths)
        self.head_sha = new_head_sha


def _record_token(handle: _GitHubMockHandle, request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    # GitHub accepts both "token <pat>" and "Bearer <pat>". Strip both.
    for prefix in ("token ", "Bearer "):
        if auth.startswith(prefix):
            handle.seen_tokens.add(auth[len(prefix):])
            return


def _wire_endpoints(handle: _GitHubMockHandle) -> None:
    server = handle.httpserver
    owner, repo, branch = handle.owner, handle.repo, handle.branch

    # ---- branches: returns the current head SHA -----------------------
    def _branch_handler(request: Request) -> Response:
        _record_token(handle, request)
        import json
        return Response(
            json.dumps({"name": branch, "commit": {"sha": handle.head_sha}}),
            status=200,
            content_type="application/json",
        )

    owner_q = re.escape(owner)
    repo_q = re.escape(repo)
    branch_q = re.escape(branch)

    server.expect_request(
        re.compile(rf"^/repos/{owner_q}/{repo_q}/branches/{branch_q}$"), method="GET"
    ).respond_with_handler(_branch_handler)

    # ---- git/trees: tree of the entire repo at the current head -------
    def _tree_handler(request: Request) -> Response:
        _record_token(handle, request)
        import json
        tree = [
            {"path": path, "type": "blob", "sha": f"sha-{path}", "size": len(body)}
            for path, body in handle.files.items()
        ]
        return Response(
            json.dumps({"sha": handle.head_sha, "tree": tree, "truncated": False}),
            status=200,
            content_type="application/json",
        )

    # The tree URL includes the head SHA. We match anything under
    # ``/git/trees/`` with a regex so the SHA can vary between syncs.
    server.expect_request(
        re.compile(rf"^/repos/{owner_q}/{repo_q}/git/trees/[^/]+$"), method="GET"
    ).respond_with_handler(_tree_handler)

    # ---- contents/{path} ---------------------------------------------
    def _contents_handler(request: Request) -> Response:
        _record_token(handle, request)
        import json
        # extract the path after the /contents/ prefix
        path = request.path.split(f"/repos/{owner}/{repo}/contents/", 1)[1]
        body = handle.files.get(path)
        if body is None:
            return Response("not found", status=404)
        return Response(
            json.dumps({
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "sha": f"sha-{path}",
                "size": len(body),
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(body).decode("ascii"),
            }),
            status=200,
            content_type="application/json",
        )

    server.expect_request(
        re.compile(rf"^/repos/{owner_q}/{repo_q}/contents/.+$"), method="GET"
    ).respond_with_handler(_contents_handler)

    # ---- compare/{base}...{head} -------------------------------------
    def _compare_handler(request: Request) -> Response:
        _record_token(handle, request)
        import json
        # /repos/owner/repo/compare/{base}...{head}
        tail = request.path.split(f"/repos/{owner}/{repo}/compare/", 1)[1]
        base_sha, _, _head_sha = tail.partition("...")
        if base_sha not in handle.commit_history:
            # No history recorded → empty diff (cursor caught up).
            return Response(
                json.dumps({"files": [], "status": "identical"}),
                status=200,
                content_type="application/json",
            )
        _, paths = handle.commit_history[base_sha]
        files = [{"filename": p, "status": "added"} for p in paths]
        return Response(
            json.dumps({"files": files, "status": "ahead"}),
            status=200,
            content_type="application/json",
        )

    server.expect_request(
        re.compile(rf"^/repos/{owner_q}/{repo_q}/compare/.+$"), method="GET"
    ).respond_with_handler(_compare_handler)


if _HAS_PYTEST:
    @pytest.fixture
    def github_mock(httpserver):
        """Provide a GitHub REST mock + valid_config for the github connector.

        Default fixture: a tiny repo with README.md, src/a.py, docs/b.md,
        and assets/logo.png (binary, which the connector silently skips).
        """
        handle = _GitHubMockHandle(httpserver, owner="acme", repo="widgets", branch="main")
        _wire_endpoints(handle)
        return handle


__all__ = ["_GitHubMockHandle", "_wire_endpoints"] + (
    ["github_mock"] if _HAS_PYTEST else []
)
