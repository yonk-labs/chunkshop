"""Postgres backend: psycopg-based connection + dialect helpers."""
from __future__ import annotations
import hashlib
import json
import os
import re
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

    # Composite DDL
    def emit_chunks_table_ddl(
        self,
        fq: str,
        cols: list,  # list[ColSpec]
        hnsw: bool,
        dim: int,
        engine: str | None = None,
    ) -> list[str]:
        # Engine clause is a no-op on PG (engine is the cluster's, not table-level).
        del engine
        del dim  # encoded in the embedding column's type_ddl

        col_lines = []
        pk_cols = []
        for c in cols:
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

        create = f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n)"

        # Strip schema prefix from fq for index naming: "db"."chunks" → chunks
        bare_table = fq.rsplit('.', 1)[-1].strip('"')
        statements = [create]
        statements.append(
            f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare_table + "_doc_seq_idx")} '
            f'ON {fq} ("doc_id", "seq_num")'
        )
        if hnsw:
            statements.append(
                f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare_table + "_emb_hnsw_idx")} '
                f'ON {fq} USING hnsw ("embedding" vector_cosine_ops)'
            )
        return statements

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
            (db, table),
        )
        return cur.fetchone()[0]

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        cur.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            ) AND attname = 'embedding'
            """,
            (table, db),
        )
        r = cur.fetchone()
        if r is None:
            return None
        m = re.match(r"^vector\((\d+)\)$", r[0])
        return int(m.group(1)) if m else None

    @staticmethod
    def _advisory_lock_key(name: str) -> int:
        digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big", signed=True)

    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (self._advisory_lock_key(key),))
        try:
            yield
        finally:
            pass
