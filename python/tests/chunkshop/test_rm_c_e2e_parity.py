"""End-to-end cross-port parity test (SC-004 of the RM-C mission brief).

Ingests the same fixture corpus via chunkshop-py and chunkshop-rs into
separate pgvector tables in ``chunkshop_test``. Joins on ``node_id``;
asserts row count + per-row metadata tuple equality (excluding the
embedding vector, which is bit-exact but not what this test gates).

The two YAMLs (docs/samples/rm-c-parity/rm-c-parity-{py,rs}.yaml) point
at the same fixture glob `python/tests/fixtures/rm-c-parity/*` — a
relative path resolved against the cwd of the ingest process. Both
ingests therefore run from the repo root so the glob resolves
identically.

Skips cleanly when:

* ``CHUNKSHOP_TEST_DSN`` is unset.
* The Rust ``chunkshop-rs`` binary isn't built with the code-aware
  features (build with
  ``cargo build --release --features "code-aware-python code-aware-java"``).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CHUNKSHOP_TEST_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
# tests/chunkshop/test_rm_c_e2e_parity.py → repo root is parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
RUST_BINARY = REPO_ROOT / "rust" / "target" / "release" / "chunkshop-rs"

PY_CONFIG = REPO_ROOT / "docs" / "samples" / "rm-c-parity" / "rm-c-parity-py.yaml"
RS_CONFIG = REPO_ROOT / "docs" / "samples" / "rm-c-parity" / "rm-c-parity-rs.yaml"

# Tables created by the YAMLs. Schema is the DSN's database name
# (Postgres backend writes into schema = database for chunkshop_test).
PY_TABLE = "chunkshop_test.rm_c_py"
RS_TABLE = "chunkshop_test.rm_c_rs"

pytestmark = [
    pytest.mark.skipif(
        not CHUNKSHOP_TEST_DSN,
        reason="CHUNKSHOP_TEST_DSN not set",
    ),
    pytest.mark.skipif(
        not RUST_BINARY.exists(),
        reason=(
            "Rust chunkshop-rs binary not built with code-aware features; run "
            "`cd rust && cargo build --release --features "
            "\"code-aware-python code-aware-java\"`"
        ),
    ),
]


def _connect():
    import psycopg

    return psycopg.connect(CHUNKSHOP_TEST_DSN)


def _drop_tables() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {PY_TABLE} CASCADE;")
            cur.execute(f"DROP TABLE IF EXISTS {RS_TABLE} CASCADE;")
        conn.commit()


@pytest.fixture(scope="module")
def ingested_tables():
    """Run Python + Rust ingests once for the module; drop tables on teardown."""
    _drop_tables()

    env = {**os.environ, "CHUNKSHOP_TEST_DSN": CHUNKSHOP_TEST_DSN}

    # Python ingest — run from REPO_ROOT so the YAML's relative
    # `python/tests/fixtures/rm-c-parity/*` glob resolves the same way the
    # Rust ingest's does.
    py_result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT / "python"),
            "--no-sync",
            "chunkshop",
            "ingest",
            "--config",
            str(PY_CONFIG),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if py_result.returncode != 0:
        pytest.fail(
            "Python ingest failed (exit "
            f"{py_result.returncode}):\nstdout:\n{py_result.stdout[-1500:]}\n"
            f"stderr:\n{py_result.stderr[-1500:]}"
        )

    # Rust ingest — also from REPO_ROOT for the same glob-resolution reason.
    rs_result = subprocess.run(
        [str(RUST_BINARY), "ingest", "--config", str(RS_CONFIG)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if rs_result.returncode != 0:
        pytest.fail(
            "Rust ingest failed (exit "
            f"{rs_result.returncode}):\nstdout:\n{rs_result.stdout[-1500:]}\n"
            f"stderr:\n{rs_result.stderr[-1500:]}"
        )

    yield (PY_TABLE, RS_TABLE)

    _drop_tables()


def test_row_count_parity(ingested_tables):
    """Same fixture corpus must produce the same chunk count in both ports."""
    py_table, rs_table = ingested_tables
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {py_table};")
            py_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {rs_table};")
            rs_count = cur.fetchone()[0]
    assert py_count == rs_count, (
        f"row count diverges: python={py_count} rust={rs_count}"
    )
    assert py_count > 0, "empty ingest — fixtures or glob misconfigured"


def test_node_id_set_parity(ingested_tables):
    """The set of node_id values must be identical between the two tables.

    node_id is the deterministic ``code_symbol_node_id`` hash of
    (project_id, language, file_path, fqn). Identity here proves
    SC-001 (build_fqn parity) + SC-002 (code_symbol_node_id parity) +
    SC-004 (per-language extractor symbol-set parity) compose
    correctly end-to-end.
    """
    py_table, rs_table = ingested_tables
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->>'node_id' FROM {py_table} "
                "WHERE metadata ? 'node_id';"
            )
            py_ids = {row[0] for row in cur.fetchall()}
            cur.execute(
                f"SELECT metadata->>'node_id' FROM {rs_table} "
                "WHERE metadata ? 'node_id';"
            )
            rs_ids = {row[0] for row in cur.fetchall()}

    only_py = py_ids - rs_ids
    only_rs = rs_ids - py_ids
    assert not only_py, f"node_ids only in Python: {sorted(only_py)}"
    assert not only_rs, f"node_ids only in Rust:   {sorted(only_rs)}"
    assert py_ids, "no node_id stamps found — symbol_aware chunker likely fell back"


def test_per_node_metadata_parity(ingested_tables):
    """Per node_id, the (fqn, language, symbol_name, parent_name, symbol_type)
    tuple must be identical across the two ports.

    Skips the line_start/line_end columns (Python emits start_line/end_line,
    Rust emits line_start/line_end — a key-naming divergence orthogonal
    to the SC-004 semantic parity this test gates).
    """
    py_table, rs_table = ingested_tables
    select_tuple = (
        "metadata->>'fqn', "
        "metadata->>'language', "
        "metadata->>'symbol_name', "
        "metadata->>'parent_name', "
        "metadata->>'symbol_type'"
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->>'node_id', {select_tuple} "
                f"FROM {py_table} WHERE metadata ? 'node_id' "
                "ORDER BY metadata->>'node_id';"
            )
            py_rows = {r[0]: tuple(r[1:]) for r in cur.fetchall()}
            cur.execute(
                f"SELECT metadata->>'node_id', {select_tuple} "
                f"FROM {rs_table} WHERE metadata ? 'node_id' "
                "ORDER BY metadata->>'node_id';"
            )
            rs_rows = {r[0]: tuple(r[1:]) for r in cur.fetchall()}

    divergent = []
    for node_id, py_tuple in py_rows.items():
        rs_tuple = rs_rows.get(node_id)
        if rs_tuple != py_tuple:
            divergent.append((node_id, py_tuple, rs_tuple))

    assert not divergent, (
        f"{len(divergent)} node(s) with divergent metadata:\n"
        + "\n".join(
            f"  {nid}: py={py!r} rs={rs!r}" for nid, py, rs in divergent[:5]
        )
    )
