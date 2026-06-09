"""Default-on read-connection pool for the PG search legs (perf, not behavior).

The pool only changes how a connection is acquired; ranking output is covered
by test_search_pg.py. These tests pin the pool's lifecycle contract:

  - ON by default; opt out via a falsy CHUNKSHOP_SEARCH_POOL
  - warm reuse when enabled, fresh-per-call when disabled
  - never recycle a poisoned connection
  - retry once on a *reused* dead connection, but not on a fresh failure
  - recycle a connection idle past the max age
  - drop inherited connections after a fork
"""
from __future__ import annotations

import time

import psycopg
import pytest

import chunkshop.search as S


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executes += 1
        if self._conn.fail_execute:
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    def fetchall(self):
        return list(self._conn.rows)


class _FakeConn:
    open_count = 0

    def __init__(self, rows=(), fail_execute=False):
        type(self).open_count += 1
        self.closed = False
        self.autocommit = False
        self.rows = rows
        self.fail_execute = fail_execute
        self.executes = 0

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _patch_connect(monkeypatch, factory=None):
    _FakeConn.open_count = 0
    factory = factory or (lambda dsn: _FakeConn())
    monkeypatch.setattr(S.psycopg, "connect", factory)


def teardown_function():
    S.close_search_pool()


# --- enable / disable semantics (default-on, chunkshop#64) ----------------


def test_pool_default_on_when_env_unset(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    assert S._pool_enabled() is True
    _patch_connect(monkeypatch)
    for _ in range(4):
        with S._read_connection("dsn") as conn:
            assert isinstance(conn, _FakeConn)
            assert conn.autocommit is True
    assert _FakeConn.open_count == 1  # warm reuse by default


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "  Off  ", ""])
def test_pool_opt_out_values_disable(monkeypatch, val):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", val)
    assert S._pool_enabled() is False
    _patch_connect(monkeypatch)
    for _ in range(3):
        with S._read_connection("dsn") as conn:
            assert isinstance(conn, _FakeConn)
    assert _FakeConn.open_count == 3  # fresh per call when opted out


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_pool_explicit_on_values_enable(monkeypatch, val):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", val)
    assert S._pool_enabled() is True


def test_pool_enabled_reuses_one_connection(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    _patch_connect(monkeypatch)
    seen = []
    for _ in range(5):
        with S._read_connection("dsn") as conn:
            seen.append(id(conn))
    assert _FakeConn.open_count == 1
    assert len(set(seen)) == 1


def test_pool_does_not_recycle_errored_connection(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    _patch_connect(monkeypatch)
    try:
        with S._read_connection("dsn") as conn:
            first = conn
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert first.closed is True  # poisoned conn closed, not pooled
    with S._read_connection("dsn") as conn2:
        assert conn2 is not first
    assert _FakeConn.open_count == 2


def test_close_search_pool_closes_idle(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    _patch_connect(monkeypatch)
    with S._read_connection("dsn") as conn:
        held = conn
    assert held.closed is False
    S.close_search_pool()
    assert held.closed is True
    S.close_search_pool()  # idempotent — second call is a no-op


# --- retry-once on a broken reused connection -----------------------------


def test_execute_read_retries_once_on_reused_dead_connection(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    # Seed the idle pool with a connection that will fail its first query —
    # this is the "server restarted while the connection sat idle" case.
    poison = _FakeConn(fail_execute=True)
    S._POOLS["dsn"] = [(poison, time.monotonic())]
    healthy = _FakeConn(rows=[("row",)])
    _patch_connect(monkeypatch, factory=lambda dsn: healthy)

    rows = S._execute_read("dsn", "SELECT 1", [])

    assert rows == [("row",)]          # retry succeeded
    assert poison.closed is True       # dead conn discarded, not re-pooled
    assert healthy.executes == 1       # query ran on the fresh conn
    # The fresh, healthy conn is returned to the pool for the next caller.
    assert any(c is healthy for c, _ts in S._POOLS.get("dsn", []))


def test_execute_read_does_not_retry_fresh_connection_failure(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    S.close_search_pool()  # ensure empty pool -> first acquire is FRESH
    poison = _FakeConn(fail_execute=True)
    _patch_connect(monkeypatch, factory=lambda dsn: poison)

    with pytest.raises(psycopg.OperationalError):
        S._execute_read("dsn", "SELECT 1", [])

    assert poison.closed is True
    assert poison.executes == 1  # query attempted once, NOT retried


def test_execute_read_disabled_uses_fresh_connection(monkeypatch):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", "0")
    healthy = _FakeConn(rows=[("x",)])
    _patch_connect(monkeypatch, factory=lambda dsn: healthy)
    rows = S._execute_read("dsn", "SELECT 1", [])
    assert rows == [("x",)]
    assert healthy.closed is True  # context-managed close on the non-pool path


# --- max-idle-age recycle --------------------------------------------------


def test_pool_recycles_idle_connection_past_max_age(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    stale = _FakeConn()
    # Released far enough in the past to exceed the max idle age.
    S._POOLS["dsn"] = [(stale, time.monotonic() - (S._POOL_MAX_AGE_S + 1))]
    fresh = _FakeConn()
    _patch_connect(monkeypatch, factory=lambda dsn: fresh)

    conn, reused = S._pool_acquire("dsn")

    assert conn is fresh
    assert reused is False
    assert stale.closed is True  # aged-out conn recycled, not handed out


# --- fork safety -----------------------------------------------------------


def test_fork_child_reset_drops_inherited_pool(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    _patch_connect(monkeypatch)
    with S._read_connection("dsn") as conn:
        held = conn
    assert S._POOLS.get("dsn")  # connection is pooled

    S._reset_pooled_connections_after_fork()

    assert S._POOLS == {}          # references dropped in the child
    assert held.closed is False    # NOT closed — would disturb the parent socket
