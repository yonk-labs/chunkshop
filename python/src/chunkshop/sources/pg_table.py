from __future__ import annotations
import os
from typing import Iterator

import psycopg
from psycopg import sql

from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.base import Document


class PgTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self) -> Iterator[Document]:
        dsn = os.environ[self.cfg.dsn_env]
        cols = [self.cfg.id_column, self.cfg.content_column]
        if self.cfg.title_column:
            cols.append(self.cfg.title_column)
        ident_cols = [sql.Identifier(c) for c in cols]
        query = sql.SQL("SELECT {cols} FROM {schema}.{table}").format(
            cols=sql.SQL(", ").join(ident_cols),
            schema=sql.Identifier(self.cfg.schema_name),
            table=sql.Identifier(self.cfg.table),
        )
        if self.cfg.where:
            query = query + sql.SQL(" WHERE ") + sql.SQL(self.cfg.where)  # trusted operator input
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(query)
            for row in cur:
                yield Document(
                    id=str(row[0]),
                    content=row[1],
                    title=row[2] if self.cfg.title_column else None,
                )
