from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError


class PDFParser:
    supported_extensions = ["pdf"]

    def parse(self, path: Path) -> str:
        try:
            import pypdf
        except (ImportError, TypeError) as exc:  # TypeError when monkeypatched to None
            raise RuntimeError(
                "PDF parsing requires pypdf. Install with `pip install chunkshop[pdf]`."
            ) from exc
        try:
            reader = pypdf.PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ParserError(f"failed to parse PDF {path}: {exc}") from exc
