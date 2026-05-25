"""Hermetic feedparser mock for the rss connector.

Monkeypatches ``sys.modules['feedparser']`` with a stub whose
``parse(url, ...)`` returns a canned feed object built from
in-memory test data. No HTTP traffic — pure offline.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest


class _Entry:
    """Mimics feedparser's ``FeedParserDict`` for entries we care about."""

    def __init__(
        self,
        *,
        id: str,
        title: str,
        link: str,
        summary: str,
        published: str,
        author: str | None = None,
        content: list[dict[str, str]] | None = None,
    ):
        self.id = id
        self.title = title
        self.link = link
        self.summary = summary
        self.published = published
        if author is not None:
            self.author = author
        if content is not None:
            self.content = content


class _FakeFeed:
    def __init__(self, entries: list[_Entry], bozo: int = 0):
        self.entries = entries
        self.bozo = bozo
        self.bozo_exception = None


class _RssMockHandle:
    def __init__(self, feed: _FakeFeed, config: dict[str, Any]):
        self.feed = feed
        self.valid_config = config


@pytest.fixture
def rss_mock(monkeypatch):
    """Provide a fake feedparser module + a valid_config for the rss connector.

    Seeds three entries — one with a full Atom content list, one with
    only summary, one with neither id nor content (exercises the
    fallback paths).
    """
    entries = [
        _Entry(
            id="urn:entry:1",
            title="First post",
            link="https://example.com/1",
            summary="short summary one",
            published="Mon, 01 Jan 2024 00:00:00 GMT",
            author="alice",
            content=[{"value": "<p>Full body of post one</p>"}],
        ),
        _Entry(
            id="urn:entry:2",
            title="Second post",
            link="https://example.com/2",
            summary="just a summary",
            published="Tue, 02 Jan 2024 00:00:00 GMT",
        ),
        _Entry(
            id="",
            title="Third post",
            link="https://example.com/3",
            summary="",
            published="Wed, 03 Jan 2024 00:00:00 GMT",
        ),
    ]
    feed = _FakeFeed(entries)

    fake_feedparser = types.ModuleType("feedparser")
    fake_feedparser.parse = lambda url, **kwargs: feed
    monkeypatch.setitem(sys.modules, "feedparser", fake_feedparser)

    handle = _RssMockHandle(
        feed=feed,
        config={"url": "https://example.com/feed.xml"},
    )
    return handle
