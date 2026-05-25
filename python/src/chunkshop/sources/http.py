"""HTTP source — depth-bounded crawl + ETag/Last-Modified incremental sync.

Behaviour summary:
    * ``crawl_depth=0`` (default) — fetch only the seed URLs (and any URLs from
      ``sitemap`` if set). Same as the legacy ``HttpSource``.
    * ``crawl_depth>=1`` — BFS crawl from the seeds, extracting ``<a href>``
      links from HTML pages. Bounded by ``crawl_depth``, ``max_pages``, a
      visited-set (so cycles are handled), and the same-host filter (override
      with ``allow_external``).
    * Incremental — implements ``IncrementalSource``. The cursor is a map
      ``{url: {"etag": "...", "last_modified": "..."}}``. On
      ``iter_changes_since(cursor)`` each URL is fetched with conditional
      ``If-None-Match`` / ``If-Modified-Since`` headers and skipped on 304.
    * Polite — minimum delay between requests (``request_delay_seconds``);
      respects ``robots.txt`` by default (one fetch per host, cached);
      identifies itself with a configurable ``User-Agent``.

Document shape per fetched URL:
    id        = url
    content   = body, HTML→text-stripped for ``text/html``
    title     = HTML ``<title>`` content (when extractable), else None
    metadata  = ``{"url", "status_code", "content_type", "etag", "last_modified"}``
    fingerprint = ETag if present (used by ``cursor_from``)

Binary MIMEs (anything that isn't ``text/*`` or ``application/json``) are
skipped with a warning. We don't try to OCR / decode them — the user pulls
PDF/DOCX/XLSX through the ``files`` source with the dedicated parsers.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.base import Document, SyncMode

log = logging.getLogger(__name__)


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Matches Sitemap 0.9 <loc> elements without namespace plumbing.
_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.IGNORECASE | re.DOTALL)

# MIMEs we treat as text. Anything else is skipped with a warning.
_TEXTY_MIMES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
)


def _normalize_url(url: str) -> str:
    """Stable normalization for visited-set / cursor key.

    - Lowercase scheme + host
    - Strip fragment
    - Empty path → ``/`` (so ``http://a.test`` and ``http://a.test/`` collapse)
    - Otherwise preserves path/query exactly.
    """
    p = urlparse(url)
    scheme = (p.scheme or "http").lower()
    host = (p.hostname or "").lower()
    if p.port:
        host = f"{host}:{p.port}"
    path = p.path or "/"
    return urlunparse((scheme, host, path, p.params, p.query, ""))


def _same_host(a: str, b: str) -> bool:
    return (urlparse(a).hostname or "").lower() == (urlparse(b).hostname or "").lower()


def _parse_sitemap(body: str) -> list[str]:
    """Return ``<loc>`` URLs from a sitemap XML body. Returns ``[]`` if nothing
    parses. Mirrors the legacy fallback for malformed XML."""
    try:
        root = ET.fromstring(body)
        urls: list[str] = []
        for loc in root.iter():
            tag = loc.tag.split("}", 1)[-1] if "}" in loc.tag else loc.tag
            if tag == "loc" and loc.text:
                urls.append(loc.text.strip())
        if urls:
            return urls
    except ET.ParseError:
        pass
    return [m.strip() for m in _LOC_RE.findall(body) if m.strip()]


def _extract_title(body: str) -> Optional[str]:
    m = _TITLE_RE.search(body)
    if not m:
        return None
    return m.group(1).strip() or None


def _strip_html(body: str) -> str:
    """HTML → text via bs4. bs4 is an ``[html]`` extra; raise if missing."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(
            "HTML→text conversion requires beautifulsoup4. "
            "Install with `pip install chunkshop[html]`."
        ) from exc
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extract_links(html_body: str, page_url: str) -> list[str]:
    """Return absolute ``<a href>`` URLs from ``html_body``.

    Uses bs4 (same dep as ``_strip_html``). Fragments are stripped via
    ``_normalize_url`` at the call site.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    out: list[str] = []
    soup = BeautifulSoup(html_body, "html.parser")
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        # Skip in-page anchors, mailto:, javascript:, tel:, etc.
        low = href.strip().lower()
        if low.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        out.append(urljoin(page_url, href))
    return out


def _content_type_root(ctype: str) -> str:
    """Return the bare MIME (no charset/params), lowercased."""
    return (ctype or "").split(";", 1)[0].strip().lower()


def _charset_of(ctype: str) -> str:
    if "charset=" in (ctype or ""):
        return ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    return "utf-8"


class HttpSource:
    """Depth-bounded HTTP crawler with ETag/Last-Modified incremental sync."""

    # The cursor is a per-URL ETag/Last-Modified map; 304 responses are skipped.
    sync_mode = SyncMode.CURSOR

    def __init__(self, cfg: Cfg, *, transport: Optional[httpx.BaseTransport] = None):
        self.cfg = cfg
        # Caller can inject an httpx.MockTransport for hermetic tests.
        self._transport = transport
        # robots.txt cache: host → RobotFileParser (or None if unavailable).
        self._robots_cache: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        # Polite-delay state — monotonic timestamp of the last outbound request.
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------ http

    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._transport,
            headers={"User-Agent": self.cfg.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )

    def _polite_wait(self) -> None:
        delay = self.cfg.request_delay_seconds
        if delay <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < delay and self._last_request_ts:
            time.sleep(delay - elapsed)
        self._last_request_ts = time.monotonic()

    def _request(
        self,
        client: httpx.Client,
        url: str,
        *,
        if_none_match: Optional[str] = None,
        if_modified_since: Optional[str] = None,
    ) -> httpx.Response:
        self._polite_wait()
        headers = {}
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        if if_modified_since:
            headers["If-Modified-Since"] = if_modified_since
        return client.get(url, headers=headers)

    # ------------------------------------------------------------- robots.txt

    def _robots_for(self, client: httpx.Client, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        if not self.cfg.respect_robots:
            return None
        p = urlparse(url)
        host_key = f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")
        if host_key in self._robots_cache:
            return self._robots_cache[host_key]
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = self._request(client, f"{host_key}/robots.txt")
            if 200 <= resp.status_code < 300 and resp.text:
                rp.parse(resp.text.splitlines())
            else:
                # Treat missing robots.txt as "allow everything", per RFC9309.
                rp = None
        except Exception as exc:
            log.debug("robots.txt fetch failed for %s: %s", host_key, exc)
            rp = None
        self._robots_cache[host_key] = rp
        return rp

    def _robots_allows(self, client: httpx.Client, url: str) -> bool:
        rp = self._robots_for(client, url)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.cfg.user_agent, url)
        except Exception:
            return True

    # --------------------------------------------------------------- fetching

    def _fetch_one(
        self,
        client: httpx.Client,
        url: str,
        cursor_entry: Optional[dict] = None,
    ) -> tuple[Optional[Document], list[str]]:
        """Fetch ``url`` and return (Document or None, discovered_links).

        - Returns ``(None, [])`` if robots.txt forbids the URL.
        - Returns ``(None, [])`` if the response is binary (warning logged).
        - Returns ``(None, [])`` for 304 Not Modified.
        - Otherwise returns a Document and any ``<a href>`` links found in
          HTML bodies (empty list for non-HTML bodies).
        """
        if not self._robots_allows(client, url):
            log.info("robots.txt disallows %s; skipping", url)
            return None, []

        ce = cursor_entry or {}
        try:
            resp = self._request(
                client,
                url,
                if_none_match=ce.get("etag"),
                if_modified_since=ce.get("last_modified"),
            )
        except Exception as exc:
            log.warning("GET %s failed: %s", url, exc)
            return None, []

        if resp.status_code == 304:
            return None, []
        if resp.status_code < 200 or resp.status_code >= 300:
            log.warning("GET %s: status %s; skipping", url, resp.status_code)
            return None, []

        ctype = resp.headers.get("Content-Type", "")
        mime = _content_type_root(ctype)
        etag = resp.headers.get("ETag")
        last_modified = resp.headers.get("Last-Modified")

        if not mime.startswith("text/") and mime not in _TEXTY_MIMES:
            log.warning(
                "Skipping binary content %s for %s (use the files source for binaries)",
                mime,
                url,
            )
            return None, []

        # Decode body — bytes → str using declared charset.
        try:
            raw = resp.text  # httpx already decodes per Content-Type charset
        except Exception:
            raw = resp.content.decode(_charset_of(ctype), errors="replace")

        title: Optional[str] = None
        links: list[str] = []
        if mime == "text/html":
            title = _extract_title(raw)
            links = _extract_links(raw, url)
            content = _strip_html(raw)
        else:
            content = raw

        meta = {
            "url": url,
            "status_code": resp.status_code,
            "content_type": ctype,
            "etag": etag,
            "last_modified": last_modified,
        }
        doc = Document(
            id=url,
            content=content,
            title=title,
            metadata=meta,
            fingerprint=etag,
        )
        return doc, links

    # -------------------------------------------------------------- discovery

    def _seed_urls(self, client: httpx.Client) -> list[str]:
        """Build the deduped, ordered list of seed URLs (cfg.urls + sitemap)."""
        seen: set[str] = set()
        out: list[str] = []
        for u in self.cfg.urls:
            n = _normalize_url(u)
            if n not in seen:
                seen.add(n)
                out.append(u)
        if self.cfg.sitemap:
            try:
                resp = self._request(client, self.cfg.sitemap)
                if 200 <= resp.status_code < 300:
                    for u in _parse_sitemap(resp.text):
                        n = _normalize_url(u)
                        if n not in seen:
                            seen.add(n)
                            out.append(u)
            except Exception as exc:
                log.warning("sitemap fetch %s failed: %s", self.cfg.sitemap, exc)
        return out

    # ------------------------------------------------------------------- BFS

    def _crawl(
        self,
        cursor: Optional[dict] = None,
    ) -> Iterator[Document]:
        """BFS crawl over seeds. Yields Documents in discovery order.

        When ``cursor`` is non-None, each URL is fetched with conditional
        headers from its cursor entry and 304s are skipped silently.
        """
        cursor = cursor or {}
        with self._client() as client:
            seeds = self._seed_urls(client)
            # Pin allowed hostnames (the set of seed hosts). Off-host links are
            # only followed when allow_external is True.
            seed_hosts = {(urlparse(s).hostname or "").lower() for s in seeds}

            visited: set[str] = set()
            # frontier holds (url, depth_remaining)
            frontier: list[tuple[str, int]] = [
                (u, self.cfg.crawl_depth) for u in seeds
            ]
            emitted = 0

            while frontier and emitted < self.cfg.max_pages:
                url, depth_left = frontier.pop(0)
                norm = _normalize_url(url)
                if norm in visited:
                    continue
                visited.add(norm)

                # Same-host filter (only matters for discovered, non-seed URLs;
                # seeds are accepted as-is).
                if (
                    not self.cfg.allow_external
                    and (urlparse(url).hostname or "").lower() not in seed_hosts
                ):
                    continue

                ce = cursor.get(url)
                doc, links = self._fetch_one(client, url, cursor_entry=ce)
                if doc is not None:
                    emitted += 1
                    yield doc
                    if emitted >= self.cfg.max_pages:
                        break

                # Only expand the frontier if we still have depth to spend.
                if depth_left > 0 and links:
                    for link in links:
                        ln = _normalize_url(link)
                        if ln in visited:
                            continue
                        if (
                            not self.cfg.allow_external
                            and (urlparse(link).hostname or "").lower() not in seed_hosts
                            # but allow if link is same-host as the page we're on
                            and not _same_host(link, url)
                        ):
                            continue
                        frontier.append((link, depth_left - 1))

    # -------------------------------------------------------------- protocol

    def iter_documents(self) -> Iterator[Document]:
        """Full crawl — equivalent to ``iter_changes_since(empty_cursor())``."""
        return self._crawl(cursor={})

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        return self._crawl(cursor=cursor or {})

    def cursor_from(self, last_document: Document) -> dict:
        """Per-doc cursor delta: ``{url: {"etag": ..., "last_modified": ...}}``.

        The consumer merges each delta into a running map; the canonical
        cursor is the accumulated full per-URL map (same shape as S3).
        """
        meta = last_document.metadata or {}
        url = meta.get("url", last_document.id)
        return {
            url: {
                "etag": meta.get("etag"),
                "last_modified": meta.get("last_modified"),
            }
        }
