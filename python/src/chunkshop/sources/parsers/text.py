from __future__ import annotations

from pathlib import Path


class TextParser:
    supported_extensions = ["txt", "md", "markdown", "rst", "log", "csv", "tsv", ""]

    def __init__(self, encoding: str = "utf-8"):
        self.encoding = encoding

    def parse(self, path: Path) -> str:
        return path.read_text(encoding=self.encoding, errors="replace")
