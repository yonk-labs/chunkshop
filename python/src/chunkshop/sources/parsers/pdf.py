from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError
from chunkshop.sources.parsers.result import ParseResult


class PDFParser:
    supported_extensions = ["pdf"]

    def parse(self, path: Path) -> str | ParseResult:
        """Extract text page by page.

        Returns a bare ``str`` on the happy path — the legacy ``FileParser``
        contract, unchanged. It escalates to a ``ParseResult`` in exactly one
        case: the document has pages and none of them yielded a character.
        That is F5's canonical case (a scanned page with no text layer), and
        the ``had_content`` claim is what turns it into a loud failure
        instead of a silently-indexed empty document.

        Note ``page.extract_text() or ""``: pypdf returns ``None`` for a page
        with no text layer. The ``or ""`` is still needed to join the pages,
        but it is no longer the end of the story — the page COUNT is now
        carried out of this function so the caller can tell "a PDF with three
        blank pages" apart from "a file with nothing in it".
        """
        try:
            import pypdf
        except (ImportError, TypeError) as exc:  # TypeError when monkeypatched to None
            raise RuntimeError(
                "PDF parsing requires pypdf. Install with `pip install chunkshop[pdf]`."
            ) from exc
        try:
            reader = pypdf.PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise ParserError(f"failed to parse PDF {path}: {exc}") from exc
        text = "\n".join(pages)
        if not text.strip() and pages:
            return ParseResult(
                text=text, had_content=f"{len(pages)} page(s)"
            )
        return text
