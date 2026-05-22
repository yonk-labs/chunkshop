"""SC-004: ingest without target.fts is unchanged — no FTS column/index."""
import importlib.util
import os

import pytest

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)


def _pg_up() -> bool:
    if importlib.util.find_spec("psycopg") is None:
        return False
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg test DB unreachable")


def test_no_fts_ingest_has_no_search_vector(tmp_path):
    import psycopg

    from chunkshop.config import CellConfig
    from chunkshop.runner import run_cell

    # Write a tiny corpus file so FilesSource has something to iterate.
    doc_file = tmp_path / "hello-world.md"
    doc_file.write_text("# Hello\n\nThis is a short test document for the FTS noop regression.")

    schema = "chunkshop_fts_noop"
    cfg = CellConfig.model_validate(
        {
            "cell_name": "fts_noop",
            "source": {
                "type": "files",
                "glob": str(tmp_path / "*.md"),
                "id_from": "stem",
            },
            "chunker": {"type": "sentence_aware"},
            "embedder": {
                "type": "fastembed",
                "model_name": "BAAI/bge-small-en-v1.5",
                "dim": 384,
            },
            "target": {
                "type": "postgres",
                "dsn": DSN,
                "database": schema,
                "table": "chunks",
                "mode": "overwrite",
                "hnsw": False,
            },
        }
    )

    try:
        run_cell(cfg)

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            # Assert no search_vector column was created.
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'chunks'",
                (schema,),
            )
            cols = {r[0] for r in cur.fetchall()}
            assert "search_vector" not in cols, (
                f"search_vector column was created without target.fts — "
                f"backward compat broken. Columns present: {cols}"
            )

            # Assert no FTS index was created.
            cur.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = %s AND tablename = 'chunks'",
                (schema,),
            )
            idxs = {r[0] for r in cur.fetchall()}
            assert not any(i.endswith("_fts_idx") for i in idxs), (
                f"FTS index created without target.fts — "
                f"backward compat broken. Indexes present: {idxs}"
            )
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
