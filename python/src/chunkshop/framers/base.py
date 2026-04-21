from __future__ import annotations
from typing import Protocol

from chunkshop.sources.base import Document


class DocFramer(Protocol):
    """Split one raw Document from a Source into one-or-more framed Documents.

    Implementations should add ``metadata["framer"]`` (framer name) and
    ``metadata["frame_seq"]`` (0-indexed position within raw doc) to each framed
    output. Raw doc metadata is preserved by value copy.

    Stateless: no I/O, no resource handles.
    """
    def frame(self, raw: Document) -> list[Document]: ...
