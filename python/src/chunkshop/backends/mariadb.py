"""MariaDB backend (>=11.7 - VECTOR type required). PyMySQL-based connection."""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import numpy as np
import pymysql


class MariaDBBackend:
    """Backend Protocol implementation for MariaDB 11.7+ (native VECTOR type)."""

    name: Literal["mariadb"] = "mariadb"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        dsn = os.environ[self._dsn_env]
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
