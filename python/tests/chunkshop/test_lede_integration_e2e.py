"""E2E: SummaryEmbedChunker wrapping hierarchy + lede.summarize callable (SC-005).

Skips cleanly if lede isn't installed or Postgres is unreachable.
Also covers SC-006 (external mode with json_corpus) in a sibling test.
"""
import json
import os

import pytest

pytest.importorskip("lede")
import psycopg  # noqa: E402

from chunkshop.config import CellConfig  # noqa: E402
from chunkshop.runner import run_cell  # noqa: E402


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES_GLOB = os.path.join(os.path.dirname(REPO_ROOT), "docs", "samples", "*-*.md")


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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_lede_e2e CASCADE")
        conn.commit()


def test_summary_embed_with_lede_through_sink(ensure_pg):
    """End-to-end: files source → hierarchy base + lede callable → pgvector.

    Verifies SC-005 (callable mode with real lede) and confirms the summarizer
    metadata stamp survives through to the target table.
    """
    cfg = CellConfig(
        cell_name="lede_e2e",
        source={"type": "files", "glob": SAMPLES_GLOB, "id_from": "stem"},
        chunker={
            "type": "summary_embed",
            "base": {"type": "hierarchy"},
            "summarizer": {
                "mode": "callable",
                "module": "chunkshop.summarizers.lede",
                "function": "summarize",
                "kwargs": {"max_length": 300},
            },
        },
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-base-en-v1.5-int8",
            "dim": 768,
            "threads": 2,
        },
        target={
            "type": "postgres",
            "dsn_env": DSN_ENV,
            "database": "chunkshop_lede_e2e",
            "table": "summarized",
            "mode": "create_if_missing",
            "source_tag": "lede_test",
            "hnsw": False,
        },
    )
    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.chunks_written > 0

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT original_content, embedded_content, metadata->>'summarizer' "
            "FROM chunkshop_lede_e2e.summarized LIMIT 20"
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for orig, embedded, summarizer_mode in rows:
            assert summarizer_mode == "callable"
            # Summary should be no longer than original (extractive).
            assert len(embedded) <= len(orig)


def test_summary_embed_external_with_json_corpus(ensure_pg, tmp_path):
    """SC-006: external mode pulling from json_corpus source's `summary` field."""
    corpus = {
        "documents": [
            {
                "id": "doc-alpha",
                "content": "Alpha detailed content goes here. " * 10,
                "title": "Alpha",
                "summary": "ALPHA_PRE_COMPUTED_SUMMARY",
            },
            {
                "id": "doc-bravo",
                "content": "Bravo detailed content goes here. " * 10,
                "title": "Bravo",
                "summary": "BRAVO_PRE_COMPUTED_SUMMARY",
            },
        ]
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus))

    cfg = CellConfig(
        cell_name="external_e2e",
        source={
            "type": "json_corpus",
            "path": str(corpus_path),
        },
        chunker={
            "type": "summary_embed",
            "base": {"type": "sentence_aware", "min_chars": 50, "max_chars": 400},
            "summarizer": {"mode": "external", "field": "summary"},
        },
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-base-en-v1.5-int8",
            "dim": 768,
            "threads": 2,
        },
        target={
            "type": "postgres",
            "dsn_env": DSN_ENV,
            "database": "chunkshop_lede_e2e",
            "table": "external_sum",
            "mode": "create_if_missing",
            "source_tag": "external_test",
            "hnsw": False,
        },
    )
    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.chunks_written > 0

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT doc_id, embedded_content, metadata->>'summarizer' "
            "FROM chunkshop_lede_e2e.external_sum"
        )
        rows = cur.fetchall()
        by_doc = {r[0]: (r[1], r[2]) for r in rows}
        assert by_doc["doc-alpha"][0] == "ALPHA_PRE_COMPUTED_SUMMARY"
        assert by_doc["doc-bravo"][0] == "BRAVO_PRE_COMPUTED_SUMMARY"
        for _, summarizer in by_doc.values():
            assert summarizer == "external"
