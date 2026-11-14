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


def test_multi_episode_session_no_seq_collision(ensure_pg):
    import numpy as np
    sink = load_sink(_target(), embed_dim=3)
    sink.create_table()
    ep1 = [Chunk("s1", 0, "episode one", "episode one",
                 {"session_id": "s1", "kind": "episode", "frame_seq": 0}),
           Chunk("s1", 1, "fact a", "fact a",
                 {"session_id": "s1", "kind": "fact", "subject": "a",
                  "predicate": "p", "object": "b"})]
    ep2 = [Chunk("s1", 0, "episode two", "episode two",
                 {"session_id": "s1", "kind": "episode", "frame_seq": 1})]
    sink.write_document("s1", ep1, np.array([[0.1, 0.2, 0.3]] * 2), [[], []])
    sink.write_document("s1", ep2, np.array([[0.1, 0.2, 0.3]]), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT count(*), count(distinct id) "
                    "FROM chunkshop_test_memory.mem")
        total, distinct = cur.fetchone()
        cur.execute("SELECT original_content FROM chunkshop_test_memory.mem "
                    "WHERE kind='episode' ORDER BY original_content")
        eps = [r[0] for r in cur.fetchall()]
    assert total == 3 and distinct == 3       # ep1 + its fact + ep2, none overwritten
    assert eps == ["episode one", "episode two"]   # BOTH episodes survived


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


@pytest.mark.parametrize("tag", ["ns1", None])
def test_provisional_second_process_does_not_overwrite_first_batch(ensure_pg, tag):
    """PYD-05: realtime runs are fresh processes; the seq base must resume
    from the table, not restart at 0 (which overwrote run 1's rows)."""
    import numpy as np
    emb = np.array([[0.1, 0.2, 0.3]])
    sink1 = load_sink(_target(source_tag=tag), embed_dim=3)
    sink1.create_table()
    sink1.write_document("s1", [Chunk("s1", 0, "batch one", "batch one",
                                      {"session_id": "s1"})], emb, [[]])
    sink2 = load_sink(_target(source_tag=tag), embed_dim=3)   # fresh sink = fresh process
    sink2.write_document("s1", [Chunk("s1", 0, "batch two", "batch two",
                                      {"session_id": "s1"})], emb, [[]])
    sink2.write_document("s1", [Chunk("s1", 0, "batch three", "batch three",
                                      {"session_id": "s1"})], emb, [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT seq_num, original_content "
                    "FROM chunkshop_test_memory.mem ORDER BY seq_num")
        rows = cur.fetchall()
    assert rows == [(0, "batch one"), (1, "batch two"), (2, "batch three")]
