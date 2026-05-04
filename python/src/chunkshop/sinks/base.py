"""Sink Protocol — every Sink owns chunkshop's data-model semantics on its backend."""
from __future__ import annotations
from typing import Protocol

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
    def count_docs(self) -> int: ...

    def query_top_k(
        self, query_vec: np.ndarray, k: int
    ) -> list[tuple[str, int, float]]:
        """Return [(doc_id, seq_num, distance), ...] for the k nearest chunks
        to `query_vec`, ordered by ascending cosine distance."""
        ...
