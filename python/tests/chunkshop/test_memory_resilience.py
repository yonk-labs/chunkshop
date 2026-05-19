"""SP-A spec §143 operational invariants: O1 late-event rebuild, O3 crash/resume.

These were listed as in-scope integration tests in the design spec but deferred
by the implementation plan's Self-Review. Written here to resolve that
contradiction: either the behaviour holds, or the gap is made explicit with
evidence instead of a prose footnote.
"""
import json, os, psycopg, pytest
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


from chunkshop.memory import ensure_staging_table, stage_events
from chunkshop.config import load_config
from chunkshop.runner import run_cell

PRESETS = Path(__file__).resolve().parents[2] / "src/chunkshop/configs/memory"


def _backdate(dsn, table="chunkshop_staging", schema="public"):
    """Push staged_at into the past so consolidate's strict-LT age predicate
    (min_age_seconds=0 -> < now()) selects the rows."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {schema}.{table} SET staged_at = now() - interval '2 hours'")
        conn.commit()


def _consolidate():
    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    return run_cell(cfg)


def _episode_text(dsn, session_id="s1"):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(
            "SELECT string_agg(original_content, ' || ') "
            "FROM agent_memory.memory WHERE doc_id=%s AND kind='episode'",
            (session_id,))
        return cur.fetchone()[0] or ""


def test_o1_late_event_rebuilds_from_full_staging(ensure_pg):
    """Spec O1: 'late events -> next run rebuilds from full staging'.

    Consolidate s1, then a late turn arrives for s1 after it was consolidated.
    Re-consolidating must leave s1's consolidated memory reflecting the WHOLE
    session (early Redis turns + late RabbitMQ turn), not just the late
    fragment. Earlier consolidated content must not be destroyed.
    """
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    stage_events(dsn, [
        {"session_id": "s1", "seq": 1, "role": "user",
         "content": "We use Redis for the job queue."},
        {"session_id": "s1", "seq": 2, "role": "assistant",
         "content": "Understood, Redis backs the queue."},
    ], table="chunkshop_staging", schema="public")
    _backdate(dsn)
    r1 = _consolidate()
    assert r1.error is None and r1.chunks_written > 0
    assert "Redis" in _episode_text(dsn, "s1")

    # A late turn for the SAME session arrives after consolidation.
    stage_events(dsn, [
        {"session_id": "s1", "seq": 3, "role": "user",
         "content": "We switched the queue to RabbitMQ."},
    ], table="chunkshop_staging", schema="public")
    _backdate(dsn)
    r2 = _consolidate()
    assert r2.error is None

    text = _episode_text(dsn, "s1")
    # Spec O1: full-staging rebuild -> both old and new content present.
    assert "RabbitMQ" in text, f"late turn missing: {text!r}"
    assert "Redis" in text, (
        "O1 violation: re-consolidating after a late event destroyed the "
        f"earlier consolidated turns. s1 episode now only holds: {text!r}")


def test_o3_crash_mid_run_resumes_cleanly(ensure_pg, monkeypatch):
    """Spec O3: per-session commit; a crash leaves processed sessions
    consolidated, the rest pending; the next run resumes and completes."""
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    stage_events(dsn, [
        {"session_id": "s1", "seq": 1, "role": "user", "content": "Session one alpha."},
        {"session_id": "s2", "seq": 1, "role": "user", "content": "Session two beta."},
        {"session_id": "s3", "seq": 1, "role": "user", "content": "Session three gamma."},
    ], table="chunkshop_staging", schema="public")
    _backdate(dsn)

    from chunkshop.sinks.memory_pg import MemorySink
    real = MemorySink.write_document
    calls = {"n": 0}

    def boom(self, doc_id, chunks, embeddings, tags_per_chunk):
        calls["n"] += 1
        if calls["n"] == 3:                       # crash on the 3rd session
            raise RuntimeError("simulated crash mid-run")
        return real(self, doc_id, chunks, embeddings, tags_per_chunk)

    monkeypatch.setattr(MemorySink, "write_document", boom)
    r1 = _consolidate()
    assert r1.error is not None, "expected the simulated crash to surface"

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT doc_id FROM agent_memory.memory "
                    "WHERE kind='episode' ORDER BY doc_id")
        done = [r[0] for r in cur.fetchall()]
    assert done == ["s1", "s2"], f"per-session commit broken: {done}"

    monkeypatch.setattr(MemorySink, "write_document", real)   # crash cleared
    r2 = _consolidate()
    assert r2.error is None

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT doc_id, count(*) FROM agent_memory.memory "
                    "WHERE kind='episode' GROUP BY doc_id ORDER BY doc_id")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["s1", "s2", "s3"], f"resume incomplete: {rows}"
    assert all(cnt == 1 for _, cnt in rows), f"resume not idempotent: {rows}"
