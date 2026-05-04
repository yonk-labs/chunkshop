"""MariaDB backend (>=11.7 - VECTOR type required). PyMySQL-based connection."""
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

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
