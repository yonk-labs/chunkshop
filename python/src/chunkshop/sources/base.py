from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, Optional, Protocol


@dataclass(frozen=True)
class Document:
    id: str
    content: str
    title: Optional[str] = None
    metadata: Optional[dict] = None


class Source(Protocol):
    def iter_documents(self) -> Iterator[Document]: ...
