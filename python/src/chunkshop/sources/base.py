# src/chunkshop/sources/base.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Optional, Protocol, runtime_checkable


class SyncMode(str, Enum):
    """How a Source detects changes between runs."""
    FULL_RESYNC = "full_resync"
    CURSOR = "cursor"
    FINGERPRINT = "fingerprint"


class StaleCursorError(Exception):
    """Raised by iter_changes_since when a cursor is too old to honor.

    Consumers should treat this as a signal to fall back to a full resync
    (call iter_documents / iter_changes_since(empty_cursor())).
    """


@dataclass(frozen=True)
class Document:
    id: str
    content: str
    title: Optional[str] = None
    metadata: Optional[dict] = None
    fingerprint: Optional[str] = None


class Source(Protocol):
    sync_mode: SyncMode = SyncMode.FULL_RESYNC

    def iter_documents(self) -> Iterator[Document]: ...
