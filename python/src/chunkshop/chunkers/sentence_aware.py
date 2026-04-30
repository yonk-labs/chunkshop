from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.chunkers._splitting import split_to_max_chars
from chunkshop.config import SentenceAwareChunker as Cfg
from chunkshop.sources.base import Document


_MD_HEADING = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _split_plain(text: str, max_chars: int, min_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if buffer:
                result.append(buffer.strip())
                buffer = ""
            result.extend(split_to_max_chars(para, max_chars))
        elif len(buffer) + len(para) + 2 > max_chars and buffer:
            result.append(buffer.strip())
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        result.append(buffer.strip())
    return result


def _split_prose(text: str, max_chars: int, min_chars: int) -> list[str]:
    headings = list(_MD_HEADING.finditer(text))
    if not headings:
        return _split_plain(text, max_chars, min_chars)
    result: list[str] = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end].strip()
        if section:
            result.extend(split_to_max_chars(section, max_chars))
    if headings[0].start() > 0:
        prefix = text[: headings[0].start()].strip()
        if prefix:
            result = split_to_max_chars(prefix, max_chars) + result
    if len(text) <= max_chars:
        return [s for s in result if s]
    return [s for s in result if len(s) >= min_chars]


class SentenceAwareChunker:
    def __init__(self, cfg: Cfg, build_chunker=None):
        self.cfg = cfg
        self._build_chunker = build_chunker
        self._warner = DedupedWarner("sentence_aware", cfg.max_chars)

    def chunk(self, doc: Document) -> list[Chunk]:
        if self.cfg.doc_type == "code":
            splits = _split_plain(doc.content, self.cfg.max_chars, self.cfg.min_chars)
        else:
            splits = _split_prose(doc.content, self.cfg.max_chars, self.cfg.min_chars)
        chunks = [
            Chunk(
                doc_id=doc.id,
                seq_num=i,
                original_content=text,
                embedded_content=text,
                metadata={"strategy": "sentence_aware"},
            )
            for i, text in enumerate(splits)
        ]
        return apply_if_oversize(
            chunks,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="sentence_aware",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )
