from __future__ import annotations
import hashlib
import re
from typing import Optional

import psycopg
from psycopg import sql
from psycopg.types.json import Json

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(schema: str, table: str) -> sql.Composed:
    for v in (schema, table):
        if not _IDENT.match(v):
            raise ValueError(f"identifier must match ^[a-z_][a-z0-9_]*$, got {v!r}")
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _event_id(session_id: str, seq, ts, content: str) -> str:
    disambig = seq if seq is not None else (ts if ts is not None else "")
    key = f"{session_id}\x00{disambig}\x00{content}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def ensure_staging_table(dsn: str, *, table: str, schema: str = "public") -> None:
    fq = _ident(schema, table)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        cur.execute(sql.SQL(
            "CREATE TABLE IF NOT EXISTS {fq} ("
            " event_id text PRIMARY KEY,"
            " session_id text NOT NULL,"
            " seq bigint,"
            " role text,"
            " content text NOT NULL,"
            " tool text,"
            " outcome text,"
            " event_ts timestamptz,"
            " staged_at timestamptz NOT NULL DEFAULT now(),"
            " consumed jsonb NOT NULL DEFAULT '{{}}'::jsonb,"
            " metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb)"
        ).format(fq=fq))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {ix} ON {fq} (session_id, seq)"
        ).format(ix=sql.Identifier(f"{table}_session_seq"), fq=fq))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {ix} ON {fq} (staged_at)"
        ).format(ix=sql.Identifier(f"{table}_staged_at"), fq=fq))
        conn.commit()


def stage_event(dsn: str, *, session_id: str, role: str, content: str,
                 ts: Optional[object] = None, seq: Optional[int] = None, tool: Optional[str] = None,
                 outcome: Optional[str] = None, event_id: Optional[str] = None,
                 metadata: Optional[dict] = None,
                 table: str, schema: str = "public") -> str:
    eid = event_id or _event_id(session_id, seq, ts, content)
    fq = _ident(schema, table)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL(
            "INSERT INTO {fq} (event_id, session_id, seq, role, content, tool,"
            " outcome, event_ts, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            " ON CONFLICT (event_id) DO NOTHING"
        ).format(fq=fq), (eid, session_id, seq, role, content, tool, outcome, ts,
                          Json(metadata or {})))
        conn.commit()
    return eid


def stage_events(dsn: str, events: list[dict], *, table: str, schema: str = "public") -> int:
    fq = _ident(schema, table)
    rows = []
    for e in events:
        eid = e.get("event_id") or _event_id(
            e["session_id"], e.get("seq"), e.get("ts"), e["content"])
        rows.append((eid, e["session_id"], e.get("seq"), e.get("role"), e["content"],
                     e.get("tool"), e.get("outcome"), e.get("ts"),
                     Json(e.get("metadata") or {})))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany(sql.SQL(
            "INSERT INTO {fq} (event_id, session_id, seq, role, content, tool,"
            " outcome, event_ts, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            " ON CONFLICT (event_id) DO NOTHING"
        ).format(fq=fq), rows)
        conn.commit()
    # Returns events submitted, NOT necessarily inserted: ON CONFLICT DO NOTHING
    # silently skips duplicate event_ids. Consumers must not treat this as an
    # inserted-row count.
    return len(rows)


def prune_staging(dsn: str, *, older_than: str, only_consolidated: bool = True,
                  table: str, schema: str = "public") -> int:
    fq = _ident(schema, table)
    cond = sql.SQL("staged_at < %s")
    if only_consolidated:
        cond = sql.SQL("staged_at < %s AND consumed ? 'consolidated'")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {fq} WHERE {cond}").format(fq=fq, cond=cond),
                    (older_than,))
        n = cur.rowcount
        conn.commit()
    return n
