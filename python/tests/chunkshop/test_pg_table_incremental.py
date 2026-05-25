# tests/chunkshop/test_pg_table_incremental.py
import os, pytest
psycopg = pytest.importorskip("psycopg")
from chunkshop.config import PgTableSource
from chunkshop.sources.base import IncrementalSource

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")


def _reachable():
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="CHUNKSHOP_TEST_DSN unreachable")


@pytest.fixture
def table():
    schema = "public"
    name = "chunkshop_test_inc"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
        cur.execute(f"CREATE TABLE {schema}.{name} (id text primary key, body text, updated_at timestamptz)")
        cur.execute(f"INSERT INTO {schema}.{name} VALUES ('a','aa', now() - interval '2 hours'),"
                    f"('b','bb', now() - interval '1 hour')")
        conn.commit()
    yield schema, name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
        conn.commit()


def _cfg(schema, name):
    return PgTableSource(type="pg_table", dsn=DSN, database=schema, table=name,
                         id_column="id", content_column="body", updated_at_column="updated_at")


def test_pg_table_is_incremental(table):
    src = __import__("chunkshop.sources.pg_table", fromlist=["PgTableSource"]).PgTableSource(_cfg(*table))
    assert isinstance(src, IncrementalSource)


def test_pg_table_cursor_only_returns_newer_rows(table):
    from chunkshop.sources.pg_table import PgTableSource as Src
    src = Src(_cfg(*table))
    first = list(src.iter_changes_since(src.empty_cursor()))
    assert {d.id for d in first} == {"a", "b"}
    cur = src.cursor_from(first[-1])
    # insert a newer row, re-sync → only the new one
    schema, name = table
    with psycopg.connect(DSN) as conn, (c := conn.cursor()):
        c.execute(f"INSERT INTO {schema}.{name} VALUES ('c','cc', now())")
        conn.commit()
    again = list(src.iter_changes_since(cur))
    assert {d.id for d in again} == {"c"}
