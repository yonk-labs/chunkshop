"""E2E: stage → realtime cell → consolidate cell; assert store + pg-raggraph contract."""
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
FIX = Path(__file__).resolve().parents[2] / "tests/fixtures/memory_session.jsonl"

PGRG_FACT_COLS = {"subject", "predicate", "object", "support_span", "confidence",
                  "effective_from", "effective_to", "retracted", "retracted_at",
                  "extractor", "namespace"}


def _run(name):
    return run_cell(load_config(str(PRESETS / name)))


def _backdate_staging(dsn: str, table: str = "chunkshop_staging",
                      schema: str = "public") -> None:
    """Back-date staged_at by 2 hours so consolidate's strict-LT WHERE selects them.

    The consolidate WHERE is:
        coalesce(event_ts, staged_at) < now() - interval 'N seconds'
    With min_age_seconds=0 that becomes < now() (strict).  Rows inserted with
    DEFAULT now() have staged_at ≈ now() and fail the strict-LT predicate.
    Back-dating makes them unambiguously old without touching production semantics.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE {schema}.{table} SET staged_at = now() - interval \'2 hours\''
        )
        conn.commit()


def test_e2e_realtime_then_consolidate(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    events = [json.loads(l) for l in FIX.read_text().splitlines() if l.strip()]
    stage_events(dsn, events, table="chunkshop_staging", schema="public")

    r1 = _run("realtime.yaml")
    assert r1.error is None and r1.chunks_written > 0
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE tier='provisional'")
        assert cur.fetchone()[0] > 0

    # Back-date so consolidate's staged_at < now() strict predicate selects them.
    # (Realtime already advanced consumed.realtime; consolidate uses consumed.consolidated
    # — a separate key — so rows are still visible to consolidate after realtime ran.)
    _backdate_staging(dsn)

    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    r2 = run_cell(cfg)
    assert r2.error is None and r2.chunks_written > 0

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE tier='provisional'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE kind='episode'")
        assert cur.fetchone()[0] >= 1
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE kind='fact'")
        assert cur.fetchone()[0] >= 1


def test_pgraggraph_contract_columns_present(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    stage_events(dsn, [{"session_id": "s1", "seq": 1, "role": "user",
                        "content": "hello world this is a sentence."}],
                 table="chunkshop_staging", schema="public")
    # Back-date so consolidate's min_age_seconds=0 strict-LT predicate selects them.
    _backdate_staging(dsn)
    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    run_cell(cfg)
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='agent_memory' AND table_name='memory'")
        cols = {r[0] for r in cur.fetchall()}
    missing = PGRG_FACT_COLS - cols
    assert not missing, f"pg-raggraph contract drift: missing {missing}"
