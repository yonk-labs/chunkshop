"""SQLite backend (with sqlite-vec extension for vector storage).

SQLite has no schema/database namespace concept — chunkshop's YAML `database`
field is required by config (loose parity) but ignored at runtime. The DSN env
var holds the file path or `:memory:`.
"""
from __future__ import annotations
import json
import os
import re
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
        try:
            import sqlite_vec
        except ImportError as e:
            conn.close()
            raise ImportError(
                "chunkshop's SQLite backend requires the 'sqlite' extra. "
                "Install with: pip install 'chunkshop[sqlite]' "
                "(or 'chunkshop[all-backends]' for all 4 backends)."
            ) from e
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
        # SQLite's ALTER TABLE has no IF NOT EXISTS clause; the sink relies on
        # catching sqlite3.OperationalError "duplicate column" for idempotency.
        return f"ALTER TABLE {fq} ADD COLUMN {self.quote_ident(col)} {type_ddl}"

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

    def emit_chunks_table_ddl(
        self, fq: str, cols: list, hnsw: bool, dim: int, engine: str | None = None,
    ) -> list[str]:
        del engine
        main_cols = [c for c in cols if c.name != "embedding"]
        embed_cols = [c for c in cols if c.name == "embedding"]
        if not embed_cols:
            raise ValueError("emit_chunks_table_ddl on SQLite expects an 'embedding' ColSpec")

        col_lines = []
        pk_cols = []
        for c in main_cols:
            line = f"  {self.quote_ident(c.name)} {c.type_ddl}"
            if c.default is not None:
                line += f" DEFAULT {c.default}"
            if not c.nullable:
                line += " NOT NULL"
            col_lines.append(line)
            if c.is_primary_key:
                pk_cols.append(c.name)
        lines = ",\n".join(col_lines)
        if pk_cols:
            pk = ", ".join(self.quote_ident(c) for c in pk_cols)
            lines += f",\n  PRIMARY KEY ({pk})"
        create_main = f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n)"

        bare = fq.strip('"')
        create_idx = (
            f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare + "_doc_seq_idx")} '
            f'ON {fq} ("doc_id", "seq_num")'
        )

        vec_fq = self.quote_ident(bare + "_vec")
        create_vec = (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec_fq} USING vec0("
            f'id TEXT PRIMARY KEY, embedding FLOAT[{dim}]'
            f")"
        )
        del hnsw
        return [create_main, create_idx, create_vec]

    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        del db
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
            (table,),
        )
        return cur.fetchone() is not None

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        del db
        cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table + "_vec",),
        )
        r = cur.fetchone()
        if not r or not r[0]:
            return None
        m = re.search(r"FLOAT\[(\d+)\]", r[0], re.IGNORECASE)
        return int(m.group(1)) if m else None

    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        del cur, key
        yield
