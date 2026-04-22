"""Runner still processes docs correctly when framer=identity (default)."""
import json
import os
import pytest
import psycopg

from chunkshop.config import CellConfig
from chunkshop.runner import run_cell


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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_framer CASCADE")
        conn.commit()


def test_identity_framer_default_preserves_existing_behavior(ensure_pg, tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({
        "documents": [
            {"id": "d1", "title": "T1",
             "content": "# Alpha\n\nAlpha bravo charlie delta echo foxtrot "
                        "golf hotel india juliet kilo lima mike november "
                        "oscar papa quebec romeo sierra tango uniform victor."},
        ]
    }))

    cfg = CellConfig(
        cell_name="framer_default",
        source={"type": "json_corpus", "path": str(corpus)},
        chunker={"type": "hierarchy"},
        embedder={"type": "fastembed",
                  "model_name": "Xenova/bge-base-en-v1.5-int8",
                  "dim": 768, "threads": 2},
        target={"dsn_env": DSN_ENV, "schema": "chunkshop_test_framer",
                "table": "t", "mode": "overwrite", "hnsw": False},
    )

    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.docs_processed == 1
    assert result.chunks_written >= 1
