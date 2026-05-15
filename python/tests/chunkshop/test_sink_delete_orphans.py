"""Integration tests for `target.delete_orphans` — closes the deletion gap when
a doc shrinks across re-runs (last run wrote 12 chunks; this run writes 8)."""
import os
import pytest
import psycopg
import numpy as np

from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"


@pytest.fixture
def ensure_pg():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_orphan CASCADE")
        conn.commit()


def _mk_target(**overrides) -> TargetConfig:
    kwargs = {
        "type": "postgres",
        "dsn_env": DSN_ENV,
        "database": "chunkshop_test_orphan",
        "table": "target_o",
        "hnsw": False,
        "mode": "create_if_missing",
        "source_tag": "notes",
    }
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


def _chunks(doc_id: str, n: int, prefix: str) -> tuple[list[Chunk], np.ndarray, list[list[str]]]:
    chunks = [
        Chunk(
            doc_id=doc_id,
            seq_num=i,
            original_content=f"{prefix} body {i}",
            embedded_content=f"{prefix} body {i}",
            metadata={},
        )
        for i in range(n)
    ]
    emb = np.tile(np.eye(1, 4, dtype=np.float32), (n, 1))  # n×4 unit-ish vectors
    tags = [[]] * n
    return chunks, emb, tags


def _seq_nums(dsn: str, doc_id: str) -> list[int]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT seq_num FROM chunkshop_test_orphan.target_o WHERE doc_id = %s ORDER BY seq_num",
            (doc_id,),
        )
        return [r[0] for r in cur.fetchall()]


def test_delete_orphans_off_leaves_stale_chunks(ensure_pg):
    """Without the flag, shrinking a doc from 5 -> 2 chunks leaves 3 stale rows.
    This is the documented deletion gap — kept as a regression for the default."""
    sink = load_sink(_mk_target(delete_orphans=False), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", *_chunks("d1", 5, "v1"))
    sink.write_document("d1", *_chunks("d1", 2, "v2"))
    assert _seq_nums(ensure_pg, "d1") == [0, 1, 2, 3, 4]


def test_delete_orphans_on_drops_excess_seq_nums(ensure_pg):
    """With the flag, shrinking 5 -> 2 leaves only seq_num 0,1 — and they hold v2 content."""
    sink = load_sink(_mk_target(delete_orphans=True), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", *_chunks("d1", 5, "v1"))
    sink.write_document("d1", *_chunks("d1", 2, "v2"))
    assert _seq_nums(ensure_pg, "d1") == [0, 1]
    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT original_content FROM chunkshop_test_orphan.target_o "
            "WHERE doc_id='d1' ORDER BY seq_num"
        )
        bodies = [r[0] for r in cur.fetchall()]
    assert bodies == ["v2 body 0", "v2 body 1"]


def test_delete_orphans_on_growing_doc_is_noop(ensure_pg):
    """Growing 2 -> 4 with the flag on must not drop the new chunks (DELETE is
    `seq_num >= len(chunks)`, so writing 4 means delete `>= 4`, which catches none)."""
    sink = load_sink(_mk_target(delete_orphans=True), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", *_chunks("d1", 2, "v1"))
    sink.write_document("d1", *_chunks("d1", 4, "v2"))
    assert _seq_nums(ensure_pg, "d1") == [0, 1, 2, 3]


def test_delete_orphans_only_touches_target_doc(ensure_pg):
    """Shrinking d1 must not touch d2's chunks."""
    sink = load_sink(_mk_target(delete_orphans=True), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", *_chunks("d1", 5, "v1"))
    sink.write_document("d2", *_chunks("d2", 3, "v1"))
    sink.write_document("d1", *_chunks("d1", 1, "v2"))
    assert _seq_nums(ensure_pg, "d1") == [0]
    assert _seq_nums(ensure_pg, "d2") == [0, 1, 2]
