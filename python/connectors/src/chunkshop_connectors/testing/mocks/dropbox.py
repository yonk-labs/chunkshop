"""Hermetic Dropbox v2 mock for the dropbox connector.

Uses ``httpx.MockTransport`` to intercept Dropbox API calls in-process.
Models the listing pagination plus the content-endpoint download
quirk where the file path rides in the ``Dropbox-API-Arg`` header.

Endpoints stubbed
-----------------
* ``POST /2/files/list_folder`` — initial listing.
* ``POST /2/files/list_folder/continue`` — pagination + incremental.
* ``POST /2/files/download`` (on the content-host) — raw file body.

The mock tracks a *Dropbox cursor* as a monotonically increasing
integer encoded as a string. Each call to ``list_folder`` or
``list_folder/continue`` returns the next batch of entries since the
cursor; on the incremental path the entries come from the
``add_change`` queue (FIFO).
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest


class _DropboxMockHandle:
    """In-memory Dropbox state + transport."""

    def __init__(self) -> None:
        # File store: path_display → record dict.
        self.files: dict[str, dict[str, Any]] = {}
        # File bodies: path_display → bytes
        self.bodies: dict[str, bytes] = {}
        # FIFO of file path_display for the next `list_folder/continue`
        # call (used to simulate incremental change deltas).
        self.pending_changes: list[str] = []
        # Monotonic cursor counter; each list_folder / continue call
        # mints a new value.
        self._cursor_counter = 0
        self.seen_tokens: set[str] = set()

        self.transport = httpx.MockTransport(self._dispatch)

        self.valid_config: dict[str, Any] = {
            "folder_path": "",
            "recursive": True,
            "token": "sl.fake_dropbox_token",
            "base_url": "https://api.dropbox.mock/2",
            "content_url": "https://content.dropbox.mock/2",
        }

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def add_file(
        self,
        *,
        path_display: str,
        content: bytes | str,
        rev: str | None = None,
        server_modified: str = "2026-05-25T12:00:00Z",
    ) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        if rev is None:
            rev = f"rev-{len(self.files):08d}"
        name = path_display.rsplit("/", 1)[-1]
        rec = {
            ".tag": "file",
            "id": f"id:{name}",
            "name": name,
            "path_display": path_display,
            "path_lower": path_display.lower(),
            "rev": rev,
            "server_modified": server_modified,
            "size": len(content),
        }
        self.files[path_display] = rec
        self.bodies[path_display] = content

    def add_change(self, path_display: str) -> None:
        """Queue a file for the next list_folder/continue call."""
        self.pending_changes.append(path_display)

    def _mint_cursor(self) -> str:
        self._cursor_counter += 1
        return f"dropbox-cursor-{self._cursor_counter}"

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------
    def _record_token(self, request: httpx.Request) -> None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            self.seen_tokens.add(auth[7:])

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
        self._record_token(request)
        url = urlparse(str(request.url))
        path = url.path

        if request.method == "POST" and path.endswith("/files/list_folder"):
            return self._handle_list_folder()

        if request.method == "POST" and path.endswith("/files/list_folder/continue"):
            return self._handle_list_folder_continue()

        if request.method == "POST" and path.endswith("/files/download"):
            return self._handle_download(request)

        return httpx.Response(404, text=f"no mock for {request.method} {path}")

    # ---- handlers -----------------------------------------------------
    def _handle_list_folder(self) -> httpx.Response:
        entries = list(self.files.values())
        cursor = self._mint_cursor()
        return httpx.Response(
            200,
            json={"entries": entries, "cursor": cursor, "has_more": False},
        )

    def _handle_list_folder_continue(self) -> httpx.Response:
        if not self.pending_changes:
            cursor = self._mint_cursor()
            return httpx.Response(
                200,
                json={"entries": [], "cursor": cursor, "has_more": False},
            )
        # Drain the queue in one batch.
        entries = []
        while self.pending_changes:
            p = self.pending_changes.pop(0)
            rec = self.files.get(p)
            if rec is not None:
                entries.append(rec)
        cursor = self._mint_cursor()
        return httpx.Response(
            200,
            json={"entries": entries, "cursor": cursor, "has_more": False},
        )

    def _handle_download(self, request: httpx.Request) -> httpx.Response:
        arg = request.headers.get("dropbox-api-arg") or request.headers.get(
            "Dropbox-API-Arg"
        )
        if not arg:
            return httpx.Response(400, json={"error": "missing Dropbox-API-Arg"})
        try:
            payload = json.loads(arg)
        except json.JSONDecodeError:
            return httpx.Response(400, json={"error": "bad Dropbox-API-Arg JSON"})
        path = payload.get("path", "")
        body = self.bodies.get(path)
        if body is None:
            return httpx.Response(409, json={"error": "not found"})
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/octet-stream"},
        )


def make_dropbox_mock() -> _DropboxMockHandle:
    handle = _DropboxMockHandle()
    handle.add_file(
        path_display="/README.md",
        content="# Hello\n\nfrom dropbox",
    )
    handle.add_file(
        path_display="/notes/spec.md",
        content="spec body",
    )
    # Non-text file: should be skipped by the connector's extension filter
    # (default allow-list excludes .png).
    handle.add_file(
        path_display="/logo.png",
        content=b"\x89PNG\r\n\x1a\n",
    )
    return handle


@pytest.fixture
def dropbox_mock():
    return make_dropbox_mock()


__all__ = ["dropbox_mock", "make_dropbox_mock"]
