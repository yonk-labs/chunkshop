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


def test_append_preflight_adds_missing_source_column(ensure_pg):
    # Create the table in overwrite mode (no source_tag set — simulating pre-v0.3.0 table).
    cfg_old = _mk_target(mode="overwrite")
    PgVectorSink(cfg_old, embed_dim=4).create_table()

    # Manually drop the source column to simulate a pre-existing table missing it
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE chunkshop_test_append.target_a DROP COLUMN IF EXISTS source")
        conn.commit()

    # Now append — should auto-add `source` column.
    cfg_append = _mk_target(mode="append", source_tag="pdfs")
    PgVectorSink(cfg_append, embed_dim=4).create_table()

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            ("chunkshop_test_append", "target_a"),
        )
        cols = {r[0] for r in cur.fetchall()}
        assert "source" in cols


def test_append_adds_promote_columns(ensure_pg):
    cfg_create = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_create, embed_dim=4).create_table()

    cfg_append = _mk_target(
        mode="append",
        source_tag="pdfs",
        promote_metadata=[
            {"path": "language", "type": "text"},
            {"path": "entities.ORG", "type": "text[]"},
        ],
    )
    PgVectorSink(cfg_append, embed_dim=4).create_table()

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            ("chunkshop_test_append", "target_a"),
        )
        cols = {r[0] for r in cur.fetchall()}
        # Dotted paths become double-underscored AND lowercased via PromoteColumn.column_name.
        assert "language" in cols
        assert "entities__org" in cols


def test_append_dim_check_works_on_empty_table(ensure_pg):
    # Create dim=4 table, don't write any rows, then append with matching dim — must succeed.
    cfg_create = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_create, embed_dim=4).create_table()

    # Verify table is empty (no rows for vector_dims to see).
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunkshop_test_append.target_a")
        assert cur.fetchone()[0] == 0

    # Append with matching dim should not raise.
    cfg_append_match = _mk_target(mode="append", source_tag="pdfs")
    PgVectorSink(cfg_append_match, embed_dim=4).create_table()

    # Append with mismatched dim on empty table must still raise.
    cfg_append_mismatch = _mk_target(mode="append", source_tag="pdfs")
    with pytest.raises(RuntimeError, match="dim"):
        PgVectorSink(cfg_append_mismatch, embed_dim=16).create_table()


def test_append_fails_on_malformed_table(ensure_pg):
    # Pre-existing non-chunkshop table with the same name — no embedding column.
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS chunkshop_test_append")
        cur.execute(
            "CREATE TABLE chunkshop_test_append.target_a (id text PRIMARY KEY, payload text)"
        )
        conn.commit()

    cfg = _mk_target(mode="append", source_tag="pdfs")
    with pytest.raises(RuntimeError, match="does not appear to be a chunkshop target"):
        PgVectorSink(cfg, embed_dim=4).create_table()


def test_overwrite_refuses_foreign_source_tag(ensure_pg):
    # First cell populates the table with source_tag=pdfs
    cfg_a = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_a, embed_dim=4).create_table()
    # Insert one row with source='pdfs' to make the foreign tag detectable
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunkshop_test_append.target_a "
            "(id, doc_id, seq_num, original_content, embedded_content, "
            " tags, metadata, embedding, source) "
            "VALUES ('d1::0','d1',0,'x','x','{}','{}'::jsonb, '[1,0,0,0]'::vector, 'pdfs')"
        )
        conn.commit()

    # Second cell in overwrite mode with a different source_tag — should refuse.
    cfg_b = _mk_target(mode="overwrite", source_tag="web_scrape")
    with pytest.raises(RuntimeError, match="source_tag"):
        PgVectorSink(cfg_b, embed_dim=4).create_table()


def test_overwrite_force_bypasses_check(ensure_pg):
    cfg_a = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_a, embed_dim=4).create_table()
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunkshop_test_append.target_a "
            "(id, doc_id, seq_num, original_content, embedded_content, "
            " tags, metadata, embedding, source) "
            "VALUES ('d1::0','d1',0,'x','x','{}','{}'::jsonb, '[1,0,0,0]'::vector, 'pdfs')"
        )
        conn.commit()

    cfg_force = _mk_target(mode="overwrite", source_tag="web_scrape", force_overwrite=True)
    # Should not raise
    PgVectorSink(cfg_force, embed_dim=4).create_table()
