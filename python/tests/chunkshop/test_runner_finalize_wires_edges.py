"""SP-E Part 2: runner integration — extractor.finalize() wires into the DB.

When a cell's extractor exposes a ``finalize()`` method (today: only the
``code_relationships`` extractor), the runner must:

  1. Call ``extractor.finalize(project_id=cfg.cell_name)`` after the per-doc loop.
  2. Materialize the returned edges into ``<schema>.code_edges`` via
     ``write_edges_schema`` + ``write_edges`` from
     ``chunkshop.extractors.code_relationships``.
  3. Leave extractors that DON'T expose ``finalize()`` untouched (back-compat).

This test exercises a real ingest into Postgres through ``run_cell`` and
asserts the side-table is populated.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)
DSN_ENV = "CHUNKSHOP_TEST_DSN"


def _pg_up() -> bool:
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")


_FILE_A = (
    "def define_unique_xyz(arg):\n"
    "    return arg + 1\n"
)
_FILE_B = (
    "def caller_abc():\n"
    "    return define_unique_xyz(42)\n"
)


def test_runner_writes_edges_for_code_relationships() -> None:
    """run_cell with code_relationships extractor populates code_edges in same schema."""
    import psycopg

    from chunkshop.config import CellConfig
    from chunkshop.runner import run_cell

    schema = f"chunkshop_runfinalize_{uuid.uuid4().hex[:8]}"
    os.environ[DSN_ENV] = DSN

    with tempfile.TemporaryDirectory() as tmpdir:
        # Lay out a tiny "repo" the files source can glob.
        root = Path(tmpdir)
        (root / "a.py").write_text(_FILE_A)
        (root / "b.py").write_text(_FILE_B)

        cfg = CellConfig(
            **{
                "cell_name": "runner_finalize_test",
                "source": {
                    "type": "files",
                    "glob": str(root / "*.py"),
                    "id_from": "stem",
                },
                "chunker": {"type": "symbol_aware"},
                "embedder": {
                    "type": "fastembed",
                    "model_name": "BAAI/bge-small-en-v1.5",
                    "dim": 384,
                },
                "extractor": {
                    "type": "code_relationships",
                    "target_schema": schema,
                },
                "target": {
                    "type": "postgres",
                    "dsn_env": DSN_ENV,
                    "database": schema,
                    "table": "chunks",
                    "hnsw": False,
                    "mode": "overwrite",
                    "source_tag": "runner_finalize",
                },
            }
        )

        try:
            result = run_cell(cfg)
            assert result.error is None, result.error
            assert result.chunks_written > 0

            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                # The code_edges table should exist + carry at least one row.
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = 'code_edges'",
                    (schema,),
                )
                assert cur.fetchone() is not None, "code_edges table not created"

                cur.execute(
                    f'SELECT edge_type, src_fqn, dst_fqn, project_id, confidence '
                    f'FROM "{schema}".code_edges'
                )
                rows = cur.fetchall()
                # The cross-file CALLS edge caller_abc -> define_unique_xyz
                # must appear.
                pairs = {(r[0], r[1].rsplit(".", 1)[-1], r[2].rsplit(".", 1)[-1]) for r in rows}
                assert ("CALLS", "caller_abc", "define_unique_xyz") in pairs
                # Project_id MUST be the cell_name so the impact_of CLI can scope.
                for r in rows:
                    assert r[3] == "runner_finalize_test", (
                        f"expected project_id=cell_name, got {r[3]!r}"
                    )
        finally:
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                conn.commit()


def test_runner_skips_finalize_for_extractor_without_method(tmp_path) -> None:
    """A non-finalize extractor (none/rake/etc) must NOT trigger edge writes."""
    import psycopg

    from chunkshop.config import CellConfig
    from chunkshop.runner import run_cell

    schema = f"chunkshop_runfinalize_skip_{uuid.uuid4().hex[:8]}"
    os.environ[DSN_ENV] = DSN

    # Two non-code docs; we use sentence_aware chunker so no codeparse path runs.
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "doc-a.md").write_text("# Doc A\n\nHello world. This is doc A.")
    (docs_root / "doc-b.md").write_text("# Doc B\n\nAnother document. Goodbye world.")

    cfg = CellConfig(
        **{
            "cell_name": "runner_no_finalize",
            "source": {
                "type": "files",
                "glob": str(docs_root / "*.md"),
                "id_from": "stem",
            },
            "chunker": {"type": "sentence_aware", "max_chars": 2000},
            "embedder": {
                "type": "fastembed",
                "model_name": "BAAI/bge-small-en-v1.5",
                "dim": 384,
            },
            # NoneExtractor doesn't have finalize -> runner skips edge write.
            "extractor": {"type": "none"},
            "target": {
                "type": "postgres",
                "dsn_env": DSN_ENV,
                "database": schema,
                "table": "chunks",
                "hnsw": False,
                "mode": "overwrite",
                "source_tag": "no_finalize",
            },
        }
    )

    try:
        result = run_cell(cfg)
        assert result.error is None, result.error
        # No code_edges table should have been created.
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = 'code_edges'",
                (schema,),
            )
            assert cur.fetchone() is None, (
                "code_edges table should NOT exist for non-finalize extractor"
            )
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
