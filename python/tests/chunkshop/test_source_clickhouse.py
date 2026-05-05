"""ClickhouseTableSource integration tests.

Initial commit covers P1-T1 (happy path); P1-T2..T5 land in
subsequent commits (Tasks 4-7 of the plan).

Each test creates and drops its own database to avoid cross-test pollution.
All tests skipped if CHUNKSHOP_TEST_DSN_CH is unset.
"""
import datetime
import decimal
import ipaddress
import os
import uuid

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


def test_p1_t3_json_safe_recursive_coercion():
    """P1-T3: nested CH types coerce to JSON-serializable forms.

    Covers Array(DateTime), Map(String, UUID), Decimal, Tuple, IPv4,
    Nullable. Asserts json.dumps(metadata) succeeds.
    """
    import json

    db = "chunkshop_src_test_t3"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            # CH requires allow_suspicious_low_cardinality_types and similar
            # flags for some experimental types but Array/Map/Tuple/Nullable
            # are all stable in 24.10.
            client.command(
                f"CREATE TABLE `{db}`.`docs` ("
                f"id String, body String, "
                f"  ts_array Array(DateTime), "
                f"  uuid_map Map(String, UUID), "
                f"  amount Decimal(10, 2), "
                f"  tup Tuple(String, Date), "
                f"  ip Nullable(IPv4), "
                f"  ip_null Nullable(IPv4)"
                f") ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [[
                    "doc1", "body text",
                    [datetime.datetime(2025, 1, 1, 12, 0, 0),
                     datetime.datetime(2025, 6, 15, 9, 30, 0)],
                    {"a": uuid.UUID("12345678-1234-5678-1234-567812345678")},
                    decimal.Decimal("123.45"),
                    ("hello", datetime.date(2025, 3, 1)),
                    ipaddress.IPv4Address("192.168.1.1"),
                    None,
                ]],
                column_names=["id", "body", "ts_array", "uuid_map", "amount",
                              "tup", "ip", "ip_null"],
            )

        cfg = Cfg(
            type="clickhouse_table",
            dsn_env=DSN_VAR,
            database=db,
            table="docs",
            id_column="id",
            content_column="body",
            metadata_columns=["ts_array", "uuid_map", "amount", "tup", "ip", "ip_null"],
        )
        docs = list(Source(cfg).iter_documents())
        assert len(docs) == 1
        meta = docs[0].metadata

        # Must be JSON-serializable end-to-end (this is the round-trip
        # the sink will perform via json.dumps).
        serialized = json.dumps(meta)
        assert serialized   # truthy = succeeded

        # Spot checks on the coerced shapes
        assert isinstance(meta["ts_array"], list)
        assert all(isinstance(x, str) for x in meta["ts_array"])
        assert "2025-01-01" in meta["ts_array"][0]

        assert isinstance(meta["uuid_map"], dict)
        assert meta["uuid_map"]["a"] == "12345678-1234-5678-1234-567812345678"

        assert meta["amount"] == 123.45
        assert isinstance(meta["amount"], float)

        # Tuple → list
        assert isinstance(meta["tup"], list)
        assert meta["tup"][0] == "hello"
        assert meta["tup"][1] == "2025-03-01"

        assert meta["ip"] == "192.168.1.1"
        assert meta["ip_null"] is None
    finally:
        with be.connect() as client:
            _drop_db(client, db)


def test_p1_t5_title_column_optional():
    """P1-T5: title_column is None → Document.title is None;
    title_column='headline' → Document.title == row.headline."""
    db = "chunkshop_src_test_t5"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, headline String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [["a", "body-a", "Hello A"], ["b", "body-b", "Hello B"]],
                column_names=["id", "body", "headline"],
            )

        # Without title_column
        cfg_no_title = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
        )
        docs = list(Source(cfg_no_title).iter_documents())
        assert len(docs) == 2
        assert all(d.title is None for d in docs)

        # With title_column
        cfg_with_title = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
            title_column="headline",
        )
        docs = list(Source(cfg_with_title).iter_documents())
        by_id = {d.id: d for d in docs}
        assert by_id["a"].title == "Hello A"
        assert by_id["b"].title == "Hello B"
    finally:
        with be.connect() as client:
            _drop_db(client, db)


def test_p1_t4_where_clause_trusted_input():
    """P1-T4: cfg.where is interpolated raw into SQL with CH dialect.
    Operator-trusted contract — same as PG/MariaDB siblings."""
    db = "chunkshop_src_test_t4"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, created_at DateTime) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [
                    ["old", "old body", datetime.datetime(2024, 1, 1, 0, 0, 0)],
                    ["new1", "new body 1", datetime.datetime(2025, 7, 1, 0, 0, 0)],
                    ["new2", "new body 2", datetime.datetime(2025, 8, 1, 0, 0, 0)],
                ],
                column_names=["id", "body", "created_at"],
            )

        cfg = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
            where="created_at > toDateTime('2025-06-01 00:00:00')",
        )
        docs = list(Source(cfg).iter_documents())
        ids = sorted(d.id for d in docs)
        assert ids == ["new1", "new2"]
    finally:
        with be.connect() as client:
            _drop_db(client, db)


def test_p1_t2_streaming_does_not_materialize():
    """P1-T2: streaming iteration is wired (uses query_rows_stream).

    Soft test — locks in the call shape. Seeds 2k rows, iterates fully,
    asserts:
      1. iteration completes (rows are reachable)
      2. the count matches what was inserted
      3. cleanup-on-early-exit doesn't blow up

    A regression that switches query_rows_stream → query would still
    pass (1) and (2) — this test is primarily a code-review marker
    that the streaming code path exists.
    """
    db = "chunkshop_src_test_t2"
    n_rows = 2_000
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            rows = [[f"doc{i:05d}", f"body of document {i}"] for i in range(n_rows)]
            client.insert(f"`{db}`.`docs`", rows, column_names=["id", "body"])

        cfg = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
        )
        src = Source(cfg)

        # Full iteration: count must match.
        all_docs = list(src.iter_documents())
        assert len(all_docs) == n_rows

        # Early-exit cleanup: take a few then bail. The StreamContext's
        # __exit__ should release the chunked HTTP response cleanly.
        partial = []
        for d in src.iter_documents():
            partial.append(d)
            if len(partial) >= 5:
                break
        assert len(partial) == 5
    finally:
        with be.connect() as client:
            _drop_db(client, db)
