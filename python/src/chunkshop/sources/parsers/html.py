from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError


class HTMLParser:
    supported_extensions = ["html", "htm"]

    def parse(self, path: Path) -> str:
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
            return soup.get_text(separator="\n", strip=True)
        except Exception as exc:
            raise ParserError(f"failed to parse HTML {path}: {exc}") from exc
