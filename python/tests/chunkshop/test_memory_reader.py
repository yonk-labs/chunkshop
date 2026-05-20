"""Integration tests for chunkshop.memory.read_pre_chunked: round-trips
chunkshop SP-A's agent_memory.memory table into pg-raggraph's
ingest_records shape, with O2 (consolidated-wins) and retracted-aware
defaults enforced at the read layer."""
import json
import os
import psycopg
import pytest
from pathlib import Path

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
    os.environ["CHUNKSHOP_MEMORY_DSN"] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS public.chunkshop_staging CASCADE")
        cur.execute("DROP SCHEMA IF EXISTS agent_memory CASCADE")
        conn.commit()


from chunkshop.memory import ensure_staging_table, stage_events, read_pre_chunked
from chunkshop.config import load_config
from chunkshop.runner import run_cell

PRESETS = Path(__file__).resolve().parents[2] / "src/chunkshop/configs/memory"
FIX = Path(__file__).resolve().parents[2] / "tests/fixtures/memory_session.jsonl"


def _backdate(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "UPDATE public.chunkshop_staging SET staged_at = now() - interval '2 hours'"
        )
        c.commit()


def _seed_and_consolidate(dsn):
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    events = [json.loads(l) for l in FIX.read_text().splitlines() if l.strip()]
    stage_events(dsn, events, table="chunkshop_staging", schema="public")
    _backdate(dsn)
    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    r = run_cell(cfg)
    assert r.error is None and r.chunks_written > 0, f"seed failed: {r}"


def _insert_synthetic_fact(dsn, *, doc_id, seq_num, subject, predicate, obj,
                           support_span="evidence", retracted=False,
                           namespace=None, tier="consolidated", embed_dim=384):
    """The default extractive consolidator emits kind='fact' rows but leaves
    subject/predicate/object NULL — structured triples come from a
    user-wired consolidator. Tests that exercise the reader's SPO mapping
    inject rows directly here so the reader contract is verified
    independently of which consolidator filled the table."""
    zero_vec = "[" + ",".join(["0"] * embed_dim) + "]"
    rid = f"{namespace or 'default'}::{doc_id}::synthetic_{seq_num}"
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_memory.memory "
            "(id, doc_id, seq_num, original_content, embedded_content, "
            " tags, metadata, embedding, source, "
            " kind, tier, namespace, subject, predicate, object, "
            " support_span, retracted, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, '{}', '{}'::jsonb, %s::vector, "
            "        'default', "
            "        'fact', %s, %s, %s, %s, %s, %s, %s, now())",
            (rid, doc_id, seq_num, f"{subject} {predicate} {obj}",
             f"{subject} {predicate} {obj}", zero_vec,
             tier, namespace, subject, predicate, obj, support_span, retracted))
        c.commit()


def test_yields_records_with_pg_raggraph_shape(ensure_pg):
    _seed_and_consolidate(ensure_pg)
    records = list(read_pre_chunked(ensure_pg))
    assert records, "expected at least one session record"
    rec = records[0]
    # exact key set pg-raggraph's ingest_records consumes
    for k in ("text", "source_id", "metadata", "pre_chunked",
             "known_entities", "known_relationships", "skip_llm"):
        assert k in rec, f"missing key {k!r}; got {sorted(rec)}"
    assert rec["skip_llm"] is True
    assert rec["source_id"].startswith("memory:")


def test_episode_rows_become_pre_chunked_with_embeddings(ensure_pg):
    _seed_and_consolidate(ensure_pg)
    records = list(read_pre_chunked(ensure_pg))
    pre = [pc for r in records for pc in r["pre_chunked"]]
    assert pre, "expected episode rows -> pre_chunked entries"
    for pc in pre:
        assert pc["content"], "pre_chunked content must be non-empty"
        assert isinstance(pc["embedding"], list)
        assert len(pc["embedding"]) > 0
        assert all(isinstance(x, float) for x in pc["embedding"])
        assert "embedded_content" in pc


def test_fact_rows_become_known_relationships(ensure_pg):
    _seed_and_consolidate(ensure_pg)
    _insert_synthetic_fact(ensure_pg, doc_id="s1", seq_num=100,
                           subject="queue", predicate="uses", obj="postgres",
                           support_span="we migrated to postgres")
    records = list(read_pre_chunked(ensure_pg))
    rels = [r for rec in records for r in rec["known_relationships"]]
    assert rels, "synthetic fact should yield at least one relationship"
    match = [r for r in rels if r["src"] == "queue"
             and r["dst"] == "postgres" and r["rel_type"] == "uses"]
    assert len(match) == 1, f"expected exactly one queue->postgres rel; got {rels}"
    assert match[0].get("description") == "we migrated to postgres"
    # known_entities must contain both src and dst names
    s1 = next(rec for rec in records if rec["metadata"]["session_id"] == "s1")
    names = {e["name"] for e in s1["known_entities"]}
    assert "queue" in names and "postgres" in names


def test_default_excludes_retracted_O2(ensure_pg):
    """A retracted fact (soft-invalidated) must be hidden by default;
    explicit opt-in returns it."""
    _seed_and_consolidate(ensure_pg)
    _insert_synthetic_fact(ensure_pg, doc_id="s1", seq_num=200,
                           subject="queue", predicate="uses", obj="redis",
                           retracted=True)
    _insert_synthetic_fact(ensure_pg, doc_id="s1", seq_num=201,
                           subject="queue", predicate="uses", obj="postgres",
                           retracted=False)
    default_rels = [r for rec in read_pre_chunked(ensure_pg)
                    for r in rec["known_relationships"]]
    all_rels = [r for rec in read_pre_chunked(ensure_pg, include_retracted=True)
                for r in rec["known_relationships"]]
    default_objs = {r["dst"] for r in default_rels if r["src"] == "queue"}
    all_objs = {r["dst"] for r in all_rels if r["src"] == "queue"}
    assert "postgres" in default_objs
    assert "redis" not in default_objs, "retracted fact must be hidden by default"
    assert "redis" in all_objs, "include_retracted=True must surface it"


def test_session_ids_filter(ensure_pg):
    _seed_and_consolidate(ensure_pg)
    all_records = list(read_pre_chunked(ensure_pg))
    assert len(all_records) >= 2, "fixture should have multiple sessions"
    target = all_records[0]["metadata"]["session_id"]
    filtered = list(read_pre_chunked(ensure_pg, session_ids=[target]))
    assert len(filtered) == 1
    assert filtered[0]["metadata"]["session_id"] == target
    # empty list short-circuits to no rows
    assert list(read_pre_chunked(ensure_pg, session_ids=[])) == []


def test_tier_default_is_consolidated(ensure_pg):
    """Default tier='consolidated' must not surface provisional rows."""
    _seed_and_consolidate(ensure_pg)
    # default
    cons = list(read_pre_chunked(ensure_pg))
    assert cons
    # tier=None disables the filter
    all_tiers = list(read_pre_chunked(ensure_pg, tier=None))
    # consolidated count <= all-tiers count (provisional may or may not be present
    # depending on whether realtime ran; we only assert the filter is wired).
    assert len(cons) <= len(all_tiers)
    for r in cons:
        assert r["metadata"]["tier"] == "consolidated"
