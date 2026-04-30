from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.chunkers._splitting import split_to_max_chars
from chunkshop.config import HierarchyChunker as Cfg
from chunkshop.sources.base import Document

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)$", re.MULTILINE)


def _emit_section_chunks(
    body: str,
    heading_text: str,
    doc_id: str,
    start_seq: int,
    prefix_heading: bool,
    max_chars: int,
) -> list[Chunk]:
    parts = split_to_max_chars(body, max_chars) if len(body) > max_chars else [body]
    chunks: list[Chunk] = []
    for i, part in enumerate(parts):
        embedded = f"{heading_text}\n\n{part}" if (heading_text and prefix_heading) else part
        chunks.append(Chunk(
            doc_id=doc_id,
            seq_num=start_seq + i,
            original_content=part,
            embedded_content=embedded,
            metadata={
                "strategy": "hierarchy",
                "heading": heading_text,
                "section_part": i,
            },
        ))
    return chunks


class HierarchyChunker:
    def __init__(self, cfg: Cfg, build_chunker=None):
        self.cfg = cfg
        self._build_chunker = build_chunker
        self._warner = DedupedWarner("hierarchy", cfg.max_chars)

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        headings = list(_HEADING.finditer(text))
        if not headings:
            body = text.strip()
            if not body:
                return []
            chunks = _emit_section_chunks(
                body=body,
                heading_text=doc.title or "",
                doc_id=doc.id,
                start_seq=0,
                prefix_heading=self.cfg.prefix_heading,
                max_chars=self.cfg.max_chars,
            )
            return apply_if_oversize(
                chunks,
                ceiling=self.cfg.effective_max_chars(),
                if_oversize_cfg=self.cfg.if_oversize,
                chunker_name="hierarchy",
                build_chunker=self._build_chunker,
                document=doc,
                warner=self._warner,
            )
        chunks: list[Chunk] = []
        if headings[0].start() > 0:
            body = text[: headings[0].start()].strip()
            if len(body) >= self.cfg.min_section_chars:
                chunks.extend(_emit_section_chunks(
                    body=body,
                    heading_text=doc.title or "",
                    doc_id=doc.id,
                    start_seq=len(chunks),
                    prefix_heading=self.cfg.prefix_heading,
                    max_chars=self.cfg.max_chars,
                ))
        for i, m in enumerate(headings):
            heading_text = m.group(2).strip()
            start = m.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[start:end].strip()
            if len(body) < self.cfg.min_section_chars:
                continue
            chunks.extend(_emit_section_chunks(
                body=body,
                heading_text=heading_text,
                doc_id=doc.id,
                start_seq=len(chunks),
                prefix_heading=self.cfg.prefix_heading,
                max_chars=self.cfg.max_chars,
            ))
        return apply_if_oversize(
            chunks,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="hierarchy",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )
