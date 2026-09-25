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


def test_overwrite_source_failure_leaves_existing_table_untouched(ensure_pg, tmp_path):
    """GAP-001: sink.create_table() (DROP+CREATE under mode: overwrite) ran
    BEFORE the source was drained. F5 (EmptyDerivationError on a scanned
    PDF), a ross timeout, and a lazy CLIP model-load failure all raise
    mid-iteration -- so an upgrader whose corpus has one bad file lost their
    live index on the very run meant to refresh it. The fix materialises the
    overwrite cell's source into a list before create_table() runs, so a
    source failure aborts before the DDL and the existing table survives."""
    good_corpus = tmp_path / "good.json"
    good_corpus.write_text(json.dumps({
        "documents": [
            {"id": "d1", "content": "The Supreme Court decided Bostock.", "title": "Case A"},
            {"id": "d2", "content": "Apple v. Pepper is an antitrust case.", "title": "Case B"},
        ]
    }))
    cell_yaml = tmp_path / "cell.yaml"
    cell_yaml.write_text(textwrap.dedent(f"""
        cell_name: runner_overwrite_fail
        source:
          type: json_corpus
          path: {good_corpus}
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
          table: runner_overwrite_fail
          mode: overwrite
          hnsw: false
    """))
    result = run_cell(load_config(cell_yaml))
    assert result.error is None

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunkshop_runner_test.runner_overwrite_fail")
        assert cur.fetchone()[0] >= 2

    # A second overwrite run against the SAME table, whose source raises
    # mid-iteration (row 2 of 3 is missing its "content" key) -- standing
    # in for F5 / a ross timeout / a lazy CLIP model-load failure, all of
    # which now raise while the source is being drained rather than at
    # config load.
    bad_corpus = tmp_path / "bad.json"
    bad_corpus.write_text(json.dumps({
        "documents": [
            {"id": "e1", "content": "fine", "title": "Case C"},
            {"id": "e2", "title": "Case D (missing content field)"},
            {"id": "e3", "content": "never reached", "title": "Case E"},
        ]
    }))
    cell2_yaml = tmp_path / "cell2.yaml"
    cell2_yaml.write_text(textwrap.dedent(f"""
        cell_name: runner_overwrite_fail
        source:
          type: json_corpus
          path: {bad_corpus}
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
          table: runner_overwrite_fail
          mode: overwrite
          hnsw: false
    """))
    result2 = run_cell(load_config(cell2_yaml))
    assert result2.error is not None

    # The DDL must never have run: the first run's rows are still there.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM chunkshop_runner_test.runner_overwrite_fail")
        doc_ids = {r[0] for r in cur.fetchall()}
        assert doc_ids == {"d1", "d2"}


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
