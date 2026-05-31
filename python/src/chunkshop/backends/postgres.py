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

    def __init__(self, dsn: str | None = None, *, dsn_env: str | None = None):
        # `dsn` is the resolved connection string (preferred, set by config
        # layer). `dsn_env` is the legacy env-var-name path. Pre-0.4.3 callers
        # rely on `._dsn` being an eager os.environ snapshot of `dsn_env`, so
        # that contract is preserved exactly; connect() still re-reads the env
        # var lazily on the legacy path (authoritative, as before).
        self._dsn_env = dsn_env
        if dsn is not None:
            self._dsn = dsn
        else:
            self._dsn = os.environ.get(dsn_env, "") if dsn_env is not None else None

    @contextmanager
    def connect(self) -> Iterator[Any]:
        dsn = os.environ[self._dsn_env] if self._dsn_env is not None else self._dsn
        with psycopg.connect(dsn) as conn:
            yield conn

    def new_connection(self) -> Any:
        """Open a raw, caller-owned psycopg connection (NOT a context manager).

        The caller is responsible for commit/rollback and close(). Used by the
        sink's hot write path to reuse one connection across many per-document
        writes instead of paying a ~5ms connect/teardown per document, while
        still committing per document (crash-safety + live-progress preserved).
        """
        dsn = os.environ[self._dsn_env] if self._dsn_env is not None else self._dsn
        return psycopg.connect(dsn)

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

    @staticmethod
    def vector_metric_sql(metric: str) -> tuple[str, str]:
        """Return pgvector (operator, HNSW opclass) for a supported metric.

        pgvector exposes nearest-neighbor search through different operators:

        - cosine distance: ``<=>`` with ``vector_cosine_ops``
        - inner product: ``<#>`` with ``vector_ip_ops``; the operator returns
          negative inner product so ascending ORDER BY means larger IP wins.
        - L2/Euclidean distance: ``<->`` with ``vector_l2_ops``
        """
        if metric == "cosine":
            return "<=>", "vector_cosine_ops"
        if metric == "inner_product":
            return "<#>", "vector_ip_ops"
        if metric == "l2":
            return "<->", "vector_l2_ops"
        raise ValueError(
            "vector_metric must be one of 'cosine', 'inner_product', or 'l2', "
            f"got {metric!r}"
        )

    @staticmethod
    def vector_score(distance: float, metric: str) -> float:
        """Convert a pgvector distance/operator value to higher-is-better score."""
        if metric == "cosine":
            return 1.0 - distance
        if metric == "inner_product":
            # pgvector's <#> returns negative inner product for ASC ordering.
            return -distance
        if metric == "l2":
            return -distance
        raise ValueError(
            "vector_metric must be one of 'cosine', 'inner_product', or 'l2', "
            f"got {metric!r}"
        )

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
        vector_metric: str = "cosine",
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
            _op, opclass = self.vector_metric_sql(vector_metric)
            idx_suffix = (
                "_emb_hnsw_idx"
                if vector_metric == "cosine"
                else f"_emb_hnsw_{vector_metric}_idx"
            )
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {self.quote_ident(bare_table + idx_suffix)} "
                f'ON {fq} USING hnsw ("embedding" {opclass})'
            )
        return statements

    def emit_plain_table_ddl(self, fq: str, cols: list) -> list[str]:
        """Emit CREATE TABLE for non-vector companion tables."""
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
        return [f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n)"]

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
