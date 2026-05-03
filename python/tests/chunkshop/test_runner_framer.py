"""Runner still processes docs correctly when framer=identity (default)."""
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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_framer CASCADE")
        conn.commit()


def test_framer_metadata_reaches_chunks(ensure_pg, tmp_path):
    """Regression: framer-produced metadata.framer + metadata.frame_seq must
    land on every chunk row, not get clobbered by the chunker's fresh metadata
    dict. Exercises the doc.metadata -> extractor -> chunker-wins merge order
    in runner.run_cell."""
    corpus = tmp_path / "corpus.md"
    corpus.write_text(
        "# Section Alpha\n\nAlpha body text that is unambiguously longer than "
        "one hundred characters so the min_section_chars filter leaves it intact.\n\n"
        "# Section Beta\n\nBeta body text that is unambiguously longer than "
        "one hundred characters so the min_section_chars filter leaves it intact."
    )

    cfg = CellConfig(
        cell_name="framer_meta_check",
        source={"type": "files", "glob": str(corpus), "id_from": "stem"},
        framer={"type": "heading_boundary", "pattern": r"^#\s", "title_from_heading": True},
        chunker={"type": "hierarchy"},
        embedder={"type": "fastembed",
                  "model_name": "Xenova/bge-base-en-v1.5-int8",
                  "dim": 768, "threads": 2},
        target={"type": "postgres", "dsn_env": DSN_ENV, "database": "chunkshop_test_framer",
                "table": "framer_meta", "mode": "overwrite", "hnsw": False},
    )
    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.chunks_written >= 2

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT metadata FROM chunkshop_test_framer.framer_meta ORDER BY doc_id, seq_num"
        )
        rows = cur.fetchall()
        assert len(rows) >= 2
        for (meta,) in rows:
            assert meta.get("framer") == "heading_boundary", (
                f"framer key missing or wrong on chunk: {meta!r}"
            )
            assert "frame_seq" in meta, f"frame_seq missing on chunk: {meta!r}"
            # Chunker-wins: strategy from hierarchy chunker still present
            assert meta.get("strategy") == "hierarchy"


def test_identity_framer_default_preserves_existing_behavior(ensure_pg, tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({
        "documents": [
            {"id": "d1", "title": "T1",
             "content": "# Alpha\n\nAlpha bravo charlie delta echo foxtrot "
                        "golf hotel india juliet kilo lima mike november "
                        "oscar papa quebec romeo sierra tango uniform victor."},
        ]
    }))

    cfg = CellConfig(
        cell_name="framer_default",
        source={"type": "json_corpus", "path": str(corpus)},
        chunker={"type": "hierarchy"},
        embedder={"type": "fastembed",
                  "model_name": "Xenova/bge-base-en-v1.5-int8",
                  "dim": 768, "threads": 2},
        target={"type": "postgres", "dsn_env": DSN_ENV, "database": "chunkshop_test_framer",
                "table": "t", "mode": "overwrite", "hnsw": False},
    )

    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.docs_processed == 1
    assert result.chunks_written >= 1
