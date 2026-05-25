"""Hermetic Google Drive v3 mock for the gdrive connector.

Uses ``httpx.MockTransport`` (in-process, no socket) to route every
HTTP call the connector makes to an in-memory state machine. The
connector's ``transport`` attribute is set by tests to the
``transport`` exposed on this fixture's handle, so all
``httpx.Client`` traffic short-circuits before reaching the network.

Endpoints stubbed
-----------------
* ``GET /drive/v3/files`` — list files; honours the ``q`` and
  ``pageToken`` query params.
* ``GET /drive/v3/files/{id}/export?mimeType=text/plain`` — export
  google-doc as plain text.
* ``GET /drive/v3/files/{id}?alt=media`` — download a binary file
  body.
* ``GET /drive/v3/changes/startPageToken`` — returns the current
  ``startPageToken``.
* ``GET /drive/v3/changes?pageToken=...`` — returns changed file IDs
  plus the next ``newStartPageToken``.

JSON shapes match the real Drive v3 reference. See
https://developers.google.com/drive/api/v3/reference/files for the
``files.list`` shape and the changes reference for the changes
shape.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

# pytest is gated so this module is importable from runtime-only contexts
# (e.g. the standalone `make_gdrive_mock()` factory used by the bundled
# demo `e2e_gdrive_mocked.py` — that demo MUST work on a fresh
# `pip install chunkshop-connectors[gdrive]` without dev extras).
try:
    import pytest  # noqa: F401
    _HAS_PYTEST = True
except ImportError:
    pytest = None  # type: ignore[assignment]
    _HAS_PYTEST = False


# ---- Fixture state ------------------------------------------------------
class _GDriveMockHandle:
    """In-memory Drive state + the ``httpx.MockTransport`` driving it.

    Attributes
    ----------
    valid_config
        Connector config dict ready to feed ``factory()``. Includes
        ``oauth_tokens`` so the connector doesn't fall back to env.
    transport
        ``httpx.MockTransport`` instance — assigned by tests to the
        connector's ``_transport`` attribute.
    files
        ``{file_id: file_record}`` — internal store.
    pending_changes
        FIFO list of ``(file_id, new_start_page_token)`` tuples. Each
        call to ``/changes`` drains the next entry.
    start_page_token
        Token returned by ``/changes/startPageToken`` on first call.
    seen_tokens
        Set of bearer tokens the connector has sent in
        ``Authorization``. Tests assert env-token plumbing.
    """

    def __init__(self):
        self.files: dict[str, dict[str, Any]] = {}
        # FIFO of (file_id, newStartPageToken).
        self.pending_changes: list[tuple[str, str]] = []
        self.start_page_token = "START_TOKEN_1"
        self.seen_tokens: set[str] = set()
        # File body store, raw bytes.
        self.bodies: dict[str, bytes] = {}
        # Google-doc export bodies, keyed by file_id (UTF-8 string).
        self.exports: dict[str, str] = {}

        # Build the transport last so it captures self.
        self.transport = httpx.MockTransport(self._dispatch)

        self.valid_config: dict[str, Any] = {
            "folder_id": "fake-folder-1",
            "oauth_tokens": {
                "access_token": "fake-at",
                "refresh_token": "fake-rt",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
                "provider": "google",
                "provider_extras": {},
            },
            # Connector concats {base}/files etc; absolute URL keeps
            # httpx happy when the MockTransport sees the request.
            "drive_base_url": "https://drive.mock/drive/v3",
        }

    # ------------------------------------------------------------------
    # Public test helpers
    # ------------------------------------------------------------------
    def add_file(
        self,
        *,
        file_id: str,
        name: str,
        mime_type: str,
        content: bytes | str,
        modified_time: str = "2026-05-25T12:00:00.000Z",
        parents: list[str] | None = None,
    ) -> None:
        """Register a file in the mock's in-memory store.

        For Google Docs (``application/vnd.google-apps.document``) the
        ``content`` is treated as the *exported* plain-text body. For
        all other MIME types it's the raw download body.
        """
        record = {
            "id": file_id,
            "name": name,
            "mimeType": mime_type,
            "modifiedTime": modified_time,
            "parents": parents or ["fake-folder-1"],
        }
        self.files[file_id] = record
        if mime_type == "application/vnd.google-apps.document":
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            self.exports[file_id] = content
        else:
            if isinstance(content, str):
                content = content.encode("utf-8")
            self.bodies[file_id] = content

    def add_change(self, file_id: str, *, new_start_page_token: str) -> None:
        """Queue a change for the next ``/changes?pageToken=...`` call.

        The change refers to ``file_id``; the response also includes
        ``newStartPageToken`` so the connector can advance its cursor.
        """
        self.pending_changes.append((file_id, new_start_page_token))

    # ------------------------------------------------------------------
    # Internal — request dispatcher
    # ------------------------------------------------------------------
    def _record_token(self, request: httpx.Request) -> None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            self.seen_tokens.add(auth[7:])

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self._record_token(request)
        url = urlparse(str(request.url))
        path = url.path
        qs = parse_qs(url.query)

        # ---- files.list ----------------------------------------------
        if path.endswith("/files") and request.method == "GET":
            return self._handle_files_list(qs)

        # ---- files.export (google-doc) -------------------------------
        if path.endswith("/export") and "/files/" in path:
            file_id = path.rsplit("/", 2)[-2]
            return self._handle_export(file_id, qs)

        # ---- changes.getStartPageToken -------------------------------
        if path.endswith("/changes/startPageToken"):
            return httpx.Response(
                200,
                json={"startPageToken": self.start_page_token},
            )

        # ---- changes.list --------------------------------------------
        if path.endswith("/changes"):
            return self._handle_changes(qs)

        # ---- files.get (download via alt=media) ----------------------
        if "/files/" in path:
            file_id = path.rsplit("/", 1)[-1]
            if qs.get("alt") == ["media"]:
                return self._handle_download(file_id)
            # Metadata-only get — return the record.
            rec = self.files.get(file_id)
            if rec is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=rec)

        return httpx.Response(404, text=f"no mock for {request.method} {path}")

    # ---- handlers --------------------------------------------------------
    def _handle_files_list(self, qs: dict[str, list[str]]) -> httpx.Response:
        """Serve ``files.list``.

        Filters by ``q`` if it looks like ``'<folder>' in parents``; on
        any other ``q`` value, returns every file in the store (the
        connector's tests don't exercise sophisticated Drive query
        syntax).
        """
        q = qs.get("q", [""])[0]
        fields = qs.get("fields", [""])[0]  # noqa: F841 — kept for parity
        page_token = qs.get("pageToken", [None])[0]  # noqa: F841

        items: list[dict[str, Any]] = []
        for fid, rec in self.files.items():
            if "in parents" in q and "'" in q:
                target = q.split("'")[1]
                if target not in rec.get("parents", []):
                    continue
            items.append(
                {
                    "id": rec["id"],
                    "name": rec["name"],
                    "mimeType": rec["mimeType"],
                    "modifiedTime": rec["modifiedTime"],
                    "parents": rec["parents"],
                }
            )
        return httpx.Response(
            200,
            json={"files": items, "incompleteSearch": False},
        )

    def _handle_export(
        self, file_id: str, qs: dict[str, list[str]]
    ) -> httpx.Response:
        mime = qs.get("mimeType", [""])[0]
        if mime != "text/plain":
            return httpx.Response(400, json={"error": f"export not supported for {mime}"})
        body = self.exports.get(file_id)
        if body is None:
            return httpx.Response(404, json={"error": "not exported"})
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    def _handle_download(self, file_id: str) -> httpx.Response:
        body = self.bodies.get(file_id)
        if body is None:
            return httpx.Response(404, json={"error": "no body"})
        rec = self.files.get(file_id, {})
        mime = rec.get("mimeType", "application/octet-stream")
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": mime},
        )

    def _handle_changes(self, qs: dict[str, list[str]]) -> httpx.Response:
        """Serve ``changes.list``.

        Each call drains one entry off ``pending_changes`` (FIFO). If
        none are queued, returns an empty change set with
        ``newStartPageToken`` equal to the current ``start_page_token``
        — i.e., "nothing happened, cursor stays put".
        """
        page_token = qs.get("pageToken", [None])[0]  # noqa: F841
        if not self.pending_changes:
            return httpx.Response(
                200,
                json={
                    "changes": [],
                    "newStartPageToken": self.start_page_token,
                },
            )
        file_id, new_token = self.pending_changes.pop(0)
        rec = self.files.get(file_id, {})
        change = {
            "fileId": file_id,
            "removed": False,
            "file": {
                "id": file_id,
                "name": rec.get("name", file_id),
                "mimeType": rec.get("mimeType", "application/octet-stream"),
                "modifiedTime": rec.get("modifiedTime", ""),
                "parents": rec.get("parents", []),
            },
        }
        self.start_page_token = new_token
        return httpx.Response(
            200,
            json={
                "changes": [change],
                "newStartPageToken": new_token,
            },
        )


def make_gdrive_mock():
    """Return a fresh ``_GDriveMockHandle`` seeded with the default fixture set.

    The default seed mirrors the ``gdrive_mock`` pytest fixture below: one
    google-doc, one text file, one image (the image is silently skipped by the
    connector with a UserWarning). This factory is what standalone scripts
    (e.g. ``connectors/examples/e2e_gdrive_mocked.py``) call to drive the
    connector outside pytest.
    """
    handle = _GDriveMockHandle()
    handle.add_file(
        file_id="file-doc-1",
        name="Design Notes",
        mime_type="application/vnd.google-apps.document",
        content="exported google-doc content\nmore text",
    )
    handle.add_file(
        file_id="file-txt-1",
        name="readme.txt",
        mime_type="text/plain",
        content=b"raw text content",
    )
    handle.add_file(
        file_id="file-img-1",
        name="logo.png",
        mime_type="image/png",
        content=b"\x89PNG\r\n\x1a\n",
    )
    return handle


# ---- pytest fixture ----------------------------------------------------
# Only registered when pytest is importable; the `make_gdrive_mock()` factory
# above is the non-pytest interface the bundled demos use.
if _HAS_PYTEST:
    @pytest.fixture
    def gdrive_mock():
        """Provide a Drive v3 mock seeded with one google-doc, one text file,
        and one image (the image is silently skipped by the connector).
        """
        return make_gdrive_mock()


__all__ = ["make_gdrive_mock"] + (["gdrive_mock"] if _HAS_PYTEST else [])
