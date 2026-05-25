"""Tests for chunkshop.sources.http.HttpSource against an in-process HTTP server."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import pytest

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.http import HttpSource


HTML_PAGE = (
    "<!DOCTYPE html><html><head><title>Hello Page</title></head>"
    "<body>Hello world.</body></html>"
)
PLAIN_TEXT = "just plain text, no HTML, no title."
SITEMAP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>{u1}</loc></url>"
    "<url><loc>{u2}</loc></url>"
    "</urlset>"
)


class _Handler(BaseHTTPRequestHandler):
    # Suppress the noisy default access log.
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return

    def do_GET(self):  # noqa: N802 - stdlib signature
        if self.path == "/html":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/text":
            body = PLAIN_TEXT.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/sitemap.xml":
            host = f"http://{self.headers.get('Host')}"
            xml = SITEMAP_XML.format(u1=f"{host}/html", u2=f"{host}/text").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(xml)))
            self.end_headers()
            self.wfile.write(xml)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def server() -> Iterator[str]:
    """Spin up an HTTPServer on a random port; yield the base URL.

    Stops the server in teardown. Skips the test if port binding fails (e.g.,
    sandboxed CI without local networking).
    """
    try:
        httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    except OSError as exc:
        pytest.skip(f"can't bind local HTTP test server: {exc}")
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_http_source_fetches_two_urls_with_correct_metadata(server):
    cfg = Cfg(type="http", urls=[f"{server}/html", f"{server}/text"])
    docs = list(HttpSource(cfg).iter_documents())
    assert len(docs) == 2

    html, text = docs[0], docs[1]
    assert html.id == f"{server}/html"
    assert html.title == "Hello Page"
    assert "Hello world." in html.content
    assert html.metadata["url"] == html.id
    assert html.metadata["status_code"] == 200
    assert "text/html" in html.metadata["content_type"]

    assert text.id == f"{server}/text"
    assert text.title is None
    assert text.content == PLAIN_TEXT
    assert text.metadata["status_code"] == 200
    assert "text/plain" in text.metadata["content_type"]


def test_http_source_walks_sitemap(server):
    cfg = Cfg(type="http", urls=[], sitemap=f"{server}/sitemap.xml")
    docs = list(HttpSource(cfg).iter_documents())
    assert len(docs) == 2
    ids = {d.id for d in docs}
    assert ids == {f"{server}/html", f"{server}/text"}


def test_http_source_dedups_urls_against_sitemap(server):
    # Same URL in both — should be fetched once.
    cfg = Cfg(type="http", urls=[f"{server}/html"], sitemap=f"{server}/sitemap.xml")
    docs = list(HttpSource(cfg).iter_documents())
    # /html appears in both urls and sitemap; /text only via sitemap. Total = 2.
    assert len(docs) == 2
    ids = [d.id for d in docs]
    # First-occurrence ordering: /html (from urls) then /text (from sitemap).
    assert ids[0] == f"{server}/html"
    assert ids[1] == f"{server}/text"


def test_http_source_skips_non_2xx_with_warning(server, caplog):
    """The crawler-aware HttpSource no longer fail-fasts on a single 404 — it
    logs a warning and continues so one bad link doesn't kill a whole crawl.

    The old behavior (RuntimeError on non-2xx) was correct for a *single-URL*
    fetcher but wrong for the depth-bounded crawler this source became when
    ``crawl_depth`` was added. The pydantic model still accepts the old
    minimal ``urls=[...]`` shape — just the failure mode shifted from raise
    to skip-with-warning.
    """
    import logging
    cfg = Cfg(type="http", urls=[f"{server}/missing"], request_delay_seconds=0)
    with caplog.at_level(logging.WARNING):
        docs = list(HttpSource(cfg).iter_documents())
    assert docs == []
    assert any("404" in r.message or "status" in r.message.lower()
               for r in caplog.records), "expected a warning about the 404"
