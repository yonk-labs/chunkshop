"""Consolidated supersede and fact invalidation must work when source_tag is None (source IS NULL)."""
import os
import numpy as np
import psycopg
import pytest

from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink

DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
SCHEMA = "chunkshop_review_p1a_mem"

PROMO = [{"path": p, "type": t} for p, t in [
    ("kind", "text"), ("session_id", "text"), ("tier", "text"), ("namespace", "text"),
    ("subject", "text"), ("predicate", "text"), ("object", "text"),
    ("recorded_at", "timestamptz"), ("effective_from", "timestamptz"),
    ("effective_to", "timestamptz"), ("retracted", "boolean"),
    ("retracted_at", "timestamptz")]]


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
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def _target(tier, supersede, **ov):
    k = dict(type="postgres", dsn_env=DSN_ENV, database=SCHEMA, table="mem",
             mode="create_if_missing", source_tag="ns1", promote_metadata=PROMO,
             memory={"tier": tier, "supersede": supersede})
    k.update(ov)
    return TargetConfig(**k)


def _emb(n):
    return np.array([[0.1, 0.2, 0.3]] * n)


@pytest.mark.parametrize("tag", ["ns1", None])
def test_consolidated_supersedes_provisional(ensure_pg, tag):
    prov = load_sink(_target("provisional", False, source_tag=tag), embed_dim=3)
    prov.create_table()
    prov.write_document("s1", [Chunk("s1", i, f"p{i}", f"p{i}", {"session_id": "s1"})
                               for i in range(2)], _emb(2), [[], []])
    cons = load_sink(_target("consolidated", True, source_tag=tag), embed_dim=3)
    cons.write_document("s1", [Chunk("s1", 0, "c", "c",
                        {"session_id": "s1", "kind": "episode"})], _emb(1), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute(f"SELECT tier, seq_num FROM {SCHEMA}.mem WHERE doc_id='s1' ORDER BY seq_num")
        rows = cur.fetchall()
    assert rows == [("consolidated", 0)]  # provisional seq 1 must be gone


@pytest.mark.parametrize("tag", ["ns1", None])
def test_fact_invalidation_retracts_older_fact(ensure_pg, tag):
    def fact(sid, obj, eff):
        return Chunk(sid, 1, obj, obj, {"session_id": sid, "kind": "fact", "subject": "queue",
                                         "predicate": "uses", "object": obj, "effective_from": eff})
    s1 = load_sink(_target("consolidated", True, source_tag=tag), embed_dim=3)
    s1.create_table()
    s1.write_document("s1", [fact("s1", "redis", "2026-01-01T00:00:00+00:00")], _emb(1), [[]])
    load_sink(_target("consolidated", True, source_tag=tag), embed_dim=3).write_document(
        "s2", [fact("s2", "postgres", "2026-03-01T00:00:00+00:00")], _emb(1), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute(f"SELECT object, retracted FROM {SCHEMA}.mem WHERE kind='fact' "
                    "ORDER BY effective_from")
        rows = cur.fetchall()
    assert rows == [("redis", True), ("postgres", False)]
