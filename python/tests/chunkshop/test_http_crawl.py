# tests/chunkshop/test_http_crawl.py
"""HttpSource depth-bounded crawl + ETag/Last-Modified cursor tests.

Hermetic — every test wires an ``httpx.MockTransport`` into the source via the
optional ``transport=`` keyword, so no network is ever touched.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.base import IncrementalSource, SyncMode
from chunkshop.testing import (
    assert_cursor_advances,
    assert_idempotent_on_re_emit,
    merge_cursor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _transport(routes):
    """Build an ``httpx.MockTransport`` from a {url: callable_or_response} map.

    Callable signature: ``(request: httpx.Request) -> httpx.Response``. A bare
    Response value is returned for every request to that URL.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Try the exact URL first, then drop a trailing slash, then strip query.
        for key in (url, url.rstrip("/"), str(request.url.copy_with(query=None))):
            if key in routes:
                v = routes[key]
                return v(request) if callable(v) else v
        return httpx.Response(404, text=f"unknown route {url}")

    return httpx.MockTransport(handler)


def _html(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        text=body,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


def _new_source(urls, transport, **kwargs):
    from chunkshop.sources.http import HttpSource

    cfg_kwargs = {
        "request_delay_seconds": 0.0,  # tests must not actually sleep
        "respect_robots": False,         # opt-in per-test
    }
    cfg_kwargs.update(kwargs)
    cfg = Cfg(type="http", urls=urls, **cfg_kwargs)
    return HttpSource(cfg, transport=transport)


# ---------------------------------------------------------------------------
# 1. depth=0 is the current behavior
# ---------------------------------------------------------------------------


def test_http_depth_zero_fetches_only_seeds():
    routes = {
        "http://a.test/": _html("<html><body><a href='/x'>x</a></body></html>"),
        "http://a.test/x": _html("<html><body>x page</body></html>"),
    }
    src = _new_source(["http://a.test/"], _transport(routes), crawl_depth=0)
    docs = list(src.iter_documents())
    assert [d.id for d in docs] == ["http://a.test/"]


# ---------------------------------------------------------------------------
# 2. depth=1 follows links once
# ---------------------------------------------------------------------------


def test_http_depth_one_follows_links():
    index_html = (
        "<html><body>"
        "<a href='/page-a'>A</a>"
        "<a href='/page-b'>B</a>"
        "</body></html>"
    )
    routes = {
        "http://a.test/": _html(index_html),
        "http://a.test/page-a": _html("<html><body>a</body></html>"),
        "http://a.test/page-b": _html("<html><body>b</body></html>"),
    }
    src = _new_source(["http://a.test/"], _transport(routes), crawl_depth=1)
    docs = list(src.iter_documents())
    assert {d.id for d in docs} == {
        "http://a.test/",
        "http://a.test/page-a",
        "http://a.test/page-b",
    }


# ---------------------------------------------------------------------------
# 3. depth=2 — link of a link
# ---------------------------------------------------------------------------


def test_http_depth_two_recursive():
    routes = {
        "http://a.test/": _html("<a href='/l1'>l1</a>"),
        "http://a.test/l1": _html("<a href='/l2'>l2</a>"),
        "http://a.test/l2": _html("leaf"),
        # depth=2 must NOT reach this:
        "http://a.test/l3": _html("should not be reached"),
    }
    src = _new_source(["http://a.test/"], _transport(routes), crawl_depth=2)
    docs = list(src.iter_documents())
    ids = {d.id for d in docs}
    assert ids == {"http://a.test/", "http://a.test/l1", "http://a.test/l2"}


# ---------------------------------------------------------------------------
# 4. visited-set prevents cycles
# ---------------------------------------------------------------------------


def test_http_visited_set_prevents_cycles():
    routes = {
        "http://a.test/a": _html("<a href='/b'>b</a>"),
        "http://a.test/b": _html("<a href='/a'>back</a>"),
    }
    src = _new_source(["http://a.test/a"], _transport(routes), crawl_depth=5)
    docs = list(src.iter_documents())
    # /a and /b each visited exactly once
    ids = sorted(d.id for d in docs)
    assert ids == ["http://a.test/a", "http://a.test/b"]


# ---------------------------------------------------------------------------
# 5. same-host filter by default
# ---------------------------------------------------------------------------


def test_http_same_host_only_by_default():
    routes = {
        "http://a.test/": _html(
            "<a href='/local'>local</a>"
            "<a href='http://external.example.org/'>ext</a>"
        ),
        "http://a.test/local": _html("local page"),
        "http://external.example.org/": _html("external"),
    }
    src = _new_source(["http://a.test/"], _transport(routes), crawl_depth=1)
    docs = list(src.iter_documents())
    ids = {d.id for d in docs}
    assert ids == {"http://a.test/", "http://a.test/local"}
    assert "http://external.example.org/" not in ids


# ---------------------------------------------------------------------------
# 6. allow_external override
# ---------------------------------------------------------------------------


def test_http_allow_external():
    routes = {
        "http://a.test/": _html(
            "<a href='/local'>local</a>"
            "<a href='http://external.example.org/'>ext</a>"
        ),
        "http://a.test/local": _html("local page"),
        "http://external.example.org/": _html("external"),
    }
    src = _new_source(
        ["http://a.test/"],
        _transport(routes),
        crawl_depth=1,
        allow_external=True,
    )
    docs = list(src.iter_documents())
    ids = {d.id for d in docs}
    assert ids == {
        "http://a.test/",
        "http://a.test/local",
        "http://external.example.org/",
    }


# ---------------------------------------------------------------------------
# 7. max_pages caps a runaway crawl
# ---------------------------------------------------------------------------


def test_http_max_pages_caps_runaway():
    # Index points to 50 sibling pages
    links = "".join(f"<a href='/p{i}'>p{i}</a>" for i in range(50))
    routes = {"http://a.test/": _html(links)}
    for i in range(50):
        routes[f"http://a.test/p{i}"] = _html(f"page {i}")
    src = _new_source(
        ["http://a.test/"],
        _transport(routes),
        crawl_depth=1,
        max_pages=10,
    )
    docs = list(src.iter_documents())
    assert len(docs) == 10


# ---------------------------------------------------------------------------
# 8. HTML stripped to text
# ---------------------------------------------------------------------------


def test_http_html_to_text():
    body = (
        "<html><head><title>T</title>"
        "<style>body{}</style></head>"
        "<body><script>var x=1</script>"
        "<p>Hello <b>world</b></p></body></html>"
    )
    routes = {"http://a.test/": _html(body)}
    src = _new_source(["http://a.test/"], _transport(routes), crawl_depth=0)
    docs = list(src.iter_documents())
    assert len(docs) == 1
    content = docs[0].content
    assert "Hello" in content
    assert "world" in content
    # script / style bodies are removed
    assert "var x=1" not in content
    assert "body{}" not in content


# ---------------------------------------------------------------------------
# 9. Incremental — 304 Not Modified skipped
# ---------------------------------------------------------------------------


def test_http_incremental_skips_304():
    state = {"served_first": False}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url != "http://a.test/page":
            return httpx.Response(404)
        # First request: serve full body with an ETag.
        if not state["served_first"]:
            state["served_first"] = True
            return httpx.Response(
                200,
                text="<html><body>v1</body></html>",
                headers={
                    "Content-Type": "text/html",
                    "ETag": '"v1"',
                    "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                },
            )
        # Second request: conditional GET should match — return 304.
        if req.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        # Otherwise unconditional — re-serve.
        return httpx.Response(
            200,
            text="<html><body>v1</body></html>",
            headers={"Content-Type": "text/html", "ETag": '"v1"'},
        )

    src = _new_source(["http://a.test/page"], httpx.MockTransport(handler), crawl_depth=0)
    cursor = src.empty_cursor()
    first = list(src.iter_changes_since(cursor))
    assert {d.id for d in first} == {"http://a.test/page"}
    cursor = merge_cursor(src, cursor, first)
    assert cursor["http://a.test/page"]["etag"] == '"v1"'
    # nothing changed — server returns 304 — no docs
    assert list(src.iter_changes_since(cursor)) == []


# ---------------------------------------------------------------------------
# 10. Incremental — only the changed URL re-emits
# ---------------------------------------------------------------------------


def test_http_incremental_re_fetches_changed():
    state = {"etag_a": '"a1"', "etag_b": '"b1"'}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        etag_key = {"http://a.test/a": "etag_a", "http://a.test/b": "etag_b"}.get(url)
        if etag_key is None:
            return httpx.Response(404)
        current = state[etag_key]
        if req.headers.get("If-None-Match") == current:
            return httpx.Response(304)
        return httpx.Response(
            200,
            text=f"<html><body>{etag_key}={current}</body></html>",
            headers={"Content-Type": "text/html", "ETag": current},
        )

    src = _new_source(
        ["http://a.test/a", "http://a.test/b"],
        httpx.MockTransport(handler),
        crawl_depth=0,
    )
    cursor = src.empty_cursor()
    first = list(src.iter_changes_since(cursor))
    assert {d.id for d in first} == {"http://a.test/a", "http://a.test/b"}
    cursor = merge_cursor(src, cursor, first)

    # Mutate /b's etag at the server. /a unchanged → 304. /b → re-emits.
    state["etag_b"] = '"b2"'
    changed = list(src.iter_changes_since(cursor))
    assert {d.id for d in changed} == {"http://a.test/b"}
    # And merging preserves /a's previous etag entry.
    cursor = merge_cursor(src, cursor, changed)
    assert cursor["http://a.test/a"]["etag"] == '"a1"'
    assert cursor["http://a.test/b"]["etag"] == '"b2"'


# ---------------------------------------------------------------------------
# 11. Generic IncrementalSource helpers pass
# ---------------------------------------------------------------------------


def test_http_satisfies_incremental_helpers():
    def handler(req: httpx.Request) -> httpx.Response:
        # Conditional GET semantics — return 304 when client already has the ETag.
        etag = f'"{req.url.path}"'
        if req.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        return httpx.Response(
            200,
            text=f"<html><body>{req.url.path}</body></html>",
            headers={"Content-Type": "text/html", "ETag": etag},
        )

    src = _new_source(
        ["http://a.test/p1", "http://a.test/p2"],
        httpx.MockTransport(handler),
        crawl_depth=0,
    )
    assert isinstance(src, IncrementalSource)
    assert src.sync_mode == SyncMode.CURSOR
    assert_cursor_advances(src)
    # build a fresh source so the helper sees the same hermetic transport
    src2 = _new_source(
        ["http://a.test/p1", "http://a.test/p2"],
        httpx.MockTransport(handler),
        crawl_depth=0,
    )
    assert_idempotent_on_re_emit(src2)


# ---------------------------------------------------------------------------
# 12. Binary MIMEs are skipped with a warning
# ---------------------------------------------------------------------------


def test_http_binary_skipped(caplog):
    routes = {
        "http://a.test/image.png": httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n",
            headers={"Content-Type": "image/png"},
        ),
        "http://a.test/page.txt": httpx.Response(
            200,
            text="hello world",
            headers={"Content-Type": "text/plain"},
        ),
    }
    src = _new_source(
        ["http://a.test/image.png", "http://a.test/page.txt"],
        _transport(routes),
        crawl_depth=0,
    )
    with caplog.at_level(logging.WARNING):
        docs = list(src.iter_documents())
    # image is skipped; text/plain comes through
    assert {d.id for d in docs} == {"http://a.test/page.txt"}
    assert any("image/png" in r.message or "binary" in r.message.lower()
               for r in caplog.records), "expected a warning about the binary MIME"


# ---------------------------------------------------------------------------
# 13. Custom User-Agent header is sent
# ---------------------------------------------------------------------------


def test_http_respects_user_agent():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ua"] = req.headers.get("User-Agent")
        return httpx.Response(200, text="ok", headers={"Content-Type": "text/plain"})

    src = _new_source(
        ["http://a.test/"],
        httpx.MockTransport(handler),
        crawl_depth=0,
        user_agent="chunkshop-tester/9.9",
    )
    list(src.iter_documents())
    assert seen["ua"] == "chunkshop-tester/9.9"


# ---------------------------------------------------------------------------
# 14. robots.txt is honored
# ---------------------------------------------------------------------------


def test_http_robots_txt_respected():
    robots = "User-agent: *\nDisallow: /private\n"
    routes = {
        "http://a.test/robots.txt": httpx.Response(
            200, text=robots, headers={"Content-Type": "text/plain"}
        ),
        "http://a.test/": _html(
            "<a href='/public'>p</a><a href='/private/secret'>x</a>"
        ),
        "http://a.test/public": _html("public ok"),
        "http://a.test/private/secret": _html("SHOULD NOT BE FETCHED"),
    }
    src = _new_source(
        ["http://a.test/"],
        _transport(routes),
        crawl_depth=1,
        respect_robots=True,
    )
    docs = list(src.iter_documents())
    ids = {d.id for d in docs}
    # /private/secret was discovered as a link but robots.txt forbids it
    assert "http://a.test/private/secret" not in ids
    assert "http://a.test/public" in ids


# ---------------------------------------------------------------------------
# Pydantic-model regression: extra-forbid + bounds
# ---------------------------------------------------------------------------


def test_http_cfg_rejects_unknown_field():
    with pytest.raises(Exception):  # pydantic ValidationError
        Cfg(type="http", urls=[], totally_unknown_field=True)


def test_http_cfg_rejects_negative_depth():
    with pytest.raises(Exception):
        Cfg(type="http", urls=[], crawl_depth=-1)


def test_http_cfg_rejects_depth_above_5():
    with pytest.raises(Exception):
        Cfg(type="http", urls=[], crawl_depth=6)
