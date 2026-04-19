import json
import os
import textwrap
from pathlib import Path

import pytest
import psycopg

from chunkshop.orchestrator import orchestrate, OrchestrationResult


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
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS chunkshop_orch_test CASCADE")
            conn.commit()
    except Exception:
        pass


def _mini_yaml(tmp_path: Path, name: str, corpus: Path) -> Path:
    y = tmp_path / f"{name}.yaml"
    y.write_text(textwrap.dedent(f"""
        cell_name: {name}
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
          dsn_env: CHUNKSHOP_TEST_DSN
          schema: chunkshop_orch_test
          table: {name}
          overwrite: true
          hnsw: false
        runtime:
          doc_limit: 1
          log_path: {tmp_path / f"{name}.log"}
    """))
    return y


def test_orchestrate_two_cells_smoke(ensure_pg, tmp_path):
    corpus = tmp_path / "c.json"
    corpus.write_text(json.dumps({
        "documents": [{"id": "d1", "content": "x y z", "title": ""}]
    }))
    y1 = _mini_yaml(tmp_path, "cell_one", corpus)
    y2 = _mini_yaml(tmp_path, "cell_two", corpus)

    result: OrchestrationResult = orchestrate(
        configs=[y1, y2],
        concurrency=2,
        checkpoint_seconds=[2, 5],
        overall_timeout_seconds=120,
    )
    assert result.total == 2
    assert result.succeeded == 2, f"failed cells: {[c for c in result.cells if c['rc'] != 0]}"
    assert result.failed == 0


def test_orchestrate_reports_failure_without_raising(ensure_pg, tmp_path):
    # Use a nonexistent corpus path so the cell fails fast inside the subprocess
    y = tmp_path / "bad.yaml"
    y.write_text(textwrap.dedent(f"""
        cell_name: bad_cell
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
          dsn_env: CHUNKSHOP_TEST_DSN
          schema: chunkshop_orch_test
          table: bad_cell
          overwrite: true
          hnsw: false
    """))
    result = orchestrate(
        configs=[y],
        concurrency=1,
        checkpoint_seconds=[5],
        overall_timeout_seconds=60,
    )
    assert result.total == 1
    assert result.succeeded == 0
    assert result.failed == 1
    # Cell metadata should record the nonzero rc
    assert result.cells[0]["rc"] != 0
