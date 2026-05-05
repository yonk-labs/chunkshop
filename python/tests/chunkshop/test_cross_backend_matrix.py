"""Parameterized cross-backend matrix: every DB-source × every DB-sink combo.

Each test seeds a small corpus into the SOURCE backend, runs `run_cell` to
ingest through the chunkshop pipeline, and verifies rows landed in the SINK
backend. Skipped unless ALL 4 backend DSN env vars are set.

ClickHouse is sink-only in v4.0 (no clickhouse_table source), so CH-as-source
combos are absent from the matrix. 3 sources × 4 sinks = 12 cells.
"""
import os
from pathlib import Path

import pytest

pytest.importorskip("pymysql")
pytest.importorskip("clickhouse_connect")
pytest.importorskip("sqlite_vec")

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import (
    PgTableSource, MariaDbTableSource, SqliteTableSource,
    TargetConfig, FastembedEmbedder, NoneExtractor,
    SentenceAwareChunker, IdentityFramerConfig, RuntimeConfig, CellConfig,
)
from chunkshop.runner import run_cell


PG_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
MARIADB_DSN = os.environ.get("CHUNKSHOP_TEST_DSN_MARIADB")
CH_DSN = os.environ.get("CHUNKSHOP_TEST_DSN_CH")
ALL_DSNS = bool(PG_DSN and MARIADB_DSN and CH_DSN)
pytestmark = pytest.mark.skipif(
    not ALL_DSNS,
    reason="all of CHUNKSHOP_TEST_DSN, CHUNKSHOP_TEST_DSN_MARIADB, CHUNKSHOP_TEST_DSN_CH required",
)


def _seed_pg(dsn_env: str, db: str) -> None:
    be = PostgresBackend(dsn_env=dsn_env)
    with be.connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{db}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{db}"')
        cur.execute(f'CREATE TABLE "{db}"."docs" (id text PRIMARY KEY, body text NOT NULL)')
        cur.execute(
            f'INSERT INTO "{db}"."docs" (id, body) VALUES (%s, %s)',
            ("doc1", "Hello world. This is sentence two. " * 10),
        )
        conn.commit()


def _seed_mariadb(dsn_env: str, db: str) -> None:
    be = MariaDBBackend(dsn_env=dsn_env)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
        cur.execute(f"CREATE DATABASE `{db}`")
        cur.execute(f"CREATE TABLE `{db}`.`docs` (id VARCHAR(64) PRIMARY KEY, body TEXT NOT NULL)")
        cur.execute(
            f"INSERT INTO `{db}`.`docs` VALUES (%s, %s)",
            ("doc1", "Hello world. This is sentence two. " * 10),
        )
        conn.commit()


def _seed_sqlite(dsn_env: str) -> None:
    with SQLiteBackend(dsn_env=dsn_env).connect() as conn:
        cur = conn.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS "docs" (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        cur.execute(
            'INSERT INTO "docs" VALUES (?, ?)',
            ("doc1", "Hello world. This is sentence two. " * 10),
        )
        conn.commit()


def _count_pg(dsn_env: str, db: str, table: str) -> int:
    with PostgresBackend(dsn_env=dsn_env).connect() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{db}"."{table}"')
        return cur.fetchone()[0]


def _count_mariadb(dsn_env: str, db: str, table: str) -> int:
    with MariaDBBackend(dsn_env=dsn_env).connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM `{db}`.`{table}`")
        return cur.fetchone()[0]


def _count_sqlite(dsn_env: str, table: str) -> int:
    with SQLiteBackend(dsn_env=dsn_env).connect() as conn:
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


def _count_ch(dsn_env: str, db: str, table: str) -> int:
    with ClickHouseBackend(dsn_env=dsn_env).connect() as client:
        r = client.query(f"SELECT count() FROM `{db}`.`{table}`")
        return int(r.result_rows[0][0])


def _drop_pg(dsn_env: str, db: str) -> None:
    with PostgresBackend(dsn_env=dsn_env).connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{db}" CASCADE')
        conn.commit()


def _drop_mariadb(dsn_env: str, db: str) -> None:
    with MariaDBBackend(dsn_env=dsn_env).connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
        conn.commit()


def _drop_ch(dsn_env: str, db: str) -> None:
    with ClickHouseBackend(dsn_env=dsn_env).connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{db}` SYNC")


SOURCE_KINDS = ["pg_table", "mariadb_table", "sqlite_table"]
SINK_KINDS = ["postgres", "mariadb", "sqlite", "clickhouse"]


def _build_source(src_kind, src_dsn_env, src_db_name):
    if src_kind == "pg_table":
        return PgTableSource(
            type="pg_table", dsn_env=src_dsn_env, database=src_db_name,
            table="docs", id_column="id", content_column="body",
        )
    if src_kind == "mariadb_table":
        return MariaDbTableSource(
            type="mariadb_table", dsn_env=src_dsn_env, database=src_db_name,
            table="docs", id_column="id", content_column="body",
        )
    if src_kind == "sqlite_table":
        return SqliteTableSource(
            type="sqlite_table", dsn_env=src_dsn_env, database="ignored",
            table="docs", id_column="id", content_column="body",
        )


def _build_target(sink_kind, sink_dsn_var, sink_db_name):
    common = {"table": "chunks", "mode": "overwrite", "source_tag": "xbm", "hnsw": False}
    if sink_kind == "sqlite":
        return TargetConfig(type="sqlite", dsn_env=sink_dsn_var, database="ignored", **common)
    return TargetConfig(type=sink_kind, dsn_env=sink_dsn_var, database=sink_db_name, **common)


@pytest.mark.parametrize("src_kind", SOURCE_KINDS)
@pytest.mark.parametrize("sink_kind", SINK_KINDS)
def test_cross_backend_matrix(src_kind, sink_kind, tmp_path, monkeypatch):
    """Every (DB-source × DB-sink) cell ingests one doc end-to-end."""
    src_db_name = f"xbm_src_{src_kind}_{sink_kind}"
    sink_db_name = f"xbm_sink_{src_kind}_{sink_kind}"

    monkeypatch.setenv("XBM_SRC_SQLITE", str(tmp_path / "src.db"))
    monkeypatch.setenv("XBM_SINK_SQLITE", str(tmp_path / "sink.db"))

    if src_kind == "pg_table":
        _seed_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN"
    elif src_kind == "mariadb_table":
        _seed_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN_MARIADB"
    else:
        _seed_sqlite("XBM_SRC_SQLITE")
        src_dsn = "XBM_SRC_SQLITE"

    if sink_kind == "postgres":
        sink_dsn = "CHUNKSHOP_TEST_DSN"
    elif sink_kind == "mariadb":
        sink_dsn = "CHUNKSHOP_TEST_DSN_MARIADB"
    elif sink_kind == "sqlite":
        sink_dsn = "XBM_SINK_SQLITE"
    else:
        sink_dsn = "CHUNKSHOP_TEST_DSN_CH"

    cfg = CellConfig(
        cell_name=f"xbm__{src_kind}__{sink_kind}",
        source=_build_source(src_kind, src_dsn, src_db_name),
        framer=IdentityFramerConfig(),
        chunker=SentenceAwareChunker(max_chars=200, min_chars=50),
        embedder=FastembedEmbedder(
            type="fastembed", model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384, threads=2, batch_size=8,
        ),
        extractor=NoneExtractor(),
        target=_build_target(sink_kind, sink_dsn, sink_db_name),
        runtime=RuntimeConfig(omp_num_threads=2),
    )
    try:
        result = run_cell(cfg)
        assert result.error is None, f"run_cell error {src_kind}->{sink_kind}: {result.error}"
        assert result.docs_processed == 1
        assert result.chunks_written > 0
        if sink_kind == "postgres":
            assert _count_pg(sink_dsn, sink_db_name, "chunks") == result.chunks_written
        elif sink_kind == "mariadb":
            assert _count_mariadb(sink_dsn, sink_db_name, "chunks") == result.chunks_written
        elif sink_kind == "sqlite":
            assert _count_sqlite(sink_dsn, "chunks") == result.chunks_written
        elif sink_kind == "clickhouse":
            assert _count_ch(sink_dsn, sink_db_name, "chunks") == result.chunks_written
    finally:
        if src_kind == "pg_table":
            _drop_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        elif src_kind == "mariadb_table":
            _drop_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
        if sink_kind == "postgres":
            _drop_pg("CHUNKSHOP_TEST_DSN", sink_db_name)
        elif sink_kind == "mariadb":
            _drop_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", sink_db_name)
        elif sink_kind == "clickhouse":
            _drop_ch("CHUNKSHOP_TEST_DSN_CH", sink_db_name)
