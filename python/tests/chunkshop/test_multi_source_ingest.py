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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_multi CASCADE")
        conn.commit()


def _json_corpus_fixture(tmp_path, prefix: str):
    # Sections must exceed HierarchyChunker.min_section_chars (default 100)
    # or the chunker drops them. Repeat the word list to clear that bar.
    body_alpha = " ".join(["Alpha bravo charlie delta echo."] * 6)
    body_delta = " ".join(["Delta echo foxtrot golf hotel."] * 6)
    path = tmp_path / f"{prefix}.json"
    path.write_text(json.dumps({
        "documents": [
            {"id": f"{prefix}_1", "title": "t1",
             "content": f"# Alpha\n\n{body_alpha}"},
            {"id": f"{prefix}_2", "title": "t2",
             "content": f"# Delta\n\n{body_delta}"},
        ]
    }))
    return str(path)


def test_two_cells_append_into_one_table(ensure_pg, tmp_path):
    dsn = ensure_pg
    corpus_a = _json_corpus_fixture(tmp_path, "cell_a")
    corpus_b = _json_corpus_fixture(tmp_path, "cell_b")

    common_target = {
        "dsn_env": DSN_ENV,
        "schema": "chunkshop_test_multi",
        "table": "unified",
        "hnsw": False,
    }

    cfg_a = CellConfig(
        cell_name="cell_a",
        source={"type": "json_corpus", "path": corpus_a},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        target={**common_target, "mode": "create_if_missing", "source_tag": "cell_a_source"},
    )
    cfg_b = CellConfig(
        cell_name="cell_b",
        source={"type": "json_corpus", "path": corpus_b},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        target={**common_target, "mode": "append", "source_tag": "cell_b_source"},
    )

    r1 = run_cell(cfg_a)
    assert r1.error is None, r1.error
    r2 = run_cell(cfg_b)
    assert r2.error is None, r2.error

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, COUNT(*) FROM chunkshop_test_multi.unified GROUP BY source"
        )
        by_source = dict(cur.fetchall())
        assert by_source.get("cell_a_source", 0) > 0
        assert by_source.get("cell_b_source", 0) > 0

        cur.execute(
            "SELECT COUNT(*) FROM chunkshop_test_multi.unified "
            "WHERE source='cell_a_source'"
        )
        only_a = cur.fetchone()[0]
        assert only_a == by_source["cell_a_source"]
