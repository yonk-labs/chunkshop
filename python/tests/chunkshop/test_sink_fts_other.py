"""SC-002: sqlite/mariadb/clickhouse sinks wire ensure_fts on create; append validates it exists.

SQLite tests skip if sqlite_vec is not installed (no server needed otherwise).
MariaDB tests skip unless $CHUNKSHOP_TEST_DSN_MARIADB is set.
ClickHouse tests skip unless $CHUNKSHOP_TEST_DSN_CH is set.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from chunkshop.config import TargetConfig


# ---------------------------------------------------------------------------
# helpers — SQLite
# ---------------------------------------------------------------------------

def _sqlite_vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


_sqlite_skip = pytest.mark.skipif(
    not _sqlite_vec_available(), reason="sqlite_vec not installed"
)


def _sqlite_cfg(dsn_env, mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="sqlite", dsn_env=dsn_env, database="ignored",
        table="chunks", mode=mode, hnsw=False, **kw,
    )


def _fts5_table_exists(db_path: str, fts_table: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (fts_table,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


@pytest.fixture
def sqlite_dsn(tmp_path, monkeypatch):
    db_path = tmp_path / "test_fts.db"
    monkeypatch.setenv("SQLITE_FTS_TEST", str(db_path))
    return "SQLITE_FTS_TEST"


# ---------------------------------------------------------------------------
# SQLite tests
# ---------------------------------------------------------------------------

@_sqlite_skip
def test_sqlite_create_if_missing_builds_fts5(sqlite_dsn, tmp_path):
    from chunkshop.backends.sqlite import SQLiteBackend
    from chunkshop.sinks.sqlite import SqliteSink

    cfg = _sqlite_cfg(sqlite_dsn, mode="create_if_missing",
                      fts={"enabled": True, "language": "english"})
    SqliteSink(cfg, SQLiteBackend(dsn_env=sqlite_dsn), embed_dim=4).create_table()
    db_path = str(tmp_path / "test_fts.db")
    assert _fts5_table_exists(db_path, "chunks_fts"), \
        "FTS5 table should exist after create_if_missing+fts"


@_sqlite_skip
def test_sqlite_overwrite_builds_fts5(sqlite_dsn, tmp_path):
    from chunkshop.backends.sqlite import SQLiteBackend
    from chunkshop.sinks.sqlite import SqliteSink

    cfg = _sqlite_cfg(sqlite_dsn, mode="overwrite",
                      fts={"enabled": True, "language": "english"})
    SqliteSink(cfg, SQLiteBackend(dsn_env=sqlite_dsn), embed_dim=4).create_table()
    db_path = str(tmp_path / "test_fts.db")
    assert _fts5_table_exists(db_path, "chunks_fts"), \
        "FTS5 table should exist after overwrite+fts"


@_sqlite_skip
def test_sqlite_no_fts_config_builds_no_fts5(sqlite_dsn, tmp_path):
    from chunkshop.backends.sqlite import SQLiteBackend
    from chunkshop.sinks.sqlite import SqliteSink

    cfg = _sqlite_cfg(sqlite_dsn, mode="overwrite")  # fts=None
    SqliteSink(cfg, SQLiteBackend(dsn_env=sqlite_dsn), embed_dim=4).create_table()
    db_path = str(tmp_path / "test_fts.db")
    assert not _fts5_table_exists(db_path, "chunks_fts"), \
        "FTS5 table should NOT exist when fts not configured"


@_sqlite_skip
def test_sqlite_append_without_fts_table_raises(sqlite_dsn):
    from chunkshop.backends.sqlite import SQLiteBackend
    from chunkshop.sinks.sqlite import SqliteSink

    # Step 1: create table without FTS
    base_cfg = _sqlite_cfg(sqlite_dsn, mode="create_if_missing")
    SqliteSink(base_cfg, SQLiteBackend(dsn_env=sqlite_dsn), embed_dim=4).create_table()
    # Step 2: append with fts.enabled — FTS5 table absent → RuntimeError
    app_cfg = _sqlite_cfg(sqlite_dsn, mode="append", source_tag="t1",
                          fts={"enabled": True, "language": "english"})
    with pytest.raises(RuntimeError, match="fts"):
        SqliteSink(app_cfg, SQLiteBackend(dsn_env=sqlite_dsn), embed_dim=4).create_table()


# ---------------------------------------------------------------------------
# helpers — MariaDB
# ---------------------------------------------------------------------------

def _pymysql_available() -> bool:
    try:
        import pymysql  # noqa: F401
        return True
    except ImportError:
        return False


_MARIADB_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
_MARIADB_DSN = os.environ.get(_MARIADB_VAR)
_mariadb_skip = pytest.mark.skipif(
    not _MARIADB_DSN or not _pymysql_available(),
    reason=f"{_MARIADB_VAR} not set or pymysql not installed",
)

_MARIADB_DB = "chunkshop_fts_sink_test_mdb"


def _mdb_cfg(mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="mariadb", dsn_env=_MARIADB_VAR, database=_MARIADB_DB,
        table="chunks", mode=mode, hnsw=False, **kw,
    )


def _drop_mariadb_db():
    from chunkshop.backends.mariadb import MariaDBBackend
    be = MariaDBBackend(dsn_env=_MARIADB_VAR)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{_MARIADB_DB}`")
        conn.commit()


def _fulltext_index_exists_mdb() -> bool:
    from chunkshop.backends.mariadb import MariaDBBackend
    be = MariaDBBackend(dsn_env=_MARIADB_VAR)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE table_schema=%s AND table_name=%s AND index_name=%s LIMIT 1",
            (_MARIADB_DB, "chunks", "chunks_ft"),
        )
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# MariaDB tests
# ---------------------------------------------------------------------------

@_mariadb_skip
def test_mariadb_create_if_missing_builds_fulltext():
    from chunkshop.backends.mariadb import MariaDBBackend
    from chunkshop.sinks.mariadb import MariaDbSink
    try:
        cfg = _mdb_cfg(mode="create_if_missing", fts={"enabled": True, "language": "english"})
        MariaDbSink(cfg, MariaDBBackend(dsn_env=_MARIADB_VAR), embed_dim=4).create_table()
        assert _fulltext_index_exists_mdb(), "FULLTEXT index should exist after create_if_missing+fts"
    finally:
        _drop_mariadb_db()


@_mariadb_skip
def test_mariadb_overwrite_builds_fulltext():
    from chunkshop.backends.mariadb import MariaDBBackend
    from chunkshop.sinks.mariadb import MariaDbSink
    try:
        cfg = _mdb_cfg(mode="overwrite", fts={"enabled": True, "language": "english"})
        MariaDbSink(cfg, MariaDBBackend(dsn_env=_MARIADB_VAR), embed_dim=4).create_table()
        assert _fulltext_index_exists_mdb(), "FULLTEXT index should exist after overwrite+fts"
    finally:
        _drop_mariadb_db()


@_mariadb_skip
def test_mariadb_no_fts_config_builds_no_fulltext():
    from chunkshop.backends.mariadb import MariaDBBackend
    from chunkshop.sinks.mariadb import MariaDbSink
    try:
        cfg = _mdb_cfg(mode="overwrite")  # fts=None
        MariaDbSink(cfg, MariaDBBackend(dsn_env=_MARIADB_VAR), embed_dim=4).create_table()
        assert not _fulltext_index_exists_mdb(), "FULLTEXT index should NOT exist when fts not set"
    finally:
        _drop_mariadb_db()


@_mariadb_skip
def test_mariadb_append_without_fulltext_raises():
    from chunkshop.backends.mariadb import MariaDBBackend
    from chunkshop.sinks.mariadb import MariaDbSink
    try:
        base_cfg = _mdb_cfg(mode="create_if_missing")
        MariaDbSink(base_cfg, MariaDBBackend(dsn_env=_MARIADB_VAR), embed_dim=4).create_table()
        app_cfg = _mdb_cfg(mode="append", source_tag="t1",
                           fts={"enabled": True, "language": "english"})
        with pytest.raises(RuntimeError, match="fts"):
            MariaDbSink(app_cfg, MariaDBBackend(dsn_env=_MARIADB_VAR), embed_dim=4).create_table()
    finally:
        _drop_mariadb_db()


# ---------------------------------------------------------------------------
# helpers — ClickHouse
# ---------------------------------------------------------------------------

def _clickhouse_connect_available() -> bool:
    try:
        import clickhouse_connect  # noqa: F401
        return True
    except ImportError:
        return False


_CH_VAR = "CHUNKSHOP_TEST_DSN_CH"
_CH_DSN = os.environ.get(_CH_VAR)
_ch_skip = pytest.mark.skipif(
    not _CH_DSN or not _clickhouse_connect_available(),
    reason=f"{_CH_VAR} not set or clickhouse_connect not installed",
)

_CH_DB = "chunkshop_fts_sink_test_ch"


def _ch_cfg(mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="clickhouse", dsn_env=_CH_VAR, database=_CH_DB,
        table="chunks", mode=mode, hnsw=False, **kw,
    )


def _drop_ch_db():
    from chunkshop.backends.clickhouse import ClickHouseBackend
    be = ClickHouseBackend(dsn_env=_CH_VAR)
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{_CH_DB}` SYNC")


def _tokenbf_index_exists_ch() -> bool:
    from chunkshop.backends.clickhouse import ClickHouseBackend
    be = ClickHouseBackend(dsn_env=_CH_VAR)
    with be.connect() as client:
        r = client.query(
            "SELECT 1 FROM system.data_skipping_indices "
            "WHERE database={db:String} AND table={tbl:String} AND name={idx:String}",
            parameters={"db": _CH_DB, "tbl": "chunks", "idx": "original_content_tok"},
        )
        return bool(r.result_rows)


# ---------------------------------------------------------------------------
# ClickHouse tests
# ---------------------------------------------------------------------------

@_ch_skip
def test_ch_create_if_missing_builds_tokenbf():
    from chunkshop.backends.clickhouse import ClickHouseBackend
    from chunkshop.sinks.clickhouse import ClickHouseSink
    try:
        cfg = _ch_cfg(mode="create_if_missing", fts={"enabled": True, "language": "english"})
        ClickHouseSink(cfg, ClickHouseBackend(dsn_env=_CH_VAR), embed_dim=4).create_table()
        assert _tokenbf_index_exists_ch(), "tokenbf_v1 index should exist after create_if_missing+fts"
    finally:
        _drop_ch_db()


@_ch_skip
def test_ch_overwrite_builds_tokenbf():
    from chunkshop.backends.clickhouse import ClickHouseBackend
    from chunkshop.sinks.clickhouse import ClickHouseSink
    try:
        cfg = _ch_cfg(mode="overwrite", fts={"enabled": True, "language": "english"})
        ClickHouseSink(cfg, ClickHouseBackend(dsn_env=_CH_VAR), embed_dim=4).create_table()
        assert _tokenbf_index_exists_ch(), "tokenbf_v1 index should exist after overwrite+fts"
    finally:
        _drop_ch_db()


@_ch_skip
def test_ch_no_fts_config_builds_no_tokenbf():
    from chunkshop.backends.clickhouse import ClickHouseBackend
    from chunkshop.sinks.clickhouse import ClickHouseSink
    try:
        cfg = _ch_cfg(mode="overwrite")  # fts=None
        ClickHouseSink(cfg, ClickHouseBackend(dsn_env=_CH_VAR), embed_dim=4).create_table()
        assert not _tokenbf_index_exists_ch(), "tokenbf_v1 index should NOT exist when fts not set"
    finally:
        _drop_ch_db()


@_ch_skip
def test_ch_append_without_tokenbf_raises():
    from chunkshop.backends.clickhouse import ClickHouseBackend
    from chunkshop.sinks.clickhouse import ClickHouseSink
    try:
        base_cfg = _ch_cfg(mode="create_if_missing")
        ClickHouseSink(base_cfg, ClickHouseBackend(dsn_env=_CH_VAR), embed_dim=4).create_table()
        app_cfg = _ch_cfg(mode="append", source_tag="t1",
                          fts={"enabled": True, "language": "english"})
        with pytest.raises(RuntimeError, match="fts"):
            ClickHouseSink(app_cfg, ClickHouseBackend(dsn_env=_CH_VAR), embed_dim=4).create_table()
    finally:
        _drop_ch_db()
