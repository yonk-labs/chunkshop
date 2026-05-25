# src/chunkshop/raw_store/base.py
"""RawStore: pluggable storage for raw source artifacts (the original bytes a
connector or upload fetched), parallel to chunkshop's vector Sink.

Opt-in: connectors still just yield Documents. When a `raw_store:` block is
configured, the connector/upload path stages bytes here so re-processing
doesn't require re-fetching, deltas can short-circuit via exists(), and the
original can be served/audited.
"""
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class RawStore(Protocol):
    def put(self, doc_id: str, data: bytes, *, content_type: str,
            meta: Optional[dict] = None) -> str:
        """Store raw bytes for doc_id. Returns an opaque ref usable by get()."""
        ...

    def get(self, ref: str) -> bytes: ...

    def exists(self, doc_id: str, fingerprint: Optional[str] = None) -> bool:
        """True if doc_id is stored; if fingerprint is given, True only when the
        stored artifact matches that fingerprint (enables delta short-circuit)."""
        ...

    def delete(self, doc_id: str) -> None: ...
