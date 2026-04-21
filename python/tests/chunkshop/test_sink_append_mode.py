import os
import pytest
import psycopg

from chunkshop.config import TargetConfig
from chunkshop.sink import PgVectorSink


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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_append CASCADE")
        conn.commit()


def _mk_target(**overrides) -> TargetConfig:
    kwargs = {
        "dsn_env": DSN_ENV,
        "schema": "chunkshop_test_append",
        "table": "target_a",
        "hnsw": False,
    }
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


def test_append_fails_when_table_missing(ensure_pg):
    cfg = _mk_target(mode="append", source_tag="pdfs")
    sink = PgVectorSink(cfg, embed_dim=4)
    with pytest.raises(RuntimeError, match="does not exist"):
        sink.create_table()


def test_append_fails_on_dim_mismatch(ensure_pg):
    # Create the table with dim=4
    cfg_create = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_create, embed_dim=4).create_table()

    # Try to append with dim=8 — should fail pre-flight
    cfg_bad = _mk_target(mode="append", source_tag="pdfs")
    with pytest.raises(RuntimeError, match="dim"):
        PgVectorSink(cfg_bad, embed_dim=8).create_table()
