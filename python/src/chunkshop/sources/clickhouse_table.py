"""ClickHouse table source — see docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md.

Three differences from sibling sources (pg_table.py, mariadb_table.py,
sqlite_table.py):

1. No cursor — ClickHouseBackend.connect() yields the clickhouse-connect
   Client directly (see backends/clickhouse.py:6-12).
2. Streaming iteration — uses client.query_rows_stream(sql) which
   returns a StreamContext (context manager). Diverges from PG/MariaDB
   siblings which fully buffer in RAM. Justified by CH's typical scale.
3. Recursive _json_safe — handles CH's broader scalar set (UUID,
   IPv4Address, IPv6Address, Decimal) plus nested list/tuple/dict.
"""
from __future__ import annotations
import base64
import datetime
import decimal
import ipaddress
import uuid
from typing import Any, Iterator

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    """Coerce clickhouse-connect-returned values to JSON-serializable forms.

    Recurses into list/tuple/dict because CH's Tuple/Array/Map types
    nest more deeply than PG/MariaDB row scalars in practice. Tuples
    become lists (true JSON has no tuple).
    """
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(v)
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    if isinstance(v, dict):
        return {k: _json_safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


class ClickhouseTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in cols)
        fq = self.backend.fq_table(self.cfg.database_name, self.cfg.table)
        query = f"SELECT {cols_sql} FROM {fq}"
        if self.cfg.where:
            # `where` is documented as TRUSTED OPERATOR INPUT — same contract
            # as PgTableSource / MariaDbTableSource / SqliteTableSource.
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as client:
            with client.query_rows_stream(query) as stream:
                for row in stream:
                    metadata = {
                        self.cfg.metadata_columns[i]: _json_safe(row[meta_start + i])
                        for i in range(len(self.cfg.metadata_columns))
                    }
                    yield Document(
                        id=str(row[0]),
                        content=row[1],
                        title=row[title_idx] if title_idx is not None else None,
                        metadata=metadata if metadata else None,
                    )
