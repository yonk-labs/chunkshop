"""Verified GitHub connector (PAT auth).

This connector walks a GitHub repository at a given branch via the
REST API and yields one chunkshop ``Document`` per text file. Auth
is PAT-only — Personal Access Token, supplied either by config or
by the ``GITHUB_TOKEN`` env var. OAuth is out of scope for this
tier (we leave that to ``chunkshop_connectors.gdrive`` / ``slack``
which need it).

Endpoints consumed
------------------
* ``GET /repos/{owner}/{repo}``
    Resolves the repo's ``default_branch`` when the caller didn't pin
    one (or as a fallback when a pinned branch 404s). See #27.
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
import shutil
import subprocess
import tempfile
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
        # branch may be None → auto-detect the repo's default branch
        # (GET /repos reports `.default_branch`). This avoids the classic
        # 404 when a repo's default is `master`, not `main` (see #27).
        self._configured_branch: Optional[str] = config.get("branch")
        # When True, a pinned branch that 404s is a hard error rather than
        # falling back to the repo default.
        self.branch_strict: bool = config.get("branch_strict", False)
        self._resolved_branch: Optional[str] = None
        self.clone: bool = config.get("clone", False)
        self.max_clone_mb: int = config.get("max_clone_mb", 200)
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
    @property
    def branch(self) -> str:
        """The branch this connector operates on.

        Resolves lazily: if the caller pinned a branch it's used as-is;
        otherwise the repo's ``default_branch`` is auto-detected via
        ``GET /repos/{owner}/{repo}`` (one extra API call, cached). This
        is the fix for #27 — repos whose default is ``master`` (most of
        the older PG ecosystem) no longer 404 on a hardcoded ``main``.
        """
        if self._resolved_branch is None:
            self._resolved_branch = (
                self._configured_branch
                if self._configured_branch is not None
                else self._default_branch()
            )
        return self._resolved_branch

    def _default_branch(self) -> str:
        data = self._get_json(f"/repos/{self.owner}/{self.repo}")
        return data["default_branch"]

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
        # Show the resolved branch if we've already detected it, else the
        # configured value (``None`` = auto-detect). Never trigger the
        # lazy ``branch`` property here — __repr__ must not do network I/O.
        branch = self._resolved_branch or self._configured_branch
        return (
            f"GitHubConnector(owner={self.owner!r}, repo={self.repo!r}, "
            f"branch={branch!r}, token={'***' if self._explicit_token else None})"
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
        try:
            data = self._get_json(
                f"/repos/{self.owner}/{self.repo}/branches/{self.branch}"
            )
        except self._httpx.HTTPStatusError as exc:
            # A pinned branch that doesn't exist 404s here. Unless the
            # caller asked for strict behaviour, fall back to the repo's
            # real default branch and retry once (#27).
            if (
                exc.response.status_code == 404
                and self._configured_branch is not None
                and not self.branch_strict
            ):
                self._resolved_branch = self._default_branch()
                data = self._get_json(
                    f"/repos/{self.owner}/{self.repo}/branches/{self._resolved_branch}"
                )
            else:
                raise
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
        if self.clone:
            if shutil.which("git") is not None:
                yield from self._iter_clone_documents()
                return
            warnings.warn(
                "github: clone=True but the `git` binary is unavailable; "
                "falling back to the REST per-file walk.",
                UserWarning,
                stacklevel=2,
            )
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

    # ---- clone-based walk (#28) -------------------------------------
    def _clone_url(self) -> str:
        """HTTPS clone URL, with the PAT inlined for private repos.

        Overridable in tests to point at a local file:// remote so the
        clone path stays hermetic.
        """
        token = self._resolve_token()
        host = "github.com"
        if token:
            return f"https://{token}@{host}/{self.owner}/{self.repo}.git"
        return f"https://{host}/{self.owner}/{self.repo}.git"

    def _git(self, *args: str, cwd: Optional[str] = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, ["git", *args], proc.stdout, proc.stderr
            )
        return proc.stdout

    def _shallow_clone(self, dest: str) -> None:
        """`git clone --depth 1` into ``dest``, with default-branch fallback."""
        url = self._clone_url()
        args = ["clone", "--depth", "1", "--single-branch"]
        if self._configured_branch is not None:
            args += ["--branch", self._configured_branch]
        try:
            self._git(*args, url, dest)
        except subprocess.CalledProcessError:
            # Pinned branch likely doesn't exist. Unless strict, retry
            # cloning the repo's default branch (mirrors REST #27 fallback).
            if self._configured_branch is None or self.branch_strict:
                raise
            self._git("clone", "--depth", "1", url, dest)

    def _iter_clone_documents(self) -> Iterator[Document]:
        tmpdir = tempfile.mkdtemp(prefix="chunkshop-gh-")
        try:
            self._shallow_clone(tmpdir)
            # One `ls-tree` lists every tracked blob with sha + size, so we
            # never touch `.git` internals and get parity metadata for free.
            listing = self._git("ls-tree", "-r", "-l", "HEAD", cwd=tmpdir)
            entries = _parse_ls_tree(listing)
            total_bytes = sum(size for _, _, size in entries)
            limit = self.max_clone_mb * 1024 * 1024
            if total_bytes > limit:
                raise RuntimeError(
                    f"github: {self.owner}/{self.repo} clone is "
                    f"{total_bytes / 1024 / 1024:.1f} MB, over the "
                    f"max_clone_mb={self.max_clone_mb} limit. Raise the "
                    f"limit or use clone=False (REST walk)."
                )
            # Resolve branch/head SHA locally — no REST round-trips.
            head_sha = self._git("rev-parse", "HEAD", cwd=tmpdir).strip()
            self._resolved_branch = (
                self._configured_branch
                or self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=tmpdir).strip()
            )
            for sha, path, size in entries:
                if not self._matches_glob(path):
                    continue
                blob = (os.path.join(tmpdir, path))
                with open(blob, "rb") as fh:
                    raw = fh.read()
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    warnings.warn(
                        f"github: skipping binary file {path!r} (not valid UTF-8)",
                        UserWarning,
                        stacklevel=2,
                    )
                    continue
                yield self._make_document(
                    path=path,
                    content=content,
                    file_sha=sha,
                    size=size,
                    branch_sha=head_sha,
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

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
# git ls-tree parsing
# ----------------------------------------------------------------------
def _parse_ls_tree(output: str) -> list[tuple[str, str, int]]:
    """Parse ``git ls-tree -r -l HEAD`` into ``(sha, path, size)`` tuples.

    Each line looks like::

        100644 blob <sha>   <size>\\t<path>

    Only ``blob`` entries are returned (submodule ``commit`` entries have
    size ``-`` and no checked-out file, so they're skipped).
    """
    entries: list[tuple[str, str, int]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        # parts == [mode, type, sha, size]
        if len(parts) != 4 or parts[1] != "blob":
            continue
        _mode, _type, sha, size_s = parts
        if not size_s.isdigit():
            continue
        entries.append((sha, path, int(size_s)))
    return entries


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
