import json
import os
import textwrap
from pathlib import Path

import pytest
import psycopg

from chunkshop.config import load_config
from chunkshop.runner import run_cell


DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg",
)


@pytest.fixture
def ensure_pg():
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {DSN} not reachable: {exc}")
    os.environ["CHUNKSHOP_TEST_DSN"] = DSN
    yield DSN
    # Teardown: drop the test schema regardless of outcome
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS chunkshop_runner_test CASCADE")
            conn.commit()
    except Exception:
        pass


def test_run_cell_end_to_end(ensure_pg, tmp_path):
    corpus = tmp_path / "c.json"
    corpus.write_text(json.dumps({
        "documents": [
            {"id": "d1", "content": "The Supreme Court decided Bostock.", "title": "Case A"},
            {"id": "d2", "content": "Apple v. Pepper is an antitrust case.", "title": "Case B"},
        ]
    }))
    yaml_path = tmp_path / "cell.yaml"
    yaml_path.write_text(textwrap.dedent(f"""
        cell_name: runner_smoke
        source:
          type: json_corpus
          path: {corpus}
        chunker:
          type: sentence_aware
        embedder:
          type: fastembed
          model_name: BAAI/bge-small-en-v1.5
          dim: 384
        target:
          type: postgres
          dsn_env: CHUNKSHOP_TEST_DSN
          database: chunkshop_runner_test
          table: runner_smoke
          mode: overwrite
          hnsw: false
        runtime:
          doc_limit: 2
    """))
    cfg = load_config(yaml_path)

    result = run_cell(cfg)
    assert result.cell_name == "runner_smoke"
    assert result.docs_processed == 2
    assert result.chunks_written >= 2
    assert result.error is None
    assert result.wall_seconds > 0

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunkshop_runner_test.runner_smoke")
        assert cur.fetchone()[0] >= 2
        cur.execute(
            "SELECT doc_id, seq_num, original_content "
            "FROM chunkshop_runner_test.runner_smoke ORDER BY doc_id, seq_num"
        )
        rows = cur.fetchall()
        # Both documents should appear; content preserved
        doc_ids = {r[0] for r in rows}
        assert doc_ids == {"d1", "d2"}
        assert any("Bostock" in r[2] for r in rows)


def test_run_cell_reports_error_without_raising(ensure_pg, tmp_path):
    # Point at a nonexistent source file; runner should return error in result, not raise.
    yaml_path = tmp_path / "cell.yaml"
    yaml_path.write_text(textwrap.dedent(f"""
        cell_name: runner_err
        source:
          type: json_corpus
          path: /nonexistent/path.json
        chunker:
          type: sentence_aware
        embedder:
          type: fastembed
          model_name: BAAI/bge-small-en-v1.5
          dim: 384
        target:
          type: postgres
          dsn_env: CHUNKSHOP_TEST_DSN
          database: chunkshop_runner_test
          table: runner_err
          mode: overwrite
          hnsw: false
    """))
    cfg = load_config(yaml_path)
    result = run_cell(cfg)
    assert result.error is not None
    assert result.docs_processed == 0
