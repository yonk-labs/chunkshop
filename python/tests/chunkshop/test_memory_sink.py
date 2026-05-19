"""Integration: MemorySink stamps cell-level promoted fields + DDL."""
import os, psycopg, pytest, numpy as np
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()

from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks import load_sink

PROMO = [{"path": "kind", "type": "text"}, {"path": "session_id", "type": "text"},
         {"path": "tier", "type": "text"}, {"path": "namespace", "type": "text"},
         {"path": "subject", "type": "text"}, {"path": "predicate", "type": "text"},
         {"path": "object", "type": "text"}, {"path": "support_span", "type": "text"},
         {"path": "recorded_at", "type": "timestamptz"},
         {"path": "effective_from", "type": "timestamptz"},
         {"path": "effective_to", "type": "timestamptz"},
         {"path": "retracted", "type": "boolean"},
         {"path": "retracted_at", "type": "timestamptz"}]


def _target(**ov):
    k = dict(type="postgres", dsn_env=DSN_ENV, database="chunkshop_test_memory",
             table="mem", mode="create_if_missing", source_tag="ns1",
             promote_metadata=PROMO,
             memory={"tier": "provisional", "supersede": False})
    k.update(ov)
    return TargetConfig(**k)


def test_memorysink_returned_and_stamps(ensure_pg):
    sink = load_sink(_target(), embed_dim=3)
    assert sink.__class__.__name__ == "MemorySink"
    sink.create_table()
    ch = Chunk(doc_id="s1", seq_num=0, original_content="raw",
               embedded_content="raw", metadata={"session_id": "s1"})
    sink.write_document("s1", [ch], np.array([[0.1, 0.2, 0.3]]), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT kind, tier, namespace, session_id, recorded_at "
                    "FROM chunkshop_test_memory.mem")
        row = cur.fetchone()
    assert row[0] == "episode" and row[1] == "provisional"
    assert row[2] == "ns1" and row[3] == "s1" and row[4] is not None
