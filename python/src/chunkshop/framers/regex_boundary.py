from __future__ import annotations
import re
from dataclasses import replace

from chunkshop.config import RegexBoundaryFramerConfig
from chunkshop.sources.base import Document


class RegexBoundaryFramer:
    """Split a Document's content on a regex boundary.

    Each slice becomes one framed Document. When ``title_pattern`` is provided,
    the first capture group from matching each slice becomes the framed title.
    """

    def __init__(self, cfg: RegexBoundaryFramerConfig):
        self.cfg = cfg
        self._split_re = re.compile(cfg.split_pattern, re.MULTILINE)
        self._title_re = re.compile(cfg.title_pattern) if cfg.title_pattern else None

    def frame(self, raw: Document) -> list[Document]:
        content = raw.content
        matches = list(self._split_re.finditer(content))
        if not matches:
            meta = dict(raw.metadata or {})
            meta["framer"] = "regex_boundary"
            meta["frame_seq"] = 0
            return [replace(raw, metadata=meta)]

        frames: list[Document] = []
        for i, m in enumerate(matches):
            start = m.start() if self.cfg.body_starts_with_match else m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            if not body:
                continue
            title = raw.title
            if self._title_re:
                tm = self._title_re.search(body)
                if tm and tm.groups():
                    title = tm.group(1).strip()
            meta = dict(raw.metadata or {})
            meta["framer"] = "regex_boundary"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                content=body,
                title=title,
                metadata=meta,
            ))
        return frames
