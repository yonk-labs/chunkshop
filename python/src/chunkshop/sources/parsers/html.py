from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError
from chunkshop.sources.parsers.result import ParseResult

#: Elements whose content is visual or externally-loaded — the HTML analogue
#: of a scanned PDF page. A page built only from these has content a text
#: extractor cannot reach, which is a derivation failure rather than an empty
#: document. Deliberately NOT "the body has any element at all":
#: ``<html><body></body></html>`` is legitimately empty and must stay quiet.
_EMBEDDED_TAGS = (
    "img",
    "svg",
    "canvas",
    "video",
    "audio",
    "picture",
    "object",
    "embed",
    "iframe",
)


class HTMLParser:
    supported_extensions = ["html", "htm"]

    def parse(self, path: Path) -> str | ParseResult:
        try:
            from bs4 import BeautifulSoup
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "HTML parsing requires beautifulsoup4. Install with `pip install chunkshop[html]`."
            ) from exc
        try:
            soup = BeautifulSoup(
                path.read_text(encoding="utf-8", errors="replace"), "html.parser"
            )
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            embedded = soup.find_all(_EMBEDDED_TAGS) if not text.strip() else []
        except Exception as exc:
            raise ParserError(f"failed to parse HTML {path}: {exc}") from exc
        if not text.strip() and embedded:
            kinds = sorted({t.name for t in embedded})
            return ParseResult(
                text=text,
                had_content=(
                    f"{len(embedded)} embedded element(s) ({', '.join(kinds)})"
                ),
            )
        return text
