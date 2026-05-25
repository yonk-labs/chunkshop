"""Verified Google Drive connector (OAuth bearer auth).

Walks a Drive folder (or runs a Drive query) and yields one chunkshop
``Document`` per text-shaped file. Backed by raw ``httpx`` against the
Drive v3 REST API — no ``google-api-python-client`` dependency
(easier to mock hermetically, dramatically lighter).

Endpoints consumed
------------------
* ``GET /drive/v3/files?q=...&fields=...&pageToken=...``
    Paginate through files matching the folder/query.
* ``GET /drive/v3/files/{id}/export?mimeType=text/plain``
    Export Google Docs as plain text.
* ``GET /drive/v3/files/{id}?alt=media``
    Download text-shaped files (text/plain, text/markdown, text/html,
    application/json).
* ``GET /drive/v3/changes/startPageToken``
    Seed the incremental cursor on first sync.
* ``GET /drive/v3/changes?pageToken=...&includeRemoved=false``
    Pull file IDs that changed since the cursor.

Sync semantics
--------------
``sync_mode = SyncMode.CURSOR``. The cursor shape is::

    {"page_token": "<drive page token>"}

* Empty cursor → full folder/query walk + an initial ``startPageToken``
  fetch so the next sync has somewhere to resume from.
* Non-empty cursor → ``/changes?pageToken=...``; only files in the
  changes list are re-emitted, plus the response's ``newStartPageToken``
  becomes the next cursor (carried on every yielded document's
  ``metadata["next_page_token"]``).

Skipped MIME types
------------------
The connector only emits content for the text-shaped MIME types
listed in ``_TEXT_MIME_TYPES`` plus ``application/vnd.google-apps.document``
(exported as text). Everything else (images, videos, binaries,
non-text Google-apps types like Sheets) is silently skipped with a
``UserWarning``.
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from typing import Any, Iterable, Iterator, Optional

import httpx

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


# MIME types we treat as "text-shaped" and download via ``alt=media``.
# Kept tight — PDF / DOCX / etc. are out of scope for this tier; users
# should pre-process them via ``yonk-doctools`` first.
_TEXT_MIME_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/html",
        "text/x-markdown",
        "application/json",
    }
)
_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

# Drive v3 ``fields`` mask for files.list. Tight — only what we use.
_LIST_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,parents)"
)


@verified
class GDriveConnector:
    """Verified-tier Google Drive connector (cursor sync, OAuth auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        self.folder_id: Optional[str] = config.get("folder_id")
        self.query: Optional[str] = config.get("query")
        self.scopes: list[str] = config.get("scopes", [])
        self._config_oauth_tokens: Optional[dict] = config.get("oauth_tokens")
        self.base_url: str = config.get(
            "drive_base_url", "https://www.googleapis.com/drive/v3"
        ).rstrip("/")

        # Optional transport hook for tests; production leaves it None.
        # Tests set ``connector._transport = mock_transport`` after
        # construction and call ``_reset_client()`` to pick it up.
        self._transport: Optional[httpx.BaseTransport] = None
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

        # Cached value of newStartPageToken from the most recent
        # /changes or /startPageToken call; cursor_from() reads it.
        self._next_page_token: Optional[str] = None

    def _reset_client(self) -> None:
        """Recreate the underlying httpx.Client honouring ``self._transport``.

        Tests call this after assigning ``connector._transport`` so the
        mock starts intercepting requests immediately.
        """
        self._client = httpx.Client(
            transport=self._transport,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    def __repr__(self) -> str:  # pragma: no cover -- defensive
        return (
            f"GDriveConnector(folder_id={self.folder_id!r}, "
            f"query={self.query!r}, oauth_tokens=<redacted>)"
        )

    # ------------------------------------------------------------------
    # OAuth resolution
    # ------------------------------------------------------------------
    def _resolve_access_token(self) -> str:
        """Resolve the OAuth access_token lazily.

        Precedence: config ``oauth_tokens`` > ``$GDRIVE_OAUTH_TOKENS`` env
        var (JSON-encoded). Raises ``ValueError`` if neither is set —
        we don't pre-flight in ``__init__`` so config validation
        doesn't depend on the runtime environment.
        """
        tokens = self._config_oauth_tokens
        if tokens is None:
            raw = os.environ.get("GDRIVE_OAUTH_TOKENS")
            if raw is None:
                raise ValueError(
                    "gdrive: oauth_tokens missing from config and "
                    "$GDRIVE_OAUTH_TOKENS env var is unset. Pass tokens via "
                    "ConfigModel.oauth_tokens or set the env var."
                )
            try:
                tokens = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"gdrive: $GDRIVE_OAUTH_TOKENS is not valid JSON: {exc}"
                ) from None
        access = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not access:
            raise ValueError("gdrive: oauth_tokens has no access_token")
        return access

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._resolve_access_token()}"}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get_json(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_bytes(self, path: str, **params: Any) -> bytes:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # File listing + content
    # ------------------------------------------------------------------
    def _build_query(self) -> str:
        """Compose the Drive query string for ``files.list``.

        If both ``folder_id`` and ``query`` are supplied, they're
        AND'd. ConfigModel already enforces at least one is present.
        """
        parts: list[str] = []
        if self.folder_id:
            parts.append(f"'{self.folder_id}' in parents")
        if self.query:
            parts.append(self.query)
        return " and ".join(parts)

    def _list_files(self) -> Iterator[dict[str, Any]]:
        """Paginate through files matching the folder/query."""
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "q": self._build_query(),
                "fields": _LIST_FIELDS,
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get_json("/files", **params)
            for f in data.get("files", []):
                yield f
            page_token = data.get("nextPageToken")
            if not page_token:
                return

    def _export_doc(self, file_id: str) -> Optional[str]:
        """Export a Google Doc as UTF-8 plain text. Returns None on failure."""
        try:
            blob = self._get_bytes(f"/files/{file_id}/export", mimeType="text/plain")
        except httpx.HTTPStatusError as exc:
            warnings.warn(
                f"gdrive: skipping {file_id!r}: export failed ({exc.response.status_code})",
                UserWarning,
                stacklevel=3,
            )
            return None
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"gdrive: skipping {file_id!r}: export not valid UTF-8",
                UserWarning,
                stacklevel=3,
            )
            return None

    def _download_text(self, file_id: str) -> Optional[str]:
        """Download a text-shaped file body. Returns None on failure."""
        try:
            blob = self._get_bytes(f"/files/{file_id}", alt="media")
        except httpx.HTTPStatusError as exc:
            warnings.warn(
                f"gdrive: skipping {file_id!r}: download failed ({exc.response.status_code})",
                UserWarning,
                stacklevel=3,
            )
            return None
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            warnings.warn(
                f"gdrive: skipping {file_id!r}: not valid UTF-8",
                UserWarning,
                stacklevel=3,
            )
            return None

    def _file_to_document(
        self, f: dict[str, Any], *, next_page_token: Optional[str]
    ) -> Optional[Document]:
        """Convert a Drive file record into a chunkshop Document.

        Returns ``None`` for skipped MIME types (non-text). Emits a
        ``UserWarning`` on the skip path so consumers can opt in to
        loud-skip mode by enabling warnings.
        """
        mime = f.get("mimeType", "")
        file_id = f["id"]
        name = f.get("name", file_id)

        if mime == _GOOGLE_DOC_MIME:
            content = self._export_doc(file_id)
        elif mime in _TEXT_MIME_TYPES:
            content = self._download_text(file_id)
        else:
            warnings.warn(
                f"gdrive: skipping {name!r} (id={file_id}): unsupported mimeType {mime!r}",
                UserWarning,
                stacklevel=3,
            )
            return None

        if content is None:
            return None

        metadata: dict[str, Any] = {
            "drive_id": file_id,
            "mime_type": mime,
            "modified_time": f.get("modifiedTime"),
            "parents": f.get("parents", []),
        }
        # next_page_token is the cursor advancement value. It rides on
        # every emitted doc's metadata; cursor_from() reads it.
        if next_page_token is not None:
            metadata["next_page_token"] = next_page_token

        return Document(
            id=file_id,
            content=content,
            title=name,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        # Even on a flat ``iter_documents`` call we want subsequent
        # incremental syncs to resume cleanly — fetch the start page
        # token first so it can ride on the metadata.
        start_token_resp = self._get_json("/changes/startPageToken")
        token = start_token_resp.get("startPageToken")
        self._next_page_token = token

        for f in self._list_files():
            doc = self._file_to_document(f, next_page_token=token)
            if doc is not None:
                yield doc

    # ---- IncrementalSource -------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        prior = cursor.get("page_token")
        if not prior:
            # First sync — defer to iter_documents which seeds the token.
            yield from self.iter_documents()
            return

        # Subsequent sync — pull changes since the prior page token.
        page_token: Optional[str] = prior
        new_start_token: Optional[str] = None
        changed_files: list[dict[str, Any]] = []
        while page_token:
            data = self._get_json(
                "/changes",
                pageToken=page_token,
                includeRemoved="false",
                fields=(
                    "nextPageToken,newStartPageToken,"
                    "changes(fileId,removed,file(id,name,mimeType,modifiedTime,parents))"
                ),
            )
            for change in data.get("changes", []):
                if change.get("removed"):
                    continue
                f = change.get("file")
                if f is None:
                    continue
                changed_files.append(f)
            new_start_token = data.get("newStartPageToken") or new_start_token
            page_token = data.get("nextPageToken")

        self._next_page_token = new_start_token or prior
        for f in changed_files:
            doc = self._file_to_document(f, next_page_token=self._next_page_token)
            if doc is not None:
                yield doc

    def cursor_from(self, last_document: Document) -> dict:
        # Monotonic cursor — every doc emitted in a single sync carries
        # the same ``next_page_token`` (the response's
        # ``newStartPageToken``), so the merge in
        # ``chunkshop.testing.merge_cursor`` converges to the same
        # value regardless of iteration order.
        meta = last_document.metadata or {}
        token = meta.get("next_page_token")
        if token is None:
            return {}
        return {"page_token": token}
