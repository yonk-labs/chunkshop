"""Shared helpers for the chunkshop bench scripts.

These match the production behavior of `Backend.vector_literal()` but live
under `docs/samples/benchmarks/` because the bench scripts query DBs
directly (around chunkshop's sink) for measurement isolation.
"""
from __future__ import annotations
from typing import Iterable


def vector_text(vec: Iterable[float], precision: int = 7) -> str:
    """Render a vector as a SQL-compatible bracketed float list.

    Mirrors `chunkshop.backends.{postgres,mariadb,sqlite}.vector_literal()`
    in spirit. The exact format (`'[' + comma-joined floats + ']'`) is what
    pgvector accepts, what `VEC_FromText()` parses, what sqlite-vec
    `MATCH` expects, and what ClickHouse's array literal looks like.
    """
    return "[" + ",".join(f"{v:.{precision}f}" for v in vec) + "]"
