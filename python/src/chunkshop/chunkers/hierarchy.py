from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.config import HierarchyChunker as Cfg
from chunkshop.sources.base import Document

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)$", re.MULTILINE)


class HierarchyChunker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        headings = list(_HEADING.finditer(text))
        if not headings:
            prefix = doc.title or ""
            body = text.strip()
            embedded = f"{prefix}\n\n{body}".strip() if (prefix and self.cfg.prefix_heading) else body
            return [Chunk(
                doc_id=doc.id,
                seq_num=0,
                original_content=body,
                embedded_content=embedded,
                metadata={"strategy": "hierarchy", "heading": prefix},
            )]
        chunks: list[Chunk] = []
        if headings[0].start() > 0:
            body = text[: headings[0].start()].strip()
            if len(body) >= self.cfg.min_section_chars:
                prefix = doc.title or ""
                embedded = f"{prefix}\n\n{body}".strip() if (prefix and self.cfg.prefix_heading) else body
                chunks.append(Chunk(
                    doc_id=doc.id,
                    seq_num=len(chunks),
                    original_content=body,
                    embedded_content=embedded,
                    metadata={"strategy": "hierarchy", "heading": prefix},
                ))
        for i, m in enumerate(headings):
            heading_text = m.group(2).strip()
            start = m.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[start:end].strip()
            if len(body) < self.cfg.min_section_chars:
                continue
            embedded = f"{heading_text}\n\n{body}" if self.cfg.prefix_heading else body
            chunks.append(Chunk(
                doc_id=doc.id,
                seq_num=len(chunks),
                original_content=body,
                embedded_content=embedded,
                metadata={"strategy": "hierarchy", "heading": heading_text},
            ))
        return chunks
