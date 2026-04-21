"""End-to-end test: ingest all four docs/samples/*.md via run_cell, then run a
real semantic query and assert top-1 hit.

Validates the full pipeline (files source -> hierarchy chunker -> fastembed
int8 -> rake_keywords extractor -> pgvector sink) against a realistic corpus,
not synthesized fixtures. Skips cleanly if Postgres is unreachable. First run
downloads ~35 MB of model weights to ~/.cache/fastembed/.
"""
import os
import pytest
import psycopg

from chunkshop.config import CellConfig
from chunkshop.runner import run_cell


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES_GLOB = os.path.join(os.path.dirname(REPO_ROOT), "docs", "samples", "*.md")


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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_e2e_samples CASCADE")
        conn.commit()


def test_ingest_samples_corpus_end_to_end(ensure_pg):
    import glob
    md_files = sorted(glob.glob(SAMPLES_GLOB))
    assert len(md_files) >= 4, f"expected >=4 sample markdown docs, found {md_files}"

    cfg = CellConfig(
        cell_name="e2e_samples",
        source={"type": "files", "glob": SAMPLES_GLOB, "id_from": "stem"},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        target={
            "dsn_env": DSN_ENV,
            "schema": "chunkshop_e2e_samples",
            "table": "handbook",
            "mode": "create_if_missing",
            "source_tag": "samples_e2e",
            "promote_metadata": [{"path": "strategy", "type": "text"}],
            "hnsw": False,
        },
    )
    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.docs_processed >= 4
    assert result.chunks_written >= 4

    # Semantic query: "how do we handle customer credentials?" should surface
    # the handbook-security "Secrets management" section.
    from fastembed import TextEmbedding
    import chunkshop.embedders  # registers int8 variant
    emb = TextEmbedding(model_name="Xenova/bge-small-en-v1.5-int8", threads=2)
    qvec = list(emb.embed(["how do we handle customer credentials"]))[0]
    qlit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT doc_id, source, strategy FROM chunkshop_e2e_samples.handbook "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            (qlit,),
        )
        row = cur.fetchone()
        assert row is not None
        top_doc, top_source, top_strategy = row
        assert top_doc == "handbook-security", f"expected top-1 'handbook-security', got {top_doc!r}"
        assert top_source == "samples_e2e"
        assert top_strategy == "hierarchy"  # promoted metadata populated
