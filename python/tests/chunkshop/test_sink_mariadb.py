"""Integration tests for MariaDbSink. Skipped unless $CHUNKSHOP_TEST_DSN_MARIADB is set
and points to a reachable MariaDB 11.7+ instance."""
import os
import numpy as np
import pytest

pytest.importorskip("pymysql")

from chunkshop.config import TargetConfig, PromoteColumn
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.mariadb import MariaDbSink
from chunkshop.backends.mariadb import MariaDBBackend


DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


@pytest.fixture
def db_name():
    return "chunkshop_test_v4"


@pytest.fixture
def cleanup(db_name):
    """Drop the test database before and after each test."""
    os.environ.setdefault(DSN_VAR, DSN or "")
    be = MariaDBBackend(dsn_env=DSN_VAR)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        conn.commit()
    yield
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        conn.commit()


def _make_cfg(db_name, table="chunks", mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="mariadb",
        dsn_env=DSN_VAR,
        database=db_name,
        table=table,
        mode=mode,
        **kw,
    )


def _make_chunks(doc_id, n=3):
    return [
        Chunk(
            doc_id=doc_id, seq_num=i,
            original_content=f"chunk {i} body",
            embedded_content=f"chunk {i} body",
            metadata={"lang": "en", "section": f"sec_{i}"},
        )
        for i in range(n)
    ]


def test_sc003_create_and_write(cleanup, db_name):
    """SC-005: a MariaDB sink can ingest a sample doc into a chunks table."""
    cfg = _make_cfg(db_name, source_tag="t1")
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("doc1", n=3)
    embs = np.random.rand(3, 4).astype(np.float32)
    tags = [["a"], ["b"], ["c"]]
    sink.write_document("doc1", chunks, embs, tags)
    assert sink.count_docs() == 1


def test_sc006_append_dim_mismatch_clear_error(cleanup, db_name):
    """SC-008: append-mode preflight fails clearly on dim mismatch."""
    cfg1 = _make_cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = MariaDbSink(cfg1, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _make_cfg(db_name, mode="append", source_tag="t2")
    sink2 = MariaDbSink(cfg2, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=8)
    with pytest.raises(RuntimeError, match=r"target dim 4 != cell embed_dim 8"):
        sink2.create_table()


def test_sc007_overwrite_foreign_tag_safety(cleanup, db_name):
    """SC-009: overwrite mode refuses to drop a table holding a foreign source_tag."""
    cfg1 = _make_cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = MariaDbSink(cfg1, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _make_cfg(db_name, mode="overwrite", source_tag="t2")
    sink2 = MariaDbSink(cfg2, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    with pytest.raises(RuntimeError, match=r"foreign source_tag"):
        sink2.create_table()


def test_sc008_delete_orphans(cleanup, db_name):
    """SC-010: delete_orphans removes chunks with seq_num beyond the new chunkset."""
    cfg = _make_cfg(db_name, source_tag="t1", delete_orphans=True)
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    # Initial write: 5 chunks
    sink.write_document("d1", _make_chunks("d1", 5), np.random.rand(5, 4).astype(np.float32), [[]] * 5)
    # Re-write with 2 chunks → seq 2, 3, 4 should be deleted
    sink.write_document("d1", _make_chunks("d1", 2), np.random.rand(2, 4).astype(np.float32), [[]] * 2)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq()}")
        assert cur.fetchone()[0] == 2


def test_sc009_hnsw_index_present(cleanup, db_name):
    """SC-011: HNSW vector index gets created on the chunks table."""
    cfg = _make_cfg(db_name, source_tag="t1", hnsw=True)
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SHOW INDEX FROM {sink._fq()}")
        rows = cur.fetchall()
        # Look for our vec_idx — index_type column is typically index 10 in SHOW INDEX
        names = [r[2] for r in rows]
        assert "vec_idx" in names


def test_sc010_promote_metadata_jsonpath(cleanup, db_name):
    """SC-012: promote_metadata extracts a JSON-path value into a typed column."""
    cfg = _make_cfg(
        db_name, source_tag="t1",
        promote_metadata=[PromoteColumn(path="lang", type="text")],
    )
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 1)  # metadata={"lang": "en", ...}
    sink.write_document("d1", chunks, np.random.rand(1, 4).astype(np.float32), [[]])
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT lang FROM {sink._fq()}")
        rows = cur.fetchall()
        assert rows and rows[0][0] == "en"
