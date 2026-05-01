"""Postgres backend: psycopg-based connection + dialect helpers."""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import numpy as np
import psycopg


class PostgresBackend:
    """Backend Protocol implementation for Postgres + pgvector."""

    name: Literal["postgres"] = "postgres"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env
        self._dsn = os.environ.get(dsn_env, "")

    @contextmanager
    def connect(self) -> Iterator[Any]:
        dsn = os.environ[self._dsn_env]
        with psycopg.connect(dsn) as conn:
            yield conn

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def fq_table(self, db: str, table: str) -> str:
        return f'{self.quote_ident(db)}.{self.quote_ident(table)}'

    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str:
        return f"vector({dim})"

    def json_type_ddl(self) -> str:
        return "jsonb"

    def tags_array_type_ddl(self) -> str:
        return "text[]"

    def text_pk_type_ddl(self) -> str:
        return "text"

    def timestamp_now_default_ddl(self) -> str:
        return "timestamptz NOT NULL DEFAULT now()"

    # Value literals
    def vector_literal(self, arr: np.ndarray) -> str:
        return "[" + ",".join(f"{x:.6f}" for x in arr) + "]"

    def tags_literal(self, tags: list[str]) -> list[str]:
        return list(tags)

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    # JSON dotted-path extraction
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        segs = dotted_path.split(".")
        if len(segs) == 1:
            return f"{col_expr}->>'{segs[0]}'"
        head = "->".join([col_expr] + [f"'{s}'" for s in segs[:-1]])
        return f"{head}->>'{segs[-1]}'"

    # DDL primitives
    def create_database_sql(self, name: str) -> str:
        return f"CREATE SCHEMA IF NOT EXISTS {self.quote_ident(name)}"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    # Upsert / conflict handling
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        keys = ", ".join(self.quote_ident(c) for c in key_cols)
        if not update_cols:
            return f"ON CONFLICT ({keys}) DO NOTHING"
        sets = ", ".join(
            f"{self.quote_ident(c)} = EXCLUDED.{self.quote_ident(c)}" for c in update_cols
        )
        return f"ON CONFLICT ({keys}) DO UPDATE SET {sets}"
