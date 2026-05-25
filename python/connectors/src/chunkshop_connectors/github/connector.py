"""Verified GitHub connector (PAT auth).

This connector walks a GitHub repository at a given branch via the
REST API and yields one chunkshop ``Document`` per text file. Auth
is PAT-only — Personal Access Token, supplied either by config or
by the ``GITHUB_TOKEN`` env var. OAuth is out of scope for this
tier (we leave that to ``chunkshop_connectors.gdrive`` / ``slack``
which need it).

Endpoints consumed
------------------
* ``GET /repos/{owner}/{repo}/branches/{branch}``
    Resolves the current head SHA of the branch.
* ``GET /repos/{owner}/{repo}/git/trees/{branch_sha}?recursive=1``
    Lists every blob (file) reachable from the head tree in one call.
* ``GET /repos/{owner}/{repo}/contents/{path}?ref={branch_sha}``
    Fetches each file's body (base64-encoded).
* ``GET /repos/{owner}/{repo}/compare/{old_sha}...{branch}``
    Used by ``iter_changes_since`` to find files touched since the
    consumer's last cursor.

Sync semantics
--------------
``sync_mode`` is ``CURSOR``. The cursor shape is::

    {"after_commit_sha": "<sha>"}

* Empty cursor → full sync, then advances to the current head SHA.
* Non-empty cursor → call ``/compare`` with ``base = after_commit_sha``
  and emit only the files in the diff (added / modified). Deletions
  are not emitted as Documents — chunkshop has no PrunableSource
  contract on this connector. Document deletions belong to a future
  follow-up (#TBD).

Binary files
------------
GitHub doesn't tell us a file's MIME type cheaply, so we try to
UTF-8 decode the body. On ``UnicodeDecodeError`` the file is
skipped silently with a ``warnings.warn(..., UserWarning)``. This
matches RAGFlow / Onyx behaviour for "best-effort text ingest" and
keeps a single image in a repo from killing the whole sync.

StaleCursorError
----------------
If GitHub returns 422 from the ``/compare`` endpoint (the
documented "cannot compare these refs" status, raised when one
side has been force-pushed away or the branch deleted), the
connector raises ``chunkshop.sources.base.StaleCursorError``. The
consumer should treat that as "drop the cursor, fall back to a
full resync."
"""
from __future__ import annotations

import base64
import fnmatch
import logging
import os
import warnings
from typing import Any, Iterable, Iterator, Optional

from chunkshop.sources.base import Document, StaleCursorError, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


@verified
class GitHubConnector:
    """Verified-tier GitHub repo connector (cursor sync, PAT auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        # ConfigModel validation has already happened in the factory.
        self.owner: str = config["owner"]
        self.repo: str = config["repo"]
        self.branch: str = config.get("branch", "main")
        self.paths_glob: Optional[list[str]] = config.get("paths_glob")
        self._explicit_token: Optional[str] = config.get("token")
        self.base_url: str = config.get("base_url", "https://api.github.com").rstrip("/")

        # Lazy httpx import — installable as the [github] extra. Users
        # who only `import chunkshop_connectors.github` to inspect
        # tier/registry shouldn't pay the cost.
        import httpx  # noqa: PLC0415

        self._httpx = httpx
        # One client per connector instance; closed by GC. Short-lived
        # in test runs, long-lived in orchestrators — httpx.Client is
        # connection-pooled internally.
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _resolve_token(self) -> Optional[str]:
        """Resolve PAT lazily on each request.

        Precedence: explicit config token > ``GITHUB_TOKEN`` env var.
        Returns ``None`` if neither is set — the connector still works
        against public repos and unauth'd mocks, just with the
        anonymous rate limit.
        """
        if self._explicit_token:
            return self._explicit_token
        return os.environ.get("GITHUB_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        token = self._resolve_token()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        return headers

    def __repr__(self) -> str:  # pragma: no cover -- defensive
        # Redact token so it can never leak via logs.
        return (
            f"GitHubConnector(owner={self.owner!r}, repo={self.repo!r}, "
            f"branch={self.branch!r}, token={'***' if self._explicit_token else None})"
        )

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------
    def _get_json(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, headers=self._auth_headers(), params=params)
        if resp.status_code == 422 and "/compare/" in path:
            raise StaleCursorError(
                f"github /compare returned 422 for {path}; the cursor refers "
                f"to a SHA no longer reachable from {self.branch}. "
                f"Drop the cursor and resync."
            )
        resp.raise_for_status()
        return resp.json()

    def _head_sha(self) -> str:
        data = self._get_json(f"/repos/{self.owner}/{self.repo}/branches/{self.branch}")
        return data["commit"]["sha"]

    def _list_tree(self, branch_sha: str) -> list[dict[str, Any]]:
        data = self._get_json(
            f"/repos/{self.owner}/{self.repo}/git/trees/{branch_sha}",
            recursive=1,
        )
        # Filter to blobs (i.e. files). Trees (subdirs) are returned with
        # type=="tree"; we don't need them since recursive=1 flattens.
        return [item for item in data.get("tree", []) if item.get("type") == "blob"]

    def _fetch_content(self, path: str, ref: str) -> tuple[Optional[str], dict[str, Any]]:
        """Fetch a file's body and metadata.

        Returns ``(content, meta)`` where ``content`` is the decoded
        UTF-8 text or ``None`` for binary. ``meta`` always carries
        ``sha`` and ``size``.
        """
        data = self._get_json(
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            ref=ref,
        )
        encoding = data.get("encoding", "base64")
        raw = data.get("content", "")
        if encoding == "base64":
            # GitHub line-wraps base64 every 60 chars; ``base64.b64decode``
            # tolerates whitespace.
            blob = base64.b64decode(raw)
        else:
            # Some endpoints return content already decoded (e.g.
            # ``encoding == "none"`` for empty files).
            blob = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)

        meta = {"sha": data.get("sha"), "size": data.get("size", len(blob))}

        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"github: skipping binary file {path!r} (not valid UTF-8)",
                UserWarning,
                stacklevel=3,
            )
            return None, meta
        return content, meta

    def _matches_glob(self, path: str) -> bool:
        if not self.paths_glob:
            return True
        # ``fnmatch`` doesn't understand ``**`` natively — it treats it
        # the same as ``*``. We expand each pattern: ``**/*.md`` becomes
        # "either ``*.md`` (top-level) or ``*/*.md`` / ``*/*/*.md`` etc."
        # The cheap and correct way is to walk the path's parents and
        # check fnmatch at every depth — but the standard recipe is to
        # split the pattern at ``**`` and check the suffix matches via
        # fnmatch and the path's segment count satisfies the depth.
        for pattern in self.paths_glob:
            if _glob_match(path, pattern):
                return True
        return False

    # ------------------------------------------------------------------
    # Document generation
    # ------------------------------------------------------------------
    def _make_document(
        self, path: str, content: str, file_sha: str, size: int, branch_sha: str
    ) -> Document:
        return Document(
            id=path,
            content=content,
            title=path,
            metadata={
                "path": path,
                "size": size,
                "sha": file_sha,
                "branch": self.branch,
                # branch_sha is *load-bearing* — cursor_from() reads it
                # to advance the cursor. Document is frozen, so we have
                # to bake it in here rather than mutate post-yield.
                "branch_sha": branch_sha,
            },
        )

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        branch_sha = self._head_sha()
        for entry in self._list_tree(branch_sha):
            path = entry["path"]
            if not self._matches_glob(path):
                continue
            content, meta = self._fetch_content(path, ref=branch_sha)
            if content is None:
                # binary — already warned
                continue
            yield self._make_document(
                path=path,
                content=content,
                file_sha=meta["sha"] or entry.get("sha", ""),
                size=meta["size"],
                branch_sha=branch_sha,
            )

    # ---- IncrementalSource ------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        # First-run shortcut: no prior cursor → full sync.
        prior = cursor.get("after_commit_sha")
        if not prior:
            yield from self.iter_documents()
            return

        branch_sha = self._head_sha()
        if prior == branch_sha:
            # Cursor already at HEAD; no work.
            return

        diff = self._get_json(
            f"/repos/{self.owner}/{self.repo}/compare/{prior}...{branch_sha}"
        )
        for file_entry in diff.get("files", []):
            status = file_entry.get("status")
            if status == "removed":
                # No PrunableSource contract here; just skip.
                continue
            path = file_entry.get("filename")
            if not path or not self._matches_glob(path):
                continue
            content, meta = self._fetch_content(path, ref=branch_sha)
            if content is None:
                continue
            yield self._make_document(
                path=path,
                content=content,
                file_sha=meta["sha"] or "",
                size=meta["size"],
                branch_sha=branch_sha,
            )

    def cursor_from(self, last_document: Document) -> dict:
        # Monotonic cursor: every doc in a sync carries the same
        # branch_sha, and merging identical values is a no-op. The
        # final cursor reflects the head we saw at sync time.
        meta = last_document.metadata or {}
        sha = meta.get("branch_sha")
        if sha is None:
            return {}
        return {"after_commit_sha": sha}


# ----------------------------------------------------------------------
# Glob matcher
# ----------------------------------------------------------------------
def _glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against a glob pattern that may include ``**``.

    Standard ``fnmatch`` treats ``*`` and ``**`` identically and
    doesn't respect path separators, so e.g. ``fnmatch("a/b.md",
    "*.md")`` returns True (wrong — we want segment-aware matching).
    ``pathlib.PurePath.match`` is closer but still has surprising
    behaviour across versions. The recipe below uses
    ``fnmatch.translate`` on segment-by-segment patterns built from
    the user input.
    """
    if "**" not in pattern:
        # Single-level: each segment of the pattern must fnmatch its
        # corresponding path segment.
        path_parts = path.split("/")
        pat_parts = pattern.split("/")
        if len(path_parts) != len(pat_parts):
            return False
        return all(fnmatch.fnmatchcase(p, q) for p, q in zip(path_parts, pat_parts))

    # With ``**``: split once on ``**`` and require:
    #   prefix segments fnmatch the leading path segments
    #   suffix segments fnmatch the trailing path segments
    #   middle (any depth, including zero) is unconstrained.
    pat_parts = pattern.split("/")
    star_idx = pat_parts.index("**")
    prefix = pat_parts[:star_idx]
    suffix = pat_parts[star_idx + 1:]

    path_parts = path.split("/")
    # need at least len(prefix)+len(suffix) segments
    if len(path_parts) < len(prefix) + len(suffix):
        return False

    head = path_parts[: len(prefix)]
    tail = path_parts[len(path_parts) - len(suffix) :] if suffix else []

    if not all(fnmatch.fnmatchcase(p, q) for p, q in zip(head, prefix)):
        return False
    if suffix and not all(fnmatch.fnmatchcase(p, q) for p, q in zip(tail, suffix)):
        return False
    return True
