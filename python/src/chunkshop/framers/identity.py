from __future__ import annotations
from dataclasses import replace

from chunkshop.sources.base import Document


class IdentityFramer:
    """Default framer: 1-to-1 pass-through. Tags the doc with framer='identity'."""

    def frame(self, raw: Document) -> list[Document]:
        meta = dict(raw.metadata or {})
        meta["framer"] = "identity"
        meta["frame_seq"] = 0
        return [replace(raw, metadata=meta)]
