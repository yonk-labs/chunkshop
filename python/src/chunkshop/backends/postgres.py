"""Postgres backend: psycopg-based connection + dialect helpers."""
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

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
