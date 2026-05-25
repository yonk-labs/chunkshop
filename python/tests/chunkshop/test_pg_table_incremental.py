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


def test_pg_table_handles_row_inserted_at_cursor_boundary(table):
    """A row inserted at the same updated_at as the cursor boundary, AFTER
    the cursor has advanced past a sibling row at that timestamp, must still
    be picked up on the next sync. The strict `WHERE updated_at > %s` cursor
    silently drops it; the tuple cursor (after_ts, after_id) yields it.
    """
    from datetime import datetime, timezone

    from chunkshop.sources.pg_table import PgTableSource as Src
    from chunkshop.testing import merge_cursor
    schema, name = table
    boundary = datetime.now(timezone.utc)
    # Sync 1: row c1 exists at the boundary timestamp.
    with psycopg.connect(DSN) as conn, (c := conn.cursor()):
        c.execute(f"INSERT INTO {schema}.{name} (id, body, updated_at) VALUES ('c1','cc1', %s)", [boundary])
        conn.commit()
    src = Src(_cfg(*table))
    first = list(src.iter_changes_since(src.empty_cursor()))
    assert {d.id for d in first} == {"a", "b", "c1"}
    cursor = merge_cursor(src, src.empty_cursor(), first)
    # Concurrent writer commits c2 at the SAME boundary timestamp as c1 —
    # mimics the realistic race: their SELECT happened before our cursor advance.
    with psycopg.connect(DSN) as conn, (c := conn.cursor()):
        c.execute(f"INSERT INTO {schema}.{name} (id, body, updated_at) VALUES ('c2','cc2', %s)", [boundary])
        conn.commit()
    # Sync 2: c2 must be emitted. Strict-`>` silently drops it; tuple cursor catches it.
    again = list(src.iter_changes_since(cursor))
    assert {d.id for d in again} == {"c2"}, (
        f"expected c2 on next sync (boundary-row), got {[d.id for d in again]}")


def test_pg_table_satisfies_incremental_helpers(table):
    from chunkshop.sources.pg_table import PgTableSource as Src
    from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit
    src = Src(_cfg(*table))
    assert_cursor_advances(src)
    # Source is stateless w.r.t. cursors; a fresh instance sees the same DB rows.
    src2 = Src(_cfg(*table))
    assert_idempotent_on_re_emit(src2)
