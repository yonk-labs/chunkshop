"""Hermetic Notion v1 REST mock for the notion connector.

Uses ``httpx.MockTransport`` (in-process, no socket) to route every
HTTP call the connector makes to an in-memory state machine. Tests
assign the connector's ``_transport`` attribute to this fixture's
``transport`` so all ``httpx.Client`` traffic short-circuits before
reaching the network.

Endpoints stubbed
-----------------
* ``POST /v1/databases/{database_id}/query`` — paginated; honours
  ``start_cursor`` and a ``filter.timestamp.last_edited_time.on_or_after``
  body filter.
* ``GET /v1/pages/{page_id}`` — single page fetch.
* ``GET /v1/blocks/{page_id}/children`` — paginated block tree;
  honours ``start_cursor``.

JSON shapes match the real Notion v1 reference
(https://developers.notion.com/reference).
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


class _NotionMockHandle:
    """In-memory Notion state + the ``httpx.MockTransport`` driving it."""

    def __init__(self) -> None:
        # page_id → page record (full Notion shape)
        self.pages: dict[str, dict[str, Any]] = {}
        # page_id → list of block records
        self.blocks: dict[str, list[dict[str, Any]]] = {}
        # database_id → ordered list of page_ids that belong to it
        self.database_members: dict[str, list[str]] = {}
        self.seen_tokens: set[str] = set()

        self.transport = httpx.MockTransport(self._dispatch)

        self.valid_config: dict[str, Any] = {
            # Default mock fixture uses a database scope.
            "database_id": "00112233-4455-6677-8899-aabbccddeeff",
            "token": "secret_fake_notion_token",
            "base_url": "https://notion.mock/v1",
        }

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def add_page(
        self,
        *,
        page_id: str,
        title: str,
        body_blocks: list[str],
        last_edited_time: str = "2026-05-25T12:00:00.000Z",
        created_time: str = "2026-05-25T11:00:00.000Z",
        database_id: str | None = None,
    ) -> None:
        """Register a page + its block children.

        ``body_blocks`` is a list of plain strings; each becomes one
        ``paragraph`` block. (Tests that need richer block types build
        records by hand via ``self.blocks[page_id]``.)
        """
        parent: dict[str, Any]
        if database_id is not None:
            parent = {"type": "database_id", "database_id": database_id}
            self.database_members.setdefault(database_id, []).append(page_id)
        else:
            parent = {"type": "workspace", "workspace": True}

        self.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "created_time": created_time,
            "last_edited_time": last_edited_time,
            "parent": parent,
            "properties": {
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": title},
                            "plain_text": title,
                        }
                    ],
                }
            },
        }
        # Build paragraph blocks for each line in body_blocks.
        blocks = []
        for i, line in enumerate(body_blocks):
            blocks.append(
                {
                    "object": "block",
                    "id": f"{page_id}-blk-{i}",
                    "type": "paragraph",
                    "has_children": False,
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": line},
                                "plain_text": line,
                            }
                        ]
                    },
                }
            )
        self.blocks[page_id] = blocks

    def touch_page(self, page_id: str, *, last_edited_time: str) -> None:
        """Mutate an existing page's ``last_edited_time`` (simulating an edit)."""
        self.pages[page_id]["last_edited_time"] = last_edited_time

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

        # ---- POST /v1/databases/{id}/query ---------------------------
        if request.method == "POST" and path.endswith("/query") and "/databases/" in path:
            database_id = path.split("/databases/", 1)[1].split("/", 1)[0]
            body = json.loads(request.content.decode("utf-8") or "{}")
            return self._handle_database_query(database_id, body)

        # ---- GET /v1/pages/{id} -------------------------------------
        if request.method == "GET" and "/pages/" in path:
            page_id = path.rsplit("/", 1)[-1]
            rec = self.pages.get(page_id)
            if rec is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=rec)

        # ---- GET /v1/blocks/{id}/children ---------------------------
        if request.method == "GET" and path.endswith("/children") and "/blocks/" in path:
            page_id = path.split("/blocks/", 1)[1].rsplit("/", 1)[0]
            qs = parse_qs(url.query)
            start_cursor = qs.get("start_cursor", [None])[0]
            page_size = int(qs.get("page_size", ["100"])[0])
            return self._handle_blocks_children(page_id, start_cursor, page_size)

        return httpx.Response(404, text=f"no mock for {request.method} {path}")

    # ---- handlers -----------------------------------------------------
    def _handle_database_query(
        self, database_id: str, body: dict[str, Any]
    ) -> httpx.Response:
        member_ids = list(self.database_members.get(database_id, []))
        # Apply on_or_after filter if present.
        on_or_after = None
        flt = body.get("filter") or {}
        if (
            flt.get("timestamp") == "last_edited_time"
            and "last_edited_time" in flt
        ):
            on_or_after = flt["last_edited_time"].get("on_or_after")
        if on_or_after:
            member_ids = [
                pid
                for pid in member_ids
                if self.pages.get(pid, {}).get("last_edited_time", "") >= on_or_after
            ]

        # Pagination: start_cursor is the next page's first index, encoded as
        # the string form of an integer. page_size limits results per call.
        page_size = int(body.get("page_size", 100))
        start_idx = 0
        start_cursor = body.get("start_cursor")
        if start_cursor:
            start_idx = int(start_cursor)
        chunk = member_ids[start_idx : start_idx + page_size]
        has_more = (start_idx + page_size) < len(member_ids)
        next_cursor = str(start_idx + page_size) if has_more else None
        results = [self.pages[pid] for pid in chunk]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "results": results,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        )

    def _handle_blocks_children(
        self, page_id: str, start_cursor: str | None, page_size: int
    ) -> httpx.Response:
        blocks = self.blocks.get(page_id, [])
        start_idx = 0 if not start_cursor else int(start_cursor)
        chunk = blocks[start_idx : start_idx + page_size]
        has_more = (start_idx + page_size) < len(blocks)
        next_cursor = str(start_idx + page_size) if has_more else None
        return httpx.Response(
            200,
            json={
                "object": "list",
                "results": chunk,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        )


def make_notion_mock() -> _NotionMockHandle:
    """Standalone factory — seed a default database with two pages."""
    handle = _NotionMockHandle()
    database_id = handle.valid_config["database_id"]
    handle.add_page(
        page_id="11111111-1111-1111-1111-111111111111",
        title="First Page",
        body_blocks=["Hello chunkshop", "second paragraph"],
        last_edited_time="2026-05-25T12:00:00.000Z",
        database_id=database_id,
    )
    handle.add_page(
        page_id="22222222-2222-2222-2222-222222222222",
        title="Second Page",
        body_blocks=["Notion content here"],
        last_edited_time="2026-05-25T13:00:00.000Z",
        database_id=database_id,
    )
    return handle


@pytest.fixture
def notion_mock():
    """Default Notion fixture: 2 pages under one database."""
    return make_notion_mock()


__all__ = ["notion_mock", "make_notion_mock"]
