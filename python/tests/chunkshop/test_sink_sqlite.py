"""Integration tests for SqliteSink. No external infrastructure — uses a tmp file.

Covers SC-003 (basic ingest), SC-008 (append preflight), SC-009 (overwrite safety),
SC-010 (delete_orphans on both tables), SC-011/SC-016 (HNSW no-op + warning),
SC-012 (promote_metadata via json_extract).
"""
import logging
import numpy as np
import pytest

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig, PromoteColumn
from chunkshop.sinks.sqlite import SqliteSink, _HNSW_WARNED


@pytest.fixture
def dsn(tmp_path, monkeypatch):
    """File-backed sqlite DB so per-call backend.connect() sees persistent data."""
    db_path = tmp_path / "chunkshop_sqlite_test.db"
    monkeypatch.setenv("SQLITE_TEST_PATH", str(db_path))
    return "SQLITE_TEST_PATH"


def _cfg(dsn_env, mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="sqlite", dsn_env=dsn_env, database="ignored",
        table="chunks", mode=mode, **kw,
    )


def _make_chunks(doc_id, n=3):
    return [
        Chunk(
            doc_id=doc_id, seq_num=i,
            original_content=f"chunk {i}",
            embedded_content=f"chunk {i}",
            metadata={"lang": "en", "section": f"s{i}"},
        ) for i in range(n)
    ]


def test_sc003_basic_ingest(dsn):
    """SC-003: SqliteSink writes to chunks + chunks_vec atomically."""
    cfg = _cfg(dsn, source_tag="t1")
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 3)
    embs = np.random.rand(3, 4).astype(np.float32)
    sink.write_document("d1", chunks, embs, [["a"], ["b"], ["c"]])
    assert sink.count_docs() == 1


def test_sc008_append_dim_mismatch(dsn):
    """SC-008: append-mode preflight on SQLite errors clearly on dim mismatch."""
    cfg1 = _cfg(dsn, source_tag="t1")
    sink1 = SqliteSink(cfg1, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _cfg(dsn, mode="append", source_tag="t2")
    sink2 = SqliteSink(cfg2, sink1.backend, embed_dim=8)
    with pytest.raises(RuntimeError, match=r"target dim 4 != cell embed_dim 8"):
        sink2.create_table()


def test_sc009_overwrite_foreign_tag(dsn):
    """SC-009: overwrite refuses to drop a table holding a foreign source_tag."""
    cfg1 = _cfg(dsn, source_tag="t1")
    sink1 = SqliteSink(cfg1, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _cfg(dsn, source_tag="t2")
    sink2 = SqliteSink(cfg2, sink1.backend, embed_dim=4)
    with pytest.raises(RuntimeError, match=r"foreign source_tag"):
        sink2.create_table()


def test_sc010_delete_orphans_both_tables(dsn):
    """SC-010: delete_orphans deletes from chunks AND chunks_vec."""
    cfg = _cfg(dsn, source_tag="t1", delete_orphans=True)
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", _make_chunks("d1", 5), np.random.rand(5, 4).astype(np.float32), [[]] * 5)
    sink.write_document("d1", _make_chunks("d1", 2), np.random.rand(2, 4).astype(np.float32), [[]] * 2)

    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq_main()}")
        assert cur.fetchone()[0] == 2
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq_vec()}")
        assert cur.fetchone()[0] == 2


def test_sc011_sc016_hnsw_logs_warning_once(dsn, caplog):
    """SC-011 / SC-016: hnsw=true on SQLite logs a warning once per process."""
    _HNSW_WARNED.clear()
    with caplog.at_level(logging.WARNING):
        cfg = _cfg(dsn, source_tag="t1", hnsw=True)
        SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
        # Second instance — no second warning
        SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    warnings = [r for r in caplog.records if "no-op" in r.message]
    assert len(warnings) == 1


def test_sc012_promote_metadata(dsn):
    """SC-012: promote_metadata column is populated via _jsonb_path_get during write."""
    cfg = _cfg(dsn, source_tag="t1",
               promote_metadata=[PromoteColumn(path="lang", type="text")])
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 1)
    sink.write_document("d1", chunks, np.random.rand(1, 4).astype(np.float32), [[]])
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT lang FROM {sink._fq_main()}")
        rows = cur.fetchall()
        assert rows[0][0] == "en"
