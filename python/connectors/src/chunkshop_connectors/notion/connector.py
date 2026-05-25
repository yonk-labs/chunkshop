"""Verified Notion connector (integration-token auth).

Walks a Notion workspace via the REST API and yields one chunkshop
``Document`` per page. Auth is via a Notion *integration token* —
not OAuth — supplied either by config (``token``) or by the
``NOTION_TOKEN`` env var. In Notion v1, end-users mint a token in
the workspace's integration settings and share specific pages /
databases with that integration; the connector inherits its access
from that sharing.

Endpoints consumed
------------------
* ``POST /v1/databases/{database_id}/query``
    Paginate via the response's ``next_cursor`` token. Used when
    ``database_id`` is configured. Filter body carries
    ``filter.timestamp == "last_edited_time"`` plus the cursor's
    ``on_or_after`` value to scope incremental syncs.
* ``GET /v1/pages/{page_id}``
    Fetch a single page's properties when ``page_ids`` is configured.
* ``GET /v1/blocks/{page_id}/children``
    Paginate over a page's block tree to assemble the document body.
    Block children are walked recursively for blocks marked
    ``has_children = true``.

Sync semantics
--------------
``sync_mode = SyncMode.CURSOR``. The cursor shape is::

    {"after_last_edited_time": "<ISO8601>"}

* Empty cursor → full sync; the connector advances the cursor to the
  maximum ``last_edited_time`` observed across emitted pages.
* Non-empty cursor → for ``database_id``: filter the query body with
  ``timestamp.last_edited_time.on_or_after``. For ``page_ids``: fetch
  each page and emit only those whose ``last_edited_time`` is
  strictly greater than the cursor.

Block-tree walker
-----------------
The block tree is reduced to plain text via a depth-first walk.
Block types that produce text (``paragraph``, ``heading_1/2/3``,
``bulleted_list_item``, ``numbered_list_item``, ``to_do``, ``quote``,
``callout``, ``code``) contribute one line each, with their
``rich_text`` ``plain_text`` segments joined. Structural / embed
blocks (``divider``, ``image``, ``video``, ``file``, ``embed``,
``bookmark``, ``child_database``, ``unsupported``) are skipped
silently. Nested blocks (``has_children = true``) are walked
recursively up to a hard depth cap so cycles in user content can't
DoS the connector.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Iterator, Optional

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


# Block types whose ``rich_text`` we emit verbatim. Other types
# (image / video / file / divider / embed / bookmark / unsupported /
# child_database / child_page) are skipped silently.
_TEXT_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
        "callout",
        "code",
        "toggle",
    }
)

# Hard cap on recursion depth when walking ``has_children`` blocks.
# Cycles aren't expected in the Notion data model, but a runaway
# integration token pointed at adversarial content shouldn't stack
# overflow. 16 is generous for real-world page nesting.
_MAX_BLOCK_DEPTH = 16


@verified
class NotionConnector:
    """Verified-tier Notion workspace connector (cursor sync, integration-token auth)."""

    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None:
        self.database_id: Optional[str] = config.get("database_id")
        self.page_ids: Optional[list[str]] = config.get("page_ids")
        self._explicit_token: Optional[str] = config.get("token")
        self.notion_version: str = config.get("notion_version", "2022-06-28")
        self.base_url: str = config.get("base_url", "https://api.notion.com/v1").rstrip("/")

        import httpx  # noqa: PLC0415 — keep import lazy for tier-introspection-only consumers

        self._httpx = httpx
        # Optional transport hook for tests; production leaves it None.
        self._transport: Optional[httpx.BaseTransport] = None
        self._client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

        # Cached during a sync to drive cursor_from(); see iter_documents.
        self._max_last_edited: Optional[str] = None

    def _reset_client(self) -> None:
        """Recreate the underlying httpx.Client honouring ``self._transport``.

        Tests call this after assigning ``connector._transport`` so the
        mock starts intercepting requests immediately.
        """
        self._client = self._httpx.Client(
            transport=self._transport,
            timeout=self._httpx.Timeout(30.0, connect=10.0),
        )

    def __repr__(self) -> str:  # pragma: no cover -- defensive
        return (
            f"NotionConnector(database_id={self.database_id!r}, "
            f"page_ids={self.page_ids!r}, "
            f"token={'***' if self._explicit_token else None})"
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _resolve_token(self) -> Optional[str]:
        """Resolve integration token. Config wins over env."""
        if self._explicit_token:
            return self._explicit_token
        return os.environ.get("NOTION_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Notion-Version": self.notion_version,
            "Accept": "application/json",
        }
        token = self._resolve_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get_json(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, headers=self._auth_headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        headers = dict(self._auth_headers())
        headers["Content-Type"] = "application/json"
        resp = self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Block-tree → plain text
    # ------------------------------------------------------------------
    def _rich_text_to_str(self, rich: list[dict[str, Any]]) -> str:
        # Notion's rich-text array carries a ``plain_text`` on every
        # segment — that's the human-readable rendering. Concatenate.
        return "".join(seg.get("plain_text", "") for seg in rich or [])

    def _block_to_line(self, block: dict[str, Any]) -> Optional[str]:
        btype = block.get("type")
        if btype not in _TEXT_BLOCK_TYPES:
            return None
        payload = block.get(btype) or {}
        rich = payload.get("rich_text") or payload.get("text") or []
        text = self._rich_text_to_str(rich)
        if not text:
            return None
        # Light formatting hints so the embedded text reads naturally.
        # The ``hierarchy`` chunker only cares about heading prefixes;
        # we surface them as markdown-style hashes.
        if btype == "heading_1":
            return f"# {text}"
        if btype == "heading_2":
            return f"## {text}"
        if btype == "heading_3":
            return f"### {text}"
        if btype == "bulleted_list_item":
            return f"- {text}"
        if btype == "numbered_list_item":
            return f"1. {text}"
        if btype == "to_do":
            checked = payload.get("checked", False)
            return f"- [{'x' if checked else ' '}] {text}"
        if btype == "quote":
            return f"> {text}"
        if btype == "code":
            return f"```\n{text}\n```"
        # paragraph / callout / toggle → plain line
        return text

    def _walk_blocks(self, page_id: str, depth: int = 0) -> Iterator[str]:
        """Walk a page's block tree and yield one text line per block.

        Paginates via ``start_cursor`` per the Notion API contract.
        Recurses into ``has_children`` blocks up to ``_MAX_BLOCK_DEPTH``.
        """
        if depth >= _MAX_BLOCK_DEPTH:
            return
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get_json(f"/blocks/{page_id}/children", **params)
            for block in data.get("results", []):
                line = self._block_to_line(block)
                if line:
                    yield line
                if block.get("has_children"):
                    child_id = block.get("id")
                    if child_id:
                        yield from self._walk_blocks(child_id, depth=depth + 1)
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not cursor:
                return

    # ------------------------------------------------------------------
    # Page → Document
    # ------------------------------------------------------------------
    def _page_title(self, page: dict[str, Any]) -> str:
        """Best-effort extraction of a page title from its properties.

        Notion pages can carry many properties; the title lives in the
        one whose ``type == "title"``. Database pages have user-named
        title columns ("Name", "Title", etc.) so we can't key on the
        property name — only on the type.
        """
        props = page.get("properties") or {}
        for value in props.values():
            if value.get("type") == "title":
                return self._rich_text_to_str(value.get("title") or [])
        return page.get("id", "<untitled>")

    def _page_parent_type(self, page: dict[str, Any]) -> str:
        parent = page.get("parent") or {}
        return parent.get("type", "unknown")

    def _page_to_document(self, page: dict[str, Any]) -> Document:
        page_id = page["id"]
        title = self._page_title(page) or page_id
        lines = list(self._walk_blocks(page_id))
        content = "\n".join(lines)
        last_edited = page.get("last_edited_time")

        # Track the max last_edited_time across the run so cursor_from()
        # can advance monotonically. Document is frozen — bake it into
        # metadata rather than mutate post-yield.
        if last_edited is not None and (
            self._max_last_edited is None or last_edited > self._max_last_edited
        ):
            self._max_last_edited = last_edited

        return Document(
            id=page_id,
            content=content,
            title=title,
            metadata={
                "notion_id": page_id,
                "last_edited_time": last_edited,
                "created_time": page.get("created_time"),
                "parent_type": self._page_parent_type(page),
                # max_last_edited is the cursor-advancement field. Every
                # doc in a single sync carries the *same* (most recent)
                # value once the sync completes; cursor_from() reads it.
                "max_last_edited_time": last_edited,
            },
        )

    # ------------------------------------------------------------------
    # Database / page-list iteration
    # ------------------------------------------------------------------
    def _query_database(
        self, *, on_or_after: Optional[str] = None
    ) -> Iterator[dict[str, Any]]:
        """Paginate ``POST /databases/{id}/query`` and yield page records."""
        assert self.database_id is not None  # caller's guard
        cursor: Optional[str] = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            if on_or_after:
                body["filter"] = {
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"on_or_after": on_or_after},
                }
            data = self._post_json(
                f"/databases/{self.database_id}/query", body=body
            )
            for page in data.get("results", []):
                yield page
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not cursor:
                return

    def _fetch_page(self, page_id: str) -> dict[str, Any]:
        return self._get_json(f"/pages/{page_id}")

    # ------------------------------------------------------------------
    # Public Source / IncrementalSource surface
    # ------------------------------------------------------------------
    def iter_documents(self) -> Iterator[Document]:
        # Build doc list eagerly so _max_last_edited is the final
        # max across the whole run before any doc is yielded — every
        # doc's metadata then carries the same advancement value.
        docs: list[Document] = []
        if self.database_id:
            for page in self._query_database():
                docs.append(self._page_to_document(page))
        elif self.page_ids:
            for pid in self.page_ids:
                page = self._fetch_page(pid)
                docs.append(self._page_to_document(page))

        max_le = self._max_last_edited
        for d in docs:
            # Rewrite each Document's metadata so max_last_edited_time
            # is the run-wide max, not the per-page value. Document is
            # frozen — produce a new instance.
            meta = dict(d.metadata or {})
            meta["max_last_edited_time"] = max_le
            yield Document(
                id=d.id,
                content=d.content,
                title=d.title,
                metadata=meta,
                fingerprint=d.fingerprint,
            )

    # ---- IncrementalSource -------------------------------------------
    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterable[Document]:
        prior = cursor.get("after_last_edited_time")
        if not prior:
            yield from self.iter_documents()
            return

        docs: list[Document] = []
        if self.database_id:
            for page in self._query_database(on_or_after=prior):
                le = page.get("last_edited_time")
                # Notion's on_or_after is inclusive — skip pages whose
                # last_edited_time exactly equals the cursor since we
                # already emitted them on the prior sync.
                if le is not None and le <= prior:
                    continue
                docs.append(self._page_to_document(page))
        elif self.page_ids:
            for pid in self.page_ids:
                page = self._fetch_page(pid)
                le = page.get("last_edited_time")
                if le is None or le <= prior:
                    continue
                docs.append(self._page_to_document(page))

        max_le = self._max_last_edited or prior
        for d in docs:
            meta = dict(d.metadata or {})
            meta["max_last_edited_time"] = max_le
            yield Document(
                id=d.id,
                content=d.content,
                title=d.title,
                metadata=meta,
                fingerprint=d.fingerprint,
            )

    def cursor_from(self, last_document: Document) -> dict:
        meta = last_document.metadata or {}
        max_le = meta.get("max_last_edited_time")
        if max_le is None:
            return {}
        return {"after_last_edited_time": max_le}
