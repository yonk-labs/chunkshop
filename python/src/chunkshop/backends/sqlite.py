"""SQLite backend (with sqlite-vec extension for vector storage).

SQLite has no schema/database namespace concept — chunkshop's YAML `database`
field is required by config (loose parity) but ignored at runtime. The DSN env
var holds the file path or `:memory:`.
"""
from __future__ import annotations
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import numpy as np


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

    def vector_type_ddl(self, dim: int) -> str:
        return f"FLOAT[{dim}]"

    def json_type_ddl(self) -> str:
        return "TEXT"

    def tags_array_type_ddl(self) -> str:
        return "TEXT"

    def text_pk_type_ddl(self) -> str:
        return "TEXT"

    def timestamp_now_default_ddl(self) -> str:
        return "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"

    def vector_literal(self, arr: "np.ndarray") -> str:
        return json.dumps([float(x) for x in arr])

    def tags_literal(self, tags: list[str]) -> str:
        return json.dumps(list(tags))

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        return f"json_extract({col_expr},'$.{dotted_path}')"

    def create_database_sql(self, name: str) -> str:
        del name
        return "SELECT 1 -- chunkshop: SQLite has no database/schema concept"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        keys = ", ".join(self.quote_ident(c) for c in key_cols)
        if not update_cols:
            return f"ON CONFLICT ({keys}) DO NOTHING"
        sets = ", ".join(
            f"{self.quote_ident(c)} = excluded.{self.quote_ident(c)}" for c in update_cols
        )
        return f"ON CONFLICT ({keys}) DO UPDATE SET {sets}"
