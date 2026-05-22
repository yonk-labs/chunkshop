"""SC-002/003: pg sink wires ensure_fts on create; append validates it exists."""
import os
import pytest

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
def _pg_up():
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2): return True
    except Exception: return False
pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")

from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink
import psycopg


def _has_fts_idx(schema):
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname='chunks_fts_idx'", (schema,))
        return cur.fetchone() is not None


def _drop(schema):
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'); c.commit()


def test_create_if_missing_builds_fts_index():
    schema = "chunkshop_fts_cim"
    try:
        cfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                           mode="create_if_missing", hnsw=False,
                           fts={"enabled": True, "language": "english"})
        load_sink(cfg, embed_dim=384).create_table()
        assert _has_fts_idx(schema)
    finally:
        _drop(schema)


def test_overwrite_builds_fts_index():
    schema = "chunkshop_fts_ow"
    try:
        cfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                           mode="overwrite", hnsw=False,
                           fts={"enabled": True, "language": "english"})
        load_sink(cfg, embed_dim=384).create_table()
        assert _has_fts_idx(schema)
    finally:
        _drop(schema)


def test_append_without_fts_index_raises():
    schema = "chunkshop_fts_append"
    try:
        base = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                            mode="create_if_missing", hnsw=False)
        load_sink(base, embed_dim=384).create_table()  # no fts
        appcfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                              mode="append", source_tag="t", hnsw=False,
                              fts={"enabled": True, "language": "english"})
        with pytest.raises(RuntimeError, match="fts"):
            load_sink(appcfg, embed_dim=384).create_table()
    finally:
        _drop(schema)


def test_no_fts_config_builds_no_index():
    schema = "chunkshop_fts_off"
    try:
        cfg = TargetConfig(type="postgres", dsn=DSN, database=schema, table="chunks",
                           mode="overwrite", hnsw=False)  # fts=None
        load_sink(cfg, embed_dim=384).create_table()
        assert not _has_fts_idx(schema)
    finally:
        _drop(schema)
