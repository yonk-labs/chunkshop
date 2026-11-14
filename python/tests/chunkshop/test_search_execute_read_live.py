"""PYC-01 / PYC-10: _execute_read against a live Postgres.

  - ef_search must actually reach the search query on the pooled (autocommit)
    path, the unpooled path, and not leak to the next user of a pooled conn
  - a non-connection DB error must not leak the pooled connection

Needs $CHUNKSHOP_TEST_DSN; skips if unreachable.
"""
from __future__ import annotations

import os

import psycopg
import pytest

import chunkshop.search as S

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
)
EF_SQL = "SELECT current_setting('hnsw.ef_search', true)"


@pytest.fixture(autouse=True)
def _live_and_clean_pool(monkeypatch):
    try:
        psycopg.connect(DSN, connect_timeout=3).close()
    except Exception:
        pytest.skip("test DB unreachable")
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    S.close_search_pool()
    yield
    S.close_search_pool()


def test_ef_search_applies_on_pooled_path():
    assert S._execute_read(DSN, EF_SQL, [], ef_search=77) == [("77",)]


def test_ef_search_does_not_leak_to_next_pooled_use():
    S._execute_read(DSN, EF_SQL, [], ef_search=77)
    # the same pooled connection is reused here
    assert len(S._POOLS[DSN]) == 1
    rows = S._execute_read(DSN, EF_SQL, [], ef_search=None)
    assert rows[0][0] != "77"


def test_ef_search_applies_with_pool_disabled(monkeypatch):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", "0")
    assert S._execute_read(DSN, EF_SQL, [], ef_search=77) == [("77",)]


def _my_backend_count(app_name: str) -> int:
    with psycopg.connect(DSN, autocommit=True) as c:
        return c.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND application_name = %s",
            [app_name],
        ).fetchone()[0]


def test_sql_error_does_not_leak_pooled_connection(monkeypatch):
    # A leaked psycopg conn is closed by GC as soon as it is unreferenced, so
    # server-side counts alone can't see it. Record every conn we open and
    # require: each one is either closed or sitting in the pool, and the pool
    # reuses one conn across failures (no reconnect per error).
    opened = []
    real_open = S._open_read_conn

    def tracking_open(dsn):
        c = real_open(dsn)
        opened.append(c)
        return c

    monkeypatch.setattr(S, "_open_read_conn", tracking_open)
    app = "chunkshop_pyc10_test"
    dsn = DSN + ("&" if "?" in DSN else "?") + f"application_name={app}"
    before = _my_backend_count(app)
    n = 12
    for _ in range(n):
        with pytest.raises(psycopg.errors.UndefinedColumn):
            S._execute_read(dsn, "SELECT no_such_column FROM pg_class", [])
    # Server-side backends opened by this process under our app name: the
    # conn is returned to the pool and reused, so far fewer than n remain.
    after_errors = _my_backend_count(app)
    assert after_errors - before <= 1, (before, after_errors)
    pooled = [c for c, _ in S._POOLS.get(dsn, [])]
    assert len(opened) == 1, f"reconnected {len(opened)}x for {n} failures"
    assert pooled == opened and not opened[0].closed
    # also with ef_search (transaction block rolled back)
    for _ in range(3):
        with pytest.raises(psycopg.errors.UndefinedColumn):
            S._execute_read(dsn, "SELECT no_such_column FROM pg_class", [], ef_search=40)
    assert _my_backend_count(app) - before <= 1
    # healthy afterwards
    assert S._execute_read(dsn, "SELECT 1", []) == [(1,)]
    assert S._execute_read(dsn, EF_SQL, [], ef_search=55) == [("55",)]


def test_keyboard_interrupt_closes_connection_not_pool(monkeypatch):
    conn, _ = S._pool_acquire(DSN)
    S._pool_release(DSN, conn, ok=True)

    def boom(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(psycopg.Cursor, "execute", boom)
    with pytest.raises(KeyboardInterrupt):
        S._execute_read(DSN, "SELECT 1", [])
    assert conn.closed
    assert not S._POOLS.get(DSN)
