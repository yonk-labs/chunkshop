from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

from chunkshop.sources.base import Document


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    seq_num: int
    original_content: str   # used for fact-matching / audit
    embedded_content: str   # what gets embedded (may differ from original)
    metadata: dict


class Chunker(Protocol):
    def chunk(self, doc: Document) -> list[Chunk]: ...
