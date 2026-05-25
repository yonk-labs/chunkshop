from __future__ import annotations
import datetime as _dt
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg import sql

from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.base import Document, SyncMode


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
    # Effective only when cfg.updated_at_column is set; otherwise the cursor
    # methods fall back to a full resync (FULL_RESYNC semantics).
    sync_mode = SyncMode.CURSOR

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

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        if not self.cfg.updated_at_column:
            # no cursor column → behave as full resync
            yield from self.iter_documents()
            return
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        ua_idx = len(cols)
        cols.append(self.cfg.updated_at_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)
        ident = [sql.Identifier(c) for c in cols]
        q = sql.SQL("SELECT {c} FROM {s}.{t}").format(
            c=sql.SQL(", ").join(ident),
            s=sql.Identifier(self.cfg.database_name),
            t=sql.Identifier(self.cfg.table),
        )
        params: list = []
        # Tuple cursor: (after_ts, after_id) > (cursor.after_ts, cursor.after_id).
        # Casting id_col::text on both sides makes this uniform across int/uuid/text id
        # types — sync only needs the ordering to be CONSISTENT across runs, and
        # lexicographic-on-text is consistent.
        after_ts = cursor.get("after_ts")
        after_id = cursor.get("after_id")
        if after_ts is not None and after_id is not None:
            q = q + sql.SQL(" WHERE (") + sql.Identifier(self.cfg.updated_at_column) + sql.SQL(
                ", ") + sql.Identifier(self.cfg.id_column) + sql.SQL("::text) > (%s, %s)")
            params.append(_dt.datetime.fromisoformat(after_ts))
            params.append(str(after_id))
        q = q + sql.SQL(" ORDER BY ") + sql.Identifier(self.cfg.updated_at_column) + sql.SQL(
            ", ") + sql.Identifier(self.cfg.id_column) + sql.SQL("::text")
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(q, params)
            for row in cur:
                meta = {
                    self.cfg.metadata_columns[i]: _json_safe(row[meta_start + i])
                    for i in range(len(self.cfg.metadata_columns))
                }
                meta["_updated_at"] = _json_safe(row[ua_idx])
                yield Document(
                    id=str(row[0]),
                    content=row[1],
                    title=row[title_idx] if title_idx is not None else None,
                    metadata=meta,
                )

    def cursor_from(self, last_document: Document) -> dict:
        ua = (last_document.metadata or {}).get("_updated_at")
        return {"after_ts": ua, "after_id": last_document.id}
