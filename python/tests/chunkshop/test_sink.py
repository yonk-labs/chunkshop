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


def test_create_and_write_roundtrip(ensure_pg):
    dsn = ensure_pg
    cfg = TargetConfig(
        type="postgres",
        dsn_env=DSN_ENV,
        database="chunkshop_test",
        table="sink_smoke",
        mode="overwrite",
        hnsw=False,  # skip index on tiny test (HNSW needs ≥1 row and takes seconds)
    )
    sink = load_sink(cfg, embed_dim=4)
    sink.create_table()
    chunks = [
        Chunk(doc_id="d1", seq_num=0, original_content="hello",
              embedded_content="hello", metadata={"strategy": "t"}),
        Chunk(doc_id="d1", seq_num=1, original_content="world",
              embedded_content="world", metadata={"strategy": "t"}),
    ]
    embeddings = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    tags = [["hi"], ["world", "planet"]]
    sink.write_document("d1", chunks, embeddings, tags)

    assert sink.count_docs() == 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, doc_id, seq_num, original_content, tags "
            "FROM chunkshop_test.sink_smoke ORDER BY seq_num"
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0] == ("d1::0", "d1", 0, "hello", ["hi"])
        assert rows[1] == ("d1::1", "d1", 1, "world", ["world", "planet"])
        cur.execute("DROP SCHEMA chunkshop_test CASCADE")
        conn.commit()


def test_concurrent_create_table_same_schema(ensure_pg):
    """Regression: two cells targeting the same schema used to race on
    CREATE SCHEMA IF NOT EXISTS and one would fail with a pg_namespace unique
    violation. Fixed via pg_advisory_xact_lock in sink.create_table.
    """
    import concurrent.futures as cf

    schema = "chunkshop_concur"

    # Pre-clean so the test is idempotent
    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()

    def _create_one(idx: int) -> None:
        cfg = TargetConfig(
            type="postgres",
            dsn_env=DSN_ENV,
            database=schema,
            table=f"concur_t{idx}",
            mode="overwrite",
            hnsw=False,
        )
        sink = load_sink(cfg, embed_dim=4)
        sink.create_table()

    # 6 concurrent creators into the same schema
    N = 6
    with cf.ThreadPoolExecutor(max_workers=N) as pool:
        futures = [pool.submit(_create_one, i) for i in range(N)]
        for f in futures:
            f.result()  # re-raises if any cell failed

    # Verify all N tables exist under the one schema
    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM pg_tables WHERE schemaname=%s", (schema,)
        )
        assert cur.fetchone()[0] == N
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.commit()


def test_write_rejects_length_mismatch(ensure_pg):
    cfg = TargetConfig(
        type="postgres",
        dsn_env=DSN_ENV,
        database="chunkshop_test",
        table="sink_mismatch",
        mode="overwrite",
        hnsw=False,
    )
    sink = load_sink(cfg, embed_dim=2)
    sink.create_table()
    chunks = [Chunk(doc_id="d1", seq_num=0, original_content="x", embedded_content="x", metadata={})]
    embeddings = np.array([[1, 0], [0, 1]], dtype=np.float32)  # 2 rows vs 1 chunk
    tags = [["t"]]
    with pytest.raises(ValueError, match="length mismatch"):
        sink.write_document("d1", chunks, embeddings, tags)
    # cleanup
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA chunkshop_test CASCADE")
        conn.commit()
