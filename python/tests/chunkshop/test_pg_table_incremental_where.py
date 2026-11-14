"""PgTableSource.iter_changes_since must apply cfg.where (as iter_documents does)."""
import os
import pytest

psycopg = pytest.importorskip("psycopg")
from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.pg_table import PgTableSource

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")


def _reachable():
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="CHUNKSHOP_TEST_DSN unreachable")

T = "chunkshop_review_p1a_incwhere"


@pytest.fixture
def table():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS public.{T}")
        cur.execute(f"CREATE TABLE public.{T} (id text primary key, body text, vis text, a int, b int,"
                    f" updated_at timestamptz)")
        cur.execute(
            f"INSERT INTO public.{T} VALUES "
            "('pub1','x','public',1,0, now() - interval '5 hours'),"
            "('prv1','x','private',0,0, now() - interval '4 hours'),"
            "('pub2','x','public',0,0, now() - interval '3 hours'),"
            "('prv2','x','private',0,0, now() - interval '2 hours'),"
            "('pub3','x','public',0,0, now() - interval '1 hour')")
        conn.commit()
    yield
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS public.{T}")
        conn.commit()


def _src(where):
    return PgTableSource(Cfg(type="pg_table", dsn=DSN, database="public", table=T, id_column="id",
                             content_column="body", updated_at_column="updated_at", where=where))


def test_incremental_respects_where_without_and_with_cursor(table):
    src = _src("vis='public'")
    first = list(src.iter_changes_since(src.empty_cursor()))
    assert [d.id for d in first] == ["pub1", "pub2", "pub3"]  # ordering unchanged
    cursor = src.cursor_from(first[0])
    later = list(src.iter_changes_since(cursor))
    assert [d.id for d in later] == ["pub2", "pub3"]  # private rows past the cursor excluded


def test_incremental_where_with_or_is_parenthesized(table):
    # (a=1 OR vis='public') AND cursor -- unparenthesized, OR would bypass the cursor predicate.
    src = _src("a=1 OR vis='private'")
    first = list(src.iter_changes_since(src.empty_cursor()))
    assert [d.id for d in first] == ["pub1", "prv1", "prv2"]
    later = list(src.iter_changes_since(src.cursor_from(first[1])))
    assert [d.id for d in later] == ["prv2"]


def test_incremental_without_where_unchanged(table):
    src = _src(None)
    assert len(list(src.iter_changes_since(src.empty_cursor()))) == 5
