from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError


class DOCXParser:
    supported_extensions = ["docx"]

    def parse(self, path: Path) -> str:
        try:
            import docx
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "DOCX parsing requires python-docx. Install with `pip install chunkshop[docx]`."
            ) from exc
        try:
            d = docx.Document(str(path))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception as exc:
            raise ParserError(f"failed to parse DOCX {path}: {exc}") from exc
