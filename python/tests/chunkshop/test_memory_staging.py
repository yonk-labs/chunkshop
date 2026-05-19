"""Integration tests for chunkshop.memory staging API."""
import os, psycopg, pytest
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

@pytest.fixture
def ensure_pg():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()


from chunkshop.memory import stage_event, stage_events, ensure_staging_table, prune_staging

T = dict(table="evt", schema="chunkshop_test_memory")


def _count(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunkshop_test_memory.evt")
        return cur.fetchone()[0]


def test_stage_event_and_idempotent(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    eid = stage_event(dsn, session_id="s1", role="user", content="hi", ts=None, **T)
    assert isinstance(eid, str) and eid
    stage_event(dsn, session_id="s1", role="user", content="hi", ts=None, event_id=eid, **T)
    assert _count(dsn) == 1


def test_stage_events_bulk(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    n = stage_events(dsn, [
        {"session_id": "s1", "role": "user", "content": "a"},
        {"session_id": "s1", "role": "assistant", "content": "b", "tool": "bash"},
    ], **T)
    assert n == 2 and _count(dsn) == 2


def test_prune_staging_only_consolidated(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    stage_event(dsn, session_id="s1", role="user", content="x", ts=None, **T)
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("UPDATE chunkshop_test_memory.evt SET consumed='{\"consolidated\": \"2000-01-01T00:00:00Z\"}', staged_at='2000-01-01'")
        c.commit()
    removed = prune_staging(dsn, older_than="2020-01-01", only_consolidated=True, **T)
    assert removed == 1 and _count(dsn) == 0
