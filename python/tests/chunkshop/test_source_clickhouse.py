"""ClickhouseTableSource integration tests.

Initial commit covers P1-T1 (happy path); P1-T2..T5 land in
subsequent commits (Tasks 4-7 of the plan).

Each test creates and drops its own database to avoid cross-test pollution.
All tests skipped if CHUNKSHOP_TEST_DSN_CH is unset.
"""
import os
import pytest

pytest.importorskip("clickhouse_connect")

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.clickhouse_table import ClickhouseTableSource as Source

DSN_VAR = "CHUNKSHOP_TEST_DSN_CH"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


def _drop_db(client, db: str) -> None:
    client.command(f"DROP DATABASE IF EXISTS `{db}` SYNC")


def _create_db(client, db: str) -> None:
    _drop_db(client, db)
    client.command(f"CREATE DATABASE `{db}`")


def test_p1_t1_iter_documents_happy_path():
    """P1-T1: 2 documents with metadata_columns round-trip cleanly."""
    db = "chunkshop_src_test_t1"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, lang String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [["a", "first body", "en"], ["b", "second body", "fr"]],
                column_names=["id", "body", "lang"],
            )

        cfg = Cfg(
            type="clickhouse_table",
            dsn_env=DSN_VAR,
            database=db,
            table="docs",
            id_column="id",
            content_column="body",
            metadata_columns=["lang"],
        )
        src = Source(cfg)
        docs = list(src.iter_documents())
        assert len(docs) == 2
        by_id = {d.id: d for d in docs}
        assert by_id["a"].content == "first body"
        assert by_id["a"].metadata == {"lang": "en"}
        assert by_id["b"].metadata == {"lang": "fr"}
        assert by_id["a"].title is None
    finally:
        with be.connect() as client:
            _drop_db(client, db)
