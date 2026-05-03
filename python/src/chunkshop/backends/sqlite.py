"""SQLite backend (with sqlite-vec extension for vector storage).

SQLite has no schema/database namespace concept — chunkshop's YAML `database`
field is required by config (loose parity) but ignored at runtime. The DSN env
var holds the file path or `:memory:`.
"""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Literal


class SQLiteBackend:
    """Backend Protocol implementation for SQLite + sqlite-vec."""

    name: Literal["sqlite"] = "sqlite"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        path = os.environ[self._dsn_env]
        conn = sqlite3.connect(path)
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        try:
            yield conn
        finally:
            conn.close()

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def fq_table(self, db: str, table: str) -> str:
        del db
        return self.quote_ident(table)
