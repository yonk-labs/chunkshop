"""Integration tests for ClickHouseSink. Skipped unless $CHUNKSHOP_TEST_DSN_CH is set
and points to a reachable ClickHouse 24.10+ instance with vector_similarity enabled."""
import logging
import os
import numpy as np
import pytest

pytest.importorskip("clickhouse_connect")

from chunkshop.config import TargetConfig, PromoteColumn
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.clickhouse import ClickHouseSink, _DELETE_ORPHANS_WARNED
from chunkshop.backends.clickhouse import ClickHouseBackend


DSN_VAR = "CHUNKSHOP_TEST_DSN_CH"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


@pytest.fixture
def db_name():
    return "chunkshop_test_v4_ch"


@pytest.fixture(autouse=True)
def cleanup(db_name):
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{db_name}` SYNC")
    yield
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{db_name}` SYNC")


def _cfg(db_name, mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="clickhouse", dsn_env=DSN_VAR, database=db_name,
        table="chunks", mode=mode, **kw,
    )


def _make_chunks(doc_id, n=3):
    return [
        Chunk(
            doc_id=doc_id, seq_num=i,
            original_content=f"chunk {i} body",
            embedded_content=f"chunk {i} body",
            metadata={"lang": "en", "section": f"sec_{i}"},
        ) for i in range(n)
    ]


def test_basic_ingest(db_name):
    cfg = _cfg(db_name, source_tag="t1")
    sink = ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("doc1", n=3)
    embs = np.random.rand(3, 4).astype(np.float32)
    sink.write_document("doc1", chunks, embs, [["a"], ["b"], ["c"]])
    assert sink.count_docs() == 1


def test_append_dim_mismatch(db_name):
    cfg1 = _cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = ClickHouseSink(cfg1, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _cfg(db_name, mode="append", source_tag="t2")
    sink2 = ClickHouseSink(cfg2, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=8)
    with pytest.raises(RuntimeError, match=r"target embedding dim is 4"):
        sink2.create_table()


def test_overwrite_foreign_tag_safety(db_name):
    cfg1 = _cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = ClickHouseSink(cfg1, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _cfg(db_name, mode="overwrite", source_tag="t2")
    sink2 = ClickHouseSink(cfg2, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    with pytest.raises(RuntimeError, match=r"overwrite refuses to drop"):
        sink2.create_table()


def test_delete_orphans_no_op_warns_once(db_name, caplog):
    _DELETE_ORPHANS_WARNED.clear()
    with caplog.at_level(logging.WARNING):
        cfg = _cfg(db_name, source_tag="t1", delete_orphans=True)
        ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
        ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    warnings = [r for r in caplog.records if "no-op" in r.message]
    assert len(warnings) == 1


def test_promote_metadata_jsonpath(db_name):
    cfg = _cfg(
        db_name, source_tag="t1",
        promote_metadata=[PromoteColumn(path="lang", type="text")],
    )
    sink = ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 1)
    sink.write_document("d1", chunks, np.random.rand(1, 4).astype(np.float32), [[]])
    with sink.backend.connect() as client:
        r = client.query(f"SELECT lang FROM {sink._fq()}")
        rows = r.result_rows
        assert rows and rows[0][0] == "en"


def test_query_top_k(db_name):
    cfg = _cfg(db_name, source_tag="t1")
    sink = ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 5)
    embs = np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float32)
    sink.write_document("d1", chunks, embs, [[]] * 5)

    q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    results = sink.query_top_k(q, k=3)
    assert len(results) == 3
    assert results[0][2] <= results[1][2] <= results[2][2]
    assert results[0][1] == 0  # exact match wins


def test_engine_clause_replacing_merge_tree(db_name):
    cfg = _cfg(
        db_name, source_tag="t1",
        engine="ReplacingMergeTree(created_at) ORDER BY (id)",
    )
    sink = ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    with sink.backend.connect() as client:
        r = client.query(
            "SELECT engine FROM system.tables WHERE database = {db:String} AND name = {tbl:String}",
            parameters={"db": db_name, "tbl": "chunks"},
        )
        assert r.result_rows[0][0] == "ReplacingMergeTree"
