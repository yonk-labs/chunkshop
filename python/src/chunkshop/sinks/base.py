"""Sink Protocol — every Sink owns chunkshop's data-model semantics on its backend."""
from __future__ import annotations
import re
from typing import Iterable, Protocol

import numpy as np

from chunkshop.chunkers.base import Chunk


class Sink(Protocol):
    def create_table(self) -> None: ...
    def write_document(
        self,
        doc_id: str,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None: ...
    def delete_document(self, doc_id: str) -> int: ...
    def framed_doc_ids(self, raw_id: str) -> list[str]:
        """Distinct doc ids in the table that are frames of `raw_id`
        (exactly ``raw_id + "#" + digits``), scoped like delete_document."""
        ...
    def count_docs(self) -> int: ...

    def query_top_k(
        self, query_vec: np.ndarray, k: int
    ) -> list[tuple[str, int, float]]:
        """Return [(doc_id, seq_num, distance), ...] for the k nearest chunks
        to `query_vec`, ordered by ascending cosine distance."""
        ...


def frame_prefix(raw_id: str) -> str:
    """Prefix every frame id of `raw_id` starts with (for SQL substring equality)."""
    return raw_id + "#"


def filter_framed_ids(raw_id: str, candidates: Iterable[str]) -> list[str]:
    """Keep only exact frame ids (``raw_id#<digits>``) from SQL prefix candidates.

    The SQL side is a prefix match that may be case-insensitive or pad-space
    collated; this exact check is what keeps ``notes#draft`` / ``Notes#1``
    from being treated as frames of ``notes``.
    """
    pat = re.compile(re.escape(raw_id) + r"#\d+")
    return sorted({c for c in candidates if pat.fullmatch(c)})
