from __future__ import annotations
import datetime as _dt
from decimal import Decimal
from typing import Any, Iterator

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.config import MariaDbTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        import base64
        return base64.b64encode(v).decode("ascii")
    return v


class MariaDbTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = MariaDBBackend(dsn_env=cfg.dsn_env)

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
            # `where` is documented as trusted operator input — same contract as PgTableSource.
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as conn:
            cur = conn.cursor()
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
