from __future__ import annotations
import re
from dataclasses import replace

from chunkshop.config import HeadingBoundaryFramerConfig
from chunkshop.sources.base import Document


class HeadingBoundaryFramer:
    """Split a Document on a markdown heading pattern.

    Each framed doc's ``title`` is the heading text (when ``title_from_heading=True``).
    Pre-heading preamble is emitted as frame 0 if non-empty.
    """

    def __init__(self, cfg: HeadingBoundaryFramerConfig):
        self.cfg = cfg
        # Full heading line matcher: anchor + pattern + rest of line
        self._heading_re = re.compile(cfg.pattern + r".+$", re.MULTILINE)

    def frame(self, raw: Document) -> list[Document]:
        content = raw.content
        matches = list(self._heading_re.finditer(content))
        if not matches:
            meta = dict(raw.metadata or {})
            meta["framer"] = "heading_boundary"
            meta["frame_seq"] = 0
            return [replace(raw, metadata=meta)]

        frames: list[Document] = []
        # Preamble before first heading
        if matches[0].start() > 0:
            preamble = content[: matches[0].start()].strip()
            if preamble:
                meta = dict(raw.metadata or {})
                meta["framer"] = "heading_boundary"
                meta["frame_seq"] = 0
                frames.append(replace(
                    raw, id=f"{raw.id}#0", content=preamble, metadata=meta,
                ))

        # One frame per heading-delimited section
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            heading_line = m.group(0).strip()
            heading_text = re.sub(self.cfg.pattern, "", heading_line).strip()
            body = content[start:end].strip()
            full = f"{heading_line}\n\n{body}" if body else heading_line
            meta = dict(raw.metadata or {})
            meta["framer"] = "heading_boundary"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                title=heading_text if self.cfg.title_from_heading else raw.title,
                content=full,
                metadata=meta,
            ))
        return frames
