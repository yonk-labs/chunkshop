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
    def count_docs(self) -> int: ...
