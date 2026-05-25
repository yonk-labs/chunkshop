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


@runtime_checkable
class IncrementalSource(Protocol):
    """Sources that support cursor-based incremental sync implement this.

    The cursor shape is source-specific (ETag map for S3, timestamp for DB
    tables, HEAD-SHA for git, opaque page token for APIs). Consumers treat it
    as an opaque dict and persist it between calls. chunkshop never stores it.
    """
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...


@runtime_checkable
class PrunableSource(Protocol):
    """Sources that can enumerate source-side deletions implement this.

    Typically called at a lower cadence than iter_changes_since because prune
    detection often requires walking the full source manifest. Returns
    source-IDs (the Document.id field), not Document objects.
    """
    def empty_prune_cursor(self) -> dict: ...
    def iter_deleted_since(self, cursor: dict) -> Iterable[str]: ...
