"""Verified RSS / Atom connector.

This is a clean-room implementation rather than a wholesale lift of
RAGFlow's ``common/data_source/rss_connector.py``. The chunkshop
verified-tier contract is narrower than RAGFlow's upstream: we
only need to fetch one feed URL via ``feedparser``, iterate
entries, and yield chunkshop ``Document``s whose fingerprint is the
entry's GUID/id (or fall back to its URL + updated timestamp).

The connector accepts a single ``url`` (the feed) plus optional
``timeout`` and ``user_agent`` overrides. Use multiple connector
cells if you need to ingest multiple feeds.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


@verified
class RssConnector:
    """Verified-tier RSS / Atom feed connector.

    Sync mode is ``FINGERPRINT``: each entry's GUID (or URL+updated)
    is emitted as the Document fingerprint so chunkshop's fingerprint
    sync mode can skip unchanged entries.
    """

    sync_mode = SyncMode.FINGERPRINT

    def __init__(self, config: dict[str, Any]) -> None:
        self.url: str = config["url"]
        self.timeout: int = config.get("timeout", 30)
        self.user_agent: str | None = config.get("user_agent")

    def iter_documents(self) -> Iterator[Document]:
        # Lazy import — users who don't have the [rss] extra can still
        # `import chunkshop_connectors.rss` to read the registry.
        import feedparser  # noqa: PLC0415

        # feedparser handles redirects, retries, and conditional GET via
        # its own internals. We pass through the user-agent override but
        # otherwise stay with library defaults.
        request_headers: dict[str, str] = {}
        if self.user_agent:
            request_headers["User-Agent"] = self.user_agent
        parsed = feedparser.parse(self.url, request_headers=request_headers or None)

        # feedparser sets `.bozo` on parse errors. We log but don't raise
        # — partial feeds still yield usable entries.
        if getattr(parsed, "bozo", 0):
            logger.warning(
                "rss: feed at %s had parse warnings: %s",
                self.url,
                getattr(parsed, "bozo_exception", "unknown"),
            )

        for entry in getattr(parsed, "entries", []):
            yield self._entry_to_document(entry)

    @staticmethod
    def _entry_to_document(entry: Any) -> Document:
        # Title: feedparser exposes both .title and entry["title"] forms;
        # prefer attribute access since it's stable across feed dialects.
        title = getattr(entry, "title", None) or "(untitled)"

        # Content priority: full-content (Atom) > summary (RSS) > empty.
        # feedparser maps both atom:content and content:encoded into the
        # `content` list — we concatenate value fields.
        content_chunks: list[str] = []
        contents = getattr(entry, "content", None) or []
        for c in contents:
            val = getattr(c, "value", None) or (c.get("value") if isinstance(c, dict) else None)
            if val:
                content_chunks.append(val)
        if not content_chunks:
            summary = getattr(entry, "summary", None)
            if summary:
                content_chunks.append(summary)
        content = "\n\n".join(content_chunks)

        # ID: prefer the entry's GUID/id; fall back to the link.
        entry_id = (
            getattr(entry, "id", None)
            or getattr(entry, "guid", None)
            or getattr(entry, "link", None)
            or title
        )

        # Fingerprint: GUID alone for stable feeds, falling back to a
        # composite if there's no GUID.
        fingerprint = (
            getattr(entry, "id", None)
            or getattr(entry, "guid", None)
            or f"{getattr(entry, 'link', '')}|{getattr(entry, 'updated', '')}"
            or None
        )

        metadata: dict[str, Any] = {}
        link = getattr(entry, "link", None)
        if link:
            metadata["link"] = link
        published = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if published:
            metadata["published"] = published
        author = getattr(entry, "author", None)
        if author:
            metadata["author"] = author

        return Document(
            id=str(entry_id),
            content=content,
            title=title,
            metadata=metadata or None,
            fingerprint=str(fingerprint) if fingerprint else None,
        )
