from __future__ import annotations
import datetime as _dt
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg import sql

from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    """Coerce psycopg-returned values to JSON-serializable forms."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        import base64
        return base64.b64encode(v).decode("ascii")
    return v


class PgTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = PostgresBackend(**cfg.backend_dsn_kwargs())

    def iter_documents(self) -> Iterator[Document]:
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)
        ident_cols = [sql.Identifier(c) for c in cols]
        query = sql.SQL("SELECT {cols} FROM {schema}.{table}").format(
            cols=sql.SQL(", ").join(ident_cols),
            schema=sql.Identifier(self.cfg.database_name),
            table=sql.Identifier(self.cfg.table),
        )
        if self.cfg.where:
            query = query + sql.SQL(" WHERE ") + sql.SQL(self.cfg.where)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(query)
            for row in cur:
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
