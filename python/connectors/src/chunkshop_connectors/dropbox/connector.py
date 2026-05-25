"""Verified Dropbox connector (OAuth bearer / PAT auth).

Walks a Dropbox folder and yields one chunkshop ``Document`` per
text-shaped file. Auth is bearer-token-based — Dropbox supports both
short-lived OAuth tokens and long-lived "app access tokens" with the
same ``Authorization: Bearer <token>`` header, so the connector
treats them identically.

Endpoints consumed
------------------
* ``POST /2/files/list_folder``
    Lists files under ``folder_path`` (root = ``""``). Paginated via
    Dropbox's own ``cursor`` mechanism — the response carries a
    ``cursor`` and ``has_more``; we follow with ``/2/files/list_folder/continue``.
* ``POST /2/files/list_folder/continue``
    Continuation of pagination using the prior ``cursor``.
* ``POST /2/files/download``
    Downloads a file by ``path`` (or ``id:``). Body is a raw byte
    stream; the request is keyed by a special ``Dropbox-API-Arg``
    header containing the JSON payload (Dropbox's "content endpoint"
    convention).

Sync semantics
--------------
``sync_mode = SyncMode.CURSOR``. The cursor shape is::

    {"cursor": "<dropbox cursor>"}

* Empty cursor → call ``/2/files/list_folder`` from scratch, walk all
  pages, and stash the final cursor returned by Dropbox into every
  yielded document's metadata for ``cursor_from``.
* Non-empty cursor → call ``/2/files/list_folder/continue?cursor=<...>``;
  Dropbox emits only entries that changed since the cursor was minted.
  The response carries a *new* cursor which becomes the next stored
  value.

Dropbox cursors are opaque, monotonic, and account-scoped — the
connector never tries to construct one itself.

Text-only filter
----------------
The connector only emits content for text-shaped files. By default
that's the set ``{.txt, .md, .csv, .json, .yaml, .yml, .rst, .log}``.
Users can override via ``include_extensions``. Files whose extension
isn't in the allow-list are silently skipped. UTF-8 decode failure
on an allow-listed file emits a ``UserWarning`` and the file is
dropped — same policy as the github connector.
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from typing import Any, Iterable, Iterator, Optional

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


_DEFAULT_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".rst", ".log"}
)


@verified
class DropboxConnector:
    """Verified-tier Dropbox connector (cursor sync, OAuth/PAT bearer auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        self._explicit_token: Optional[str] = config.get("token")
        # Dropbox uses "" (empty string) to mean account root.
        self.folder_path: str = config.get("folder_path", "")
        self.recursive: bool = config.get("recursive", True)
        ext_cfg = config.get("include_extensions")
        if ext_cfg is None:
            self.include_extensions = _DEFAULT_TEXT_EXTENSIONS
        else:
            self.include_extensions = frozenset(e.lower() for e in ext_cfg)
        self.base_url: str = config.get(
            "base_url", "https://api.dropboxapi.com/2"
        ).rstrip("/")
        self.content_url: str = config.get(
            "content_url", "https://content.dropboxapi.com/2"
        ).rstrip("/")

        import httpx  # noqa: PLC0415

        self._httpx = httpx
        self._transport: Optional[httpx.BaseTransport] = None
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

        # Holds the most recent Dropbox cursor returned by the API.
        # cursor_from() reads this to advance the source cursor.
        self._latest_cursor: Optional[str] = None

    def _reset_client(self) -> None:
        self._client = self._httpx.Client(
            transport=self._transport,
            timeout=self._httpx.Timeout(30.0, connect=10.0),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"DropboxConnector(folder_path={self.folder_path!r}, "
            f"recursive={self.recursive!r}, "
            f"token={'***' if self._explicit_token else None})"
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _resolve_token(self) -> Optional[str]:
        if self._explicit_token:
            return self._explicit_token
        return os.environ.get("DROPBOX_TOKEN")

    def _auth_headers(self, *, content_type_json: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        token = self._resolve_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if content_type_json:
            headers["Content-Type"] = "application/json"
        return headers

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _post_rpc(self, path: str, body: dict[str, Any]) -> Any:
        """POST to a Dropbox RPC endpoint (api.dropboxapi.com/2/...)."""
        url = f"{self.base_url}{path}"
        resp = self._client.post(url, headers=self._auth_headers(), json=body)
        resp.raise_for_status()
        return resp.json()

    def _post_content_download(self, path_or_id: str) -> bytes:
        """POST to a Dropbox content endpoint.

        Dropbox's content endpoints are quirky: the request body is the
        *raw response payload* (empty for download), and the JSON args
        ride on a ``Dropbox-API-Arg`` header.
        """
        url = f"{self.content_url}/files/download"
        headers = self._auth_headers(content_type_json=False)
        headers["Dropbox-API-Arg"] = json.dumps({"path": path_or_id})
        # Body is empty for /files/download.
        resp = self._client.post(url, headers=headers, content=b"")
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------
    def _list_folder(self) -> Iterator[dict[str, Any]]:
        """Walk ``folder_path`` and yield file entries.

        Stashes the final Dropbox cursor on ``self._latest_cursor`` so
        ``iter_documents``/``iter_changes_since`` can bake it into
        every emitted document for cursor_from().
        """
        data = self._post_rpc(
            "/files/list_folder",
            body={
                "path": self.folder_path,
                "recursive": self.recursive,
                "include_deleted": False,
                # 2000 is the Dropbox per-page max; smaller doesn't help us.
                "limit": 2000,
            },
        )
        yield from self._emit_entries(data)
        while data.get("has_more"):
            cursor = data.get("cursor")
            if not cursor:
                break
            data = self._post_rpc(
                "/files/list_folder/continue",
                body={"cursor": cursor},
            )
            yield from self._emit_entries(data)
        # Final cursor from the last page (or the only page).
        self._latest_cursor = data.get("cursor", self._latest_cursor)

    def _list_folder_continue(self, cursor: str) -> Iterator[dict[str, Any]]:
        """Continue from a stored Dropbox cursor (incremental sync)."""
        data = self._post_rpc(
            "/files/list_folder/continue", body={"cursor": cursor}
        )
        yield from self._emit_entries(data)
        while data.get("has_more"):
            next_cursor = data.get("cursor")
            if not next_cursor:
                break
            data = self._post_rpc(
                "/files/list_folder/continue",
                body={"cursor": next_cursor},
            )
            yield from self._emit_entries(data)
        self._latest_cursor = data.get("cursor", cursor)

    def _emit_entries(self, data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        for entry in data.get("entries", []):
            # Skip folders and deleted entries — we only emit live files.
            tag = entry.get(".tag")
            if tag != "file":
                continue
            yield entry

    # ------------------------------------------------------------------
    # Filtering + download
    # ------------------------------------------------------------------
    def _matches_extension(self, name: str) -> bool:
        lower = name.lower()
        for ext in self.include_extensions:
            if lower.endswith(ext):
                return True
        return False

    def _download_text(self, path: str) -> Optional[str]:
        try:
            blob = self._post_content_download(path)
        except self._httpx.HTTPStatusError as exc:
            warnings.warn(
                f"dropbox: skipping {path!r}: download failed "
                f"({exc.response.status_code})",
                UserWarning,
                stacklevel=3,
            )
            return None
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"dropbox: skipping {path!r}: not valid UTF-8",
                UserWarning,
                stacklevel=3,
            )
            return None

    def _entry_to_document(self, entry: dict[str, Any]) -> Optional[Document]:
        name = entry.get("name", "")
        path_lower = entry.get("path_lower") or entry.get("path_display") or name
        if not self._matches_extension(name):
            return None
        content = self._download_text(entry.get("path_display") or path_lower)
        if content is None:
            return None
        return Document(
            id=path_lower,
            content=content,
            title=name,
            metadata={
                "dropbox_id": entry.get("id"),
                "server_modified": entry.get("server_modified"),
                "rev": entry.get("rev"),
                "size": entry.get("size"),
                "path_display": entry.get("path_display"),
                # cursor is monotonic; the value at the end of the run
                # is the one cursor_from() returns.
                "dropbox_cursor": self._latest_cursor,
            },
        )

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        # Materialise the entry list eagerly so _latest_cursor is the
        # final cursor before we yield any doc (every emitted doc
        # carries the same advancement value).
        entries = list(self._list_folder())
        for entry in entries:
            doc = self._entry_to_document(entry)
            if doc is not None:
                # Rewrite metadata so dropbox_cursor reflects the final
                # cursor (entries may have been emitted before the
                # cursor was stashed on the last page).
                yield Document(
                    id=doc.id,
                    content=doc.content,
                    title=doc.title,
                    metadata={**(doc.metadata or {}), "dropbox_cursor": self._latest_cursor},
                    fingerprint=doc.fingerprint,
                )

    # ---- IncrementalSource -------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        prior = cursor.get("cursor")
        if not prior:
            yield from self.iter_documents()
            return

        entries = list(self._list_folder_continue(prior))
        for entry in entries:
            doc = self._entry_to_document(entry)
            if doc is not None:
                yield Document(
                    id=doc.id,
                    content=doc.content,
                    title=doc.title,
                    metadata={**(doc.metadata or {}), "dropbox_cursor": self._latest_cursor},
                    fingerprint=doc.fingerprint,
                )

    def cursor_from(self, last_document: Document) -> dict:
        meta = last_document.metadata or {}
        cur = meta.get("dropbox_cursor")
        if cur is None:
            return {}
        return {"cursor": cur}
