"""Backend Protocol + shared dataclasses.

Backends own everything that MUST be different per backend, including DDL
sequencing. Sinks own chunkshop-specific data-model semantics (modes,
metadata promotion, delete_orphans, source-tag write-once).
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ContextManager, Iterator, Literal, Protocol

import numpy as np


@dataclass(frozen=True)
class ColSpec:
    """One column in a chunks table — backend-agnostic shape, backend-specific type DDL."""
    name: str
    type_ddl: str           # backend-specific type fragment, e.g. "text" / "VARCHAR(255)"
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False


class Backend(Protocol):
    """One backend = one DB engine. Stateless; methods are pure helpers + a connect ctx-mgr."""

    name: Literal["postgres", "mariadb", "sqlite", "clickhouse"]
    supports_upsert: bool       # CH = False; PG/MariaDB/SQLite = True

    # Connection lifecycle
    @contextmanager
    def connect(self) -> Iterator[Any]: ...   # yields driver-native connection

    # Identifier safety
    def quote_ident(self, name: str) -> str: ...
    def fq_table(self, db: str, table: str) -> str: ...

    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str: ...
    def json_type_ddl(self) -> str: ...
    def tags_array_type_ddl(self) -> str: ...
    def text_pk_type_ddl(self) -> str: ...
    def timestamp_now_default_ddl(self) -> str: ...

    # Value literals (returned as parameter-bindable Python values for the driver)
    def vector_literal(self, arr: np.ndarray) -> Any: ...
    def tags_literal(self, tags: list[str]) -> Any: ...
    def json_literal(self, obj: Any) -> Any: ...

    # JSON dotted-path extraction (used by promote_metadata + metadata_columns)
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str: ...

    # Upsert / conflict handling
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str: ...

    # DDL primitives
    def create_database_sql(self, name: str) -> str: ...
    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str: ...
    def drop_table_sql(self, fq: str) -> str: ...

    # Composite DDL — backend handles HNSW timing differences
    def emit_chunks_table_ddl(
        self,
        fq: str,
        cols: list[ColSpec],
        hnsw: bool,
        dim: int,
        engine: str | None = None,
    ) -> list[str]: ...

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool: ...
    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None: ...

    # Concurrent-create serialization (some backends are no-op)
    def with_create_lock(self, cur: Any, key: str) -> ContextManager[None]: ...
