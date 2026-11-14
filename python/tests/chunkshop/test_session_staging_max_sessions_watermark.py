"""Sessions beyond max_sessions must stay eligible: only yielded sessions get the watermark."""
import os
import psycopg
import pytest

from chunkshop.config import SessionStagingSource as Cfg
from chunkshop.memory import ensure_staging_table, stage_event
from chunkshop.sources.session_staging import SessionStagingSource

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
SCHEMA = "chunkshop_review_p1a_stg"


@pytest.fixture
def staged():
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG not reachable: {exc}")
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
        c.commit()
    ensure_staging_table(DSN, table="evt", schema=SCHEMA)
    for sid in ("s1", "s2", "s3"):
        stage_event(DSN, session_id=sid, role="user", content=f"hi {sid}", ts=None,
                    table="evt", schema=SCHEMA)
    yield
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.commit()


def _consumed():
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute(f"SELECT session_id FROM {SCHEMA}.evt "
                    "WHERE coalesce(consumed->>'realtime','') <> '' ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def _run():
    cfg = Cfg(type="session_staging", dsn=DSN, staging_table="evt", staging_schema=SCHEMA,
              mode="realtime", max_sessions=1)
    return [d.id for d in SessionStagingSource(cfg).iter_documents()]


def test_max_sessions_does_not_consume_unyielded_sessions(staged):
    assert _run() == ["s1"]
    assert _consumed() == ["s1"]
    assert _run() == ["s2"]
    assert _run() == ["s3"]
    assert _run() == []
    assert _consumed() == ["s1", "s2", "s3"]
