"""Phase C: fact filtering (--include-facts) + the fact-search command.

Unit-level tests assert the CLI surface (flags, command registration). Two
Postgres integration tests (guarded on $CHUNKSHOP_TEST_DSN) ingest a tiny memory
cell whose `lede` consolidator emits one episode + one fact, then exercise both
`fact-search` (breadcrumb) and `search` (facts excluded by default).
"""
import os
import json

import pytest


# --- C2: --include-facts flag ---------------------------------------------

def test_search_has_include_facts_flag():
    from chunkshop.cli import search as search_cmd
    names = {p.name for p in search_cmd.params}
    assert "include_facts" in names


# --- C3: fact-search command registration ---------------------------------

def test_fact_search_command_registered():
    from chunkshop.cli import cli
    assert "fact-search" in cli.commands


DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
SCHEMA = "chunkshop_factsearch_test"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


def _write_memory_cell_yaml(tmp_path):
    """Write a cell config: inline source + consolidation chunker (lede mode) +
    a plain pgvector target. The `lede` consolidator emits one episode chunk
    (kind='episode') and one fact chunk (kind='fact', source_chunk_seq=0) into
    the SAME table — exactly the layout fact-search queries.
    """
    import yaml as _yaml

    cfg = {
        "cell_name": "factsearch_test",
        "source": {"type": "inline"},
        "chunker": {
            "type": "consolidation",
            "base": {"type": "sentence_aware", "doc_type": "prose"},
            "consolidator": {"mode": "lede"},
        },
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "postgres",
            "dsn_env": "CHUNKSHOP_TEST_DSN",
            "database": SCHEMA,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "factsrc",
        },
    }
    path = tmp_path / "cell.yaml"
    path.write_text(_yaml.safe_dump(cfg))
    return path


def _ingest_one_fact_doc(cfg_path):
    """Ingest a tiny doc whose salient sentence contains the query word 'alpha'.

    Returns the doc_id. The lede consolidator extracts a fact whose support_span
    (the fact chunk's original_content) contains 'Alpha', so a query for 'alpha'
    matches it.
    """
    from chunkshop.config import CellConfig
    from chunkshop.pipeline import Pipeline
    import yaml as _yaml

    cfg = CellConfig(**_yaml.safe_load(cfg_path.read_text()))
    pipe = Pipeline(cfg)
    doc_id = "doc_alpha"
    text = (
        "Alpha is a project. Alpha ships embeddings to pgvector for retrieval. "
        "Beta is an unrelated thing."
    )
    pipe.ingest_text(doc_id, text, metadata={"topic": "alpha"})
    return doc_id


def _drop_schema():
    import psycopg
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


@pytest.fixture
def fact_corpus(tmp_path):
    import psycopg
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:  # pragma: no cover - skip path
        pytest.skip(f"PG at {DSN} not reachable: {exc}")
    _drop_schema()
    cfg_path = _write_memory_cell_yaml(tmp_path)
    doc_id = _ingest_one_fact_doc(cfg_path)
    try:
        yield cfg_path, doc_id
    finally:
        _drop_schema()


@pytest.mark.skipif(not DSN, reason="CHUNKSHOP_TEST_DSN not set")
def test_fact_search_returns_breadcrumb(fact_corpus):
    from click.testing import CliRunner
    from chunkshop.cli import cli

    cfg_path, doc_id = fact_corpus
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fact-search", "--config", str(cfg_path), "--query", "alpha", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload, "expected at least one fact entry"
    first = payload[0]
    assert {"fact", "chunk", "doc_id"} <= set(first)
    assert first["chunk"] is not None
    assert first["chunk"]["doc_id"] == doc_id


@pytest.mark.skipif(not DSN, reason="CHUNKSHOP_TEST_DSN not set")
def test_normal_search_excludes_facts_by_default(fact_corpus):
    from click.testing import CliRunner
    from chunkshop.cli import cli

    cfg_path, _doc_id = fact_corpus
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["search", "--config", str(cfg_path), "--query", "alpha", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    for row in payload["chunks"]:
        assert (row.get("metadata") or {}).get("kind") != "fact"
