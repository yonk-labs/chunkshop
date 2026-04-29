"""HTTP source — fetch a list of URLs (and/or URLs from a sitemap) into Documents.

Stdlib-only: ``urllib.request`` for fetches, ``xml.etree.ElementTree`` for sitemap
parsing, ``re`` for HTML ``<title>`` extraction. No retries, no auth, no rate
limiting — single GET per URL, fail-fast on non-2xx responses.

Document shape per fetched URL:
    id        = url
    content   = response body (decoded as text per Content-Type charset, default utf-8)
    title     = HTML <title> tag content (case-insensitive single-pass regex), else None
    metadata  = {"url": url, "status_code": int, "content_type": str}

Sitemap support: one-level ``<urlset>`` with ``<loc>`` children. Sitemap-index
(sitemap-of-sitemaps) is intentionally NOT supported — flag for follow-up.
"""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterator

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.base import Document


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Matches Sitemap 0.9 <loc> elements without namespace plumbing.
_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.IGNORECASE | re.DOTALL)


def _fetch(url: str) -> tuple[str, int, str]:
    """GET ``url`` and return (body_text, status_code, content_type).

    Raises on non-2xx responses or network errors.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "chunkshop-http/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        if status < 200 or status >= 300:
            raise RuntimeError(f"GET {url}: status {status}")
        ctype = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
    return body, status, ctype


def _extract_title(body: str) -> str | None:
    m = _TITLE_RE.search(body)
    if not m:
        return None
    text = m.group(1).strip()
    return text or None


def _parse_sitemap(body: str) -> list[str]:
    """Return ``<loc>`` URLs from a sitemap XML body. Returns ``[]`` if parsing
    fails or no locs are present. Order: document order.
    """
    try:
        root = ET.fromstring(body)
        urls = []
        for loc in root.iter():
            tag = loc.tag.split("}", 1)[-1] if "}" in loc.tag else loc.tag
            if tag == "loc" and loc.text:
                urls.append(loc.text.strip())
        if urls:
            return urls
    except ET.ParseError:
        pass
    return [m.strip() for m in _LOC_RE.findall(body) if m.strip()]


class HttpSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self) -> Iterator[Document]:
        fetch_list: list[str] = []
        seen: set[str] = set()
        for u in self.cfg.urls:
            if u not in seen:
                fetch_list.append(u)
                seen.add(u)
        if self.cfg.sitemap:
            sm_body, _, _ = _fetch(self.cfg.sitemap)
            for u in _parse_sitemap(sm_body):
                if u not in seen:
                    fetch_list.append(u)
                    seen.add(u)

        out: list[Document] = []
        for url in fetch_list:
            body, status, ctype = _fetch(url)
            title = _extract_title(body)
            out.append(Document(
                id=url,
                content=body,
                title=title,
                metadata={"url": url, "status_code": status, "content_type": ctype},
            ))
        return iter(out)
