from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.config import SentenceAwareChunker as Cfg
from chunkshop.sources.base import Document


_MAX_CHARS = 3000  # ~750 tokens for BAAI/bge-small-en-v1.5
_MIN_CHARS = 200

_MD_HEADING = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _hard_split(text: str) -> list[str]:
    if len(text) <= _MAX_CHARS:
        return [text]
    out: list[str] = []
    for i in range(0, len(text), _MAX_CHARS):
        out.append(text[i : i + _MAX_CHARS])
    return out


def _split_plain(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(para) > _MAX_CHARS:
            if buffer:
                result.append(buffer.strip())
                buffer = ""
            result.extend(_hard_split(para))
        elif len(buffer) + len(para) + 2 > _MAX_CHARS and buffer:
            result.append(buffer.strip())
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        result.append(buffer.strip())
    return result


def _split_prose(text: str) -> list[str]:
    headings = list(_MD_HEADING.finditer(text))
    if not headings:
        return _split_plain(text)
    result: list[str] = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end].strip()
        if section:
            result.extend(_hard_split(section))
    if headings[0].start() > 0:
        prefix = text[: headings[0].start()].strip()
        if prefix:
            result = _hard_split(prefix) + result
    if len(text) <= _MAX_CHARS:
        return [s for s in result if s]
    return [s for s in result if len(s) >= _MIN_CHARS]


class SentenceAwareChunker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def chunk(self, doc: Document) -> list[Chunk]:
        if self.cfg.doc_type == "code":
            splits = _split_plain(doc.content)
        else:
            splits = _split_prose(doc.content)
        return [
            Chunk(
                doc_id=doc.id,
                seq_num=i,
                original_content=text,
                embedded_content=text,
                metadata={"strategy": "sentence_aware"},
            )
            for i, text in enumerate(splits)
        ]
