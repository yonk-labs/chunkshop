from __future__ import annotations

from pathlib import Path

from chunkshop.sources.parsers.base import ParserError
from chunkshop.sources.parsers.result import ParseResult


class PPTXParser:
    supported_extensions = ["pptx"]

    def parse(self, path: Path) -> str | ParseResult:
        """Extract every slide's text-frame text.

        Slides are this format's page analogue, so the F5 claim is the same
        shape as ``PDFParser``'s: a deck with slides and no characters is an
        image-only deck, not an empty file.
        """
        try:
            from pptx import Presentation
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "PPTX parsing requires python-pptx. Install with `pip install chunkshop[pptx]`."
            ) from exc
        try:
            prs = Presentation(str(path))
            slide_count = 0
            out: list[str] = []
            for slide in prs.slides:
                slide_count += 1
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        out.append(shape.text_frame.text)
        except Exception as exc:
            raise ParserError(f"failed to parse PPTX {path}: {exc}") from exc
        text = "\n".join(out)
        if not text.strip() and slide_count:
            return ParseResult(
                text=text, had_content=f"{slide_count} slide(s)"
            )
        return text
