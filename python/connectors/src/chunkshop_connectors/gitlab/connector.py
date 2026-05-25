"""Verified GitLab connector (PAT auth).

Walks a GitLab project at a given branch and yields one chunkshop
``Document`` per text file. Auth is PAT-only (Personal Access Token,
supplied via config ``token`` or the ``GITLAB_TOKEN`` env var).
OAuth is out of scope — server-side ingest workloads run with a
long-lived project / group access token.

Endpoints consumed
------------------
* ``GET /projects/{project}/repository/tree?recursive=true&ref={branch}``
    Lists every blob (file) reachable from the branch in one call
    (paginated; we honour ``X-Next-Page``).
* ``GET /projects/{project}/repository/files/{path_encoded}?ref={branch}``
    Fetches a file's content (base64-encoded ``content`` field).
* ``GET /projects/{project}/repository/commits/{branch}``
    Resolves the current head SHA of the branch.
* ``GET /projects/{project}/repository/compare?from={old}&to={new}``
    Diff between two refs; powers ``iter_changes_since``.

The ``project`` segment is URL-encoded — GitLab requires
``namespace%2Fproject`` for ``"namespace/project"``-shaped IDs;
numeric project IDs pass through unchanged.

Sync semantics
--------------
``sync_mode = SyncMode.CURSOR``. The cursor shape is::

    {"after_commit_sha": "<sha>"}

Same shape as the github connector. Empty cursor → full sync to head
SHA. Non-empty → ``GET /repository/compare`` between cursor and
current head, emit changed files (added / modified). Deletions are
not surfaced as Documents — this connector does not implement
``PrunableSource``.

Binary files
------------
GitLab's tree endpoint doesn't expose MIME type. Same policy as the
github connector: try UTF-8 decode; on failure emit a
``UserWarning`` and skip.
"""
from __future__ import annotations

import base64
import fnmatch
import logging
import os
import warnings
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import quote

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


@verified
class GitLabConnector:
    """Verified-tier GitLab project connector (cursor sync, PAT auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        self.project: str = config["project"]
        self.branch: str = config.get("branch", "main")
        self.paths_glob: Optional[list[str]] = config.get("paths_glob")
        self._explicit_token: Optional[str] = config.get("token")
        self.base_url: str = config.get(
            "base_url", "https://gitlab.com/api/v4"
        ).rstrip("/")

        import httpx  # noqa: PLC0415

        self._httpx = httpx
        self._transport: Optional[httpx.BaseTransport] = None
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

    def _reset_client(self) -> None:
        self._client = self._httpx.Client(
            transport=self._transport,
            timeout=self._httpx.Timeout(30.0, connect=10.0),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"GitLabConnector(project={self.project!r}, branch={self.branch!r}, "
            f"token={'***' if self._explicit_token else None})"
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _resolve_token(self) -> Optional[str]:
        if self._explicit_token:
            return self._explicit_token
        return os.environ.get("GITLAB_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self._resolve_token()
        if token:
            # GitLab supports either PRIVATE-TOKEN or
            # Authorization: Bearer; PRIVATE-TOKEN works for both PATs
            # and project / group access tokens, so we prefer it.
            headers["PRIVATE-TOKEN"] = token
        return headers

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------
    def _project_segment(self) -> str:
        """URL-encode the project identifier for use in paths.

        GitLab paths require ``namespace/project`` to be encoded as
        ``namespace%2Fproject``. Numeric IDs need no encoding (no
        slash). ``quote(safe="")`` does the right thing for both.
        """
        return quote(self.project, safe="")

    def _file_path_segment(self, path: str) -> str:
        """URL-encode a file path for the ``/files/...`` endpoint.

        GitLab requires the full path (including slashes) to be
        percent-encoded.
        """
        return quote(path, safe="")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get_json(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, path: str, **params: Any) -> list[Any]:
        """Aggregate all pages of a paginated GitLab list endpoint.

        GitLab's pagination uses ``X-Next-Page`` (or ``Link`` headers).
        Cheaper / more portable: just follow ``page`` until we get an
        empty list.
        """
        all_items: list[Any] = []
        page = 1
        while True:
            page_params = dict(params)
            page_params["page"] = page
            page_params["per_page"] = page_params.get("per_page", 100)
            url = f"{self.base_url}{path}"
            resp = self._client.get(
                url, headers=self._auth_headers(), params=page_params
            )
            resp.raise_for_status()
            items = resp.json()
            if not items:
                break
            all_items.extend(items)
            # If the response carries X-Next-Page, honour it; else stop.
            next_page = resp.headers.get("X-Next-Page", "")
            if not next_page:
                break
            try:
                page = int(next_page)
            except ValueError:
                break
        return all_items

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------
    def _head_sha(self) -> str:
        data = self._get_json(
            f"/projects/{self._project_segment()}/repository/commits/{self.branch}"
        )
        return data["id"]

    def _list_tree(self) -> list[dict[str, Any]]:
        items = self._get_paginated(
            f"/projects/{self._project_segment()}/repository/tree",
            ref=self.branch,
            recursive="true",
        )
        return [item for item in items if item.get("type") == "blob"]

    def _fetch_content(self, path: str, ref: str) -> tuple[Optional[str], dict[str, Any]]:
        """Fetch a file's body and metadata via ``/repository/files/{path}``.

        Returns ``(content, meta)``. Content is ``None`` for binary or
        404 (warned, skipped).
        """
        try:
            data = self._get_json(
                f"/projects/{self._project_segment()}/repository/files/{self._file_path_segment(path)}",
                ref=ref,
            )
        except self._httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Concurrent delete or branch rewrite — skip quietly.
                return None, {}
            raise

        encoding = data.get("encoding", "base64")
        raw = data.get("content", "")
        if encoding == "base64":
            blob = base64.b64decode(raw)
        else:
            blob = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)

        meta = {
            "blob_id": data.get("blob_id"),
            "size": data.get("size", len(blob)),
            "commit_id": data.get("commit_id"),
            "last_commit_id": data.get("last_commit_id"),
        }

        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"gitlab: skipping binary file {path!r} (not valid UTF-8)",
                UserWarning,
                stacklevel=3,
            )
            return None, meta
        return content, meta

    def _matches_glob(self, path: str) -> bool:
        if not self.paths_glob:
            return True
        for pattern in self.paths_glob:
            if _glob_match(path, pattern):
                return True
        return False

    # ------------------------------------------------------------------
    # Document generation
    # ------------------------------------------------------------------
    def _make_document(
        self,
        *,
        path: str,
        content: str,
        blob_id: str,
        size: int,
        branch_sha: str,
    ) -> Document:
        return Document(
            id=path,
            content=content,
            title=path,
            metadata={
                "path": path,
                "size": size,
                "blob_id": blob_id,
                "branch": self.branch,
                # branch_sha is the cursor-advancement value (same shape as github).
                "branch_sha": branch_sha,
            },
        )

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        branch_sha = self._head_sha()
        for entry in self._list_tree():
            path = entry["path"]
            if not self._matches_glob(path):
                continue
            content, meta = self._fetch_content(path, ref=branch_sha)
            if content is None:
                continue
            yield self._make_document(
                path=path,
                content=content,
                blob_id=meta.get("blob_id") or entry.get("id", ""),
                size=meta.get("size", 0),
                branch_sha=branch_sha,
            )

    # ---- IncrementalSource -------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        prior = cursor.get("after_commit_sha")
        if not prior:
            yield from self.iter_documents()
            return

        branch_sha = self._head_sha()
        if prior == branch_sha:
            return

        diff = self._get_json(
            f"/projects/{self._project_segment()}/repository/compare",
            **{"from": prior, "to": branch_sha},
        )
        for file_entry in diff.get("diffs", []):
            if file_entry.get("deleted_file"):
                continue
            # GitLab's compare returns ``new_path`` and ``old_path``;
            # ``new_path`` is what we want (added / modified file path).
            path = file_entry.get("new_path") or file_entry.get("old_path")
            if not path or not self._matches_glob(path):
                continue
            content, meta = self._fetch_content(path, ref=branch_sha)
            if content is None:
                continue
            yield self._make_document(
                path=path,
                content=content,
                blob_id=meta.get("blob_id") or "",
                size=meta.get("size", 0),
                branch_sha=branch_sha,
            )

    def cursor_from(self, last_document: Document) -> dict:
        meta = last_document.metadata or {}
        sha = meta.get("branch_sha")
        if sha is None:
            return {}
        return {"after_commit_sha": sha}


# ----------------------------------------------------------------------
# Glob matcher — re-uses the same recipe as the github connector. We
# duplicate it here rather than importing from `github.connector`
# because connector modules should be installable independently.
# ----------------------------------------------------------------------
def _glob_match(path: str, pattern: str) -> bool:
    if "**" not in pattern:
        path_parts = path.split("/")
        pat_parts = pattern.split("/")
        if len(path_parts) != len(pat_parts):
            return False
        return all(fnmatch.fnmatchcase(p, q) for p, q in zip(path_parts, pat_parts))

    pat_parts = pattern.split("/")
    star_idx = pat_parts.index("**")
    prefix = pat_parts[:star_idx]
    suffix = pat_parts[star_idx + 1 :]

    path_parts = path.split("/")
    if len(path_parts) < len(prefix) + len(suffix):
        return False
    head = path_parts[: len(prefix)]
    tail = path_parts[len(path_parts) - len(suffix) :] if suffix else []
    if not all(fnmatch.fnmatchcase(p, q) for p, q in zip(head, prefix)):
        return False
    if suffix and not all(fnmatch.fnmatchcase(p, q) for p, q in zip(tail, suffix)):
        return False
    return True
