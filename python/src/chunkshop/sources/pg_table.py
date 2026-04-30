from __future__ import annotations
import datetime as _dt
import os
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg import sql

from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    """Coerce a psycopg-returned value to a JSON-serializable form.

    json.dumps can't handle Decimal / datetime / UUID / date out of the
    box. The sink writes metadata as jsonb via json.dumps, so anything
    we put in Document.metadata has to round-trip cleanly. Conventions:
      - Decimal → float (lossy on huge values, fine for sales totals)
      - datetime / date → ISO 8601 string
      - bytes → base64 string (rare in metadata; keeps the row valid)
      - everything else → as-is (None, str, int, float, bool, list, dict)
    """
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

    def iter_documents(self) -> Iterator[Document]:
        dsn = os.environ[self.cfg.dsn_env]
        # Build the column list in a deterministic order:
        #   [id, content, optional title, *metadata_columns...]
        # Index positions matter — `row[1]` is always content, `row[2]` is
        # title if title_column is set, then metadata_columns from there.
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
            schema=sql.Identifier(self.cfg.schema_name),
            table=sql.Identifier(self.cfg.table),
        )
        if self.cfg.where:
            query = query + sql.SQL(" WHERE ") + sql.SQL(self.cfg.where)  # trusted operator input
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(query)
            for row in cur:
                # Build metadata dict from the trailing columns. Coerce
                # to JSON-safe types (Decimal → float, datetime → ISO,
                # bytes → b64) so the sink's json.dumps round-trips cleanly.
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
