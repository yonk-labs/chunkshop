"""MariaDB backend (>=11.7 - VECTOR type required). PyMySQL-based connection."""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import numpy as np

try:
    import pymysql
except ImportError as e:
    raise ImportError(
        "chunkshop's MariaDB backend requires the 'mariadb' extra. "
        "Install with: pip install 'chunkshop[mariadb]' "
        "(or 'chunkshop[all-backends]' for all 4 backends)."
    ) from e


class MariaDBBackend:
    """Backend Protocol implementation for MariaDB 11.7+ (native VECTOR type)."""

    name: Literal["mariadb"] = "mariadb"
    supports_upsert: bool = True

    def __init__(self, dsn: str | None = None, *, dsn_env: str | None = None):
        # `dsn` = resolved connection string (preferred). `dsn_env` = legacy
        # env-var-name path, kept for backward compat (lazy read at connect()).
        self._dsn = dsn
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        dsn = self._dsn if self._dsn is not None else os.environ[self._dsn_env]
        kwargs = _parse_mysql_dsn(dsn)
        conn = pymysql.connect(**kwargs)
        try:
            yield conn
        finally:
            conn.close()

    def quote_ident(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def fq_table(self, db: str, table: str) -> str:
        return f"{self.quote_ident(db)}.{self.quote_ident(table)}"

    def vector_type_ddl(self, dim: int) -> str:
        return f"VECTOR({dim})"

    def json_type_ddl(self) -> str:
        return "JSON"

    def tags_array_type_ddl(self) -> str:
        return "JSON"

    def text_pk_type_ddl(self) -> str:
        return "VARCHAR(255)"

    def timestamp_now_default_ddl(self) -> str:
        return "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"

    def vector_literal(self, arr: "np.ndarray") -> str:
        text = "[" + ",".join(f"{x:.6f}" for x in arr) + "]"
        return f"VEC_FromText('{text}')"

    def tags_literal(self, tags: list[str]) -> str:
        return json.dumps(list(tags))

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        return f"JSON_UNQUOTE(JSON_EXTRACT({col_expr},'$.{dotted_path}'))"

    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        del key_cols
        if not update_cols:
            return ""  # Sink should switch to INSERT IGNORE for empty update_cols
        sets = ", ".join(
            f"{self.quote_ident(c)} = VALUES({self.quote_ident(c)})" for c in update_cols
        )
        return f"ON DUPLICATE KEY UPDATE {sets}"

    def create_database_sql(self, name: str) -> str:
        return f"CREATE DATABASE IF NOT EXISTS {self.quote_ident(name)}"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    def emit_chunks_table_ddl(
        self,
        fq: str,
        cols: list,
        hnsw: bool,
        dim: int,
        engine: str | None = None,
        vector_metric: str = "cosine",
    ) -> list[str]:
        del dim
        del vector_metric
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
        if hnsw:
            lines += ",\n  VECTOR INDEX `vec_idx` (`embedding`)"
        engine_clause = f" ENGINE={engine or 'InnoDB'}"
        lines += ",\n  KEY `doc_seq_idx` (`doc_id`, `seq_num`)"
        return [f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n){engine_clause}"]

    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (db, table),
        )
        return cur.fetchone()[0] > 0

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        cur.execute(
            "SELECT column_type FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name='embedding'",
            (db, table),
        )
        r = cur.fetchone()
        if not r:
            return None
        import re as _re
        m = _re.match(r"^vector\((\d+)\)$", r[0].lower())
        return int(m.group(1)) if m else None

    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        lock_name = f"chunkshop_{key}"[:64]
        cur.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
        got = cur.fetchone()[0]
        if got != 1:
            raise RuntimeError(f"could not acquire MariaDB lock {lock_name!r} within 30s")
        try:
            yield
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def _parse_mysql_dsn(dsn: str) -> dict:
    """Parse mysql://user:pass@host:port/dbname into PyMySQL kwargs."""
    from urllib.parse import urlparse, unquote
    parsed = urlparse(dsn)
    if parsed.scheme not in ("mysql", "mariadb"):
        raise ValueError(f"expected mysql:// or mariadb:// DSN, got {parsed.scheme!r}")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": parsed.username and unquote(parsed.username),
        "password": parsed.password and unquote(parsed.password),
        "database": parsed.path.lstrip("/") or None,
        "charset": "utf8mb4",
        "autocommit": False,
    }
