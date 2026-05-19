from __future__ import annotations
from typing import Iterator

from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import SessionStagingSource as Cfg
from chunkshop.sources.base import Document

_SELECT = (
    "SELECT event_id, session_id, seq, role, content, tool, outcome,"
    " extract(epoch FROM coalesce(event_ts, staged_at)) "
    "FROM {schema}.{table} {where} ORDER BY session_id, seq NULLS LAST"
)


class SessionStagingSource:
    """Yield one Document per session from the chunkshop staging table.

    realtime mode: sessions with events newer than consumed.realtime.
    consolidate mode: sessions quiet for >= min_age_seconds with new events
    since consumed.consolidated. Advances the per-session watermark on yield.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = PostgresBackend(**cfg.backend_dsn_kwargs())

    # -- pure: rows -> Documents (unit-tested) ------------------------------
    def _documents_from_rows(self, rows) -> Iterator[Document]:
        by_session: dict[str, list[dict]] = {}
        for (eid, sid, seq, role, content, tool, outcome, ts) in rows:
            by_session.setdefault(sid, []).append(
                {"event_id": eid, "seq": seq, "role": role, "content": content,
                 "tool": tool, "outcome": outcome, "ts": ts})
        for sid, evs in by_session.items():
            evs.sort(key=lambda e: (e["seq"] is None, e["seq"], e["ts"]))
            lines = []
            for e in evs:
                tag = e["role"] or "event"
                if e["tool"]:
                    tag += f"/{e['tool']}"
                lines.append(f"[{tag}] {e['content']}")
            yield Document(
                id=sid,
                content="\n".join(lines),
                title=None,
                metadata={
                    "session_id": sid,
                    "namespace": None,
                    "event_count": len(evs),
                    "first_ts": evs[0]["ts"],
                    "last_ts": evs[-1]["ts"],
                    "mode": self.cfg.mode,
                    "_session_events": evs,
                },
            )

    # -- production: query staging, yield, advance watermark ----------------
    def iter_documents(self) -> Iterator[Document]:
        if self.cfg.mode == "realtime":
            where = ("WHERE coalesce(consumed->>'realtime','') = '' "
                     "OR staged_at > (consumed->>'realtime')::timestamptz")
            wm = "realtime"
        else:
            where = ("WHERE coalesce(event_ts, staged_at) "
                     f"< now() - interval '{int(self.cfg.min_age_seconds)} seconds' "
                     "AND (coalesce(consumed->>'consolidated','') = '' "
                     "OR staged_at > (consumed->>'consolidated')::timestamptz)")
            wm = "consolidated"
        q = _SELECT.format(schema=self.cfg.staging_schema,
                           table=self.cfg.staging_table, where=where)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall()
            sessions = {r[1] for r in rows}
            for doc in self._documents_from_rows(rows):
                yield doc
            if sessions:
                cur.execute(
                    f"UPDATE {self.cfg.staging_schema}.{self.cfg.staging_table} "
                    f"SET consumed = consumed || jsonb_build_object(%s, now()::text) "
                    f"WHERE session_id = ANY(%s)",
                    (wm, list(sessions)))
                conn.commit()
