"""Opt-in read-connection pool for the PG search legs (perf, not behavior).

The pool only changes how a connection is acquired; ranking output is covered
by test_search_pg.py. These tests pin the pool's lifecycle contract: reuse when
enabled, fresh-per-call when disabled, and never-pool-a-poisoned-connection.
"""
from __future__ import annotations

import chunkshop.search as S


class _FakeConn:
    open_count = 0

    def __init__(self):
        type(self).open_count += 1
        self.closed = False
        self.autocommit = False

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _patch_connect(monkeypatch):
    _FakeConn.open_count = 0
    monkeypatch.setattr(S.psycopg, "connect", lambda dsn: _FakeConn())


def teardown_function():
    S.close_search_pool()


def test_pool_disabled_opens_fresh_connection_each_time(monkeypatch):
    monkeypatch.delenv("CHUNKSHOP_SEARCH_POOL", raising=False)
    _patch_connect(monkeypatch)
    for _ in range(3):
        with S._read_connection("dsn") as conn:
            assert isinstance(conn, _FakeConn)
    assert _FakeConn.open_count == 3  # no reuse when disabled


def test_pool_enabled_reuses_one_connection(monkeypatch):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", "1")
    _patch_connect(monkeypatch)
    seen = []
    for _ in range(5):
        with S._read_connection("dsn") as conn:
            seen.append(id(conn))
            assert conn.autocommit is True  # reads pool autocommit
    assert _FakeConn.open_count == 1       # opened once, reused 4x
    assert len(set(seen)) == 1


def test_pool_does_not_recycle_errored_connection(monkeypatch):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", "1")
    _patch_connect(monkeypatch)
    try:
        with S._read_connection("dsn") as conn:
            first = conn
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert first.closed is True             # poisoned conn closed, not pooled
    with S._read_connection("dsn") as conn2:
        assert conn2 is not first
    assert _FakeConn.open_count == 2


def test_close_search_pool_closes_idle(monkeypatch):
    monkeypatch.setenv("CHUNKSHOP_SEARCH_POOL", "1")
    _patch_connect(monkeypatch)
    with S._read_connection("dsn") as conn:
        held = conn
    assert held.closed is False
    S.close_search_pool()
    assert held.closed is True
