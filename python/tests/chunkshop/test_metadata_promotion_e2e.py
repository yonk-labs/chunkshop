"""End-to-end: composite(spacy + lang_detect) populates promoted columns.

DC-005 gate. Wires the full pipeline: source -> chunker -> embedder ->
composite(spacy_entities, lang_detect) -> sink with ``promote_metadata``.

Asserts:
- ``language`` text column populated with ``'en'``.
- ``entities__org`` text[] column contains Apple and Microsoft across rows.

Skips when:
- Postgres at ``CHUNKSHOP_TEST_DSN`` (or the default 5434 DSN) is unreachable.
- ``spacy`` or ``langdetect`` extras missing.
"""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("spacy")
pytest.importorskip("langdetect")
import psycopg  # noqa: E402

from chunkshop.config import CellConfig  # noqa: E402
from chunkshop.runner import run_cell  # noqa: E402


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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_meta_e2e CASCADE")
        conn.commit()


def test_composite_extractor_feeds_promoted_columns(ensure_pg, tmp_path):
    dsn = ensure_pg
    corpus_path = tmp_path / "news.json"
    # Bodies padded so HierarchyChunker (default min_section_chars=100) emits rows.
    apple_body = (
        "Apple Inc. reported record earnings this quarter. "
        "Tim Cook spoke at the event in Cupertino about product strategy. "
        "Analysts highlighted services revenue growth and installed-base expansion."
    )
    msft_body = (
        "Microsoft announced a new partnership with OpenAI in Redmond. "
        "Satya Nadella described the deal as strategic for Azure customers "
        "and laid out an agenda for enterprise AI adoption over the next year."
    )
    corpus_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "id": "a1",
                        "title": "Apple News",
                        "content": f"# Apple quarter\n\n{apple_body}",
                    },
                    {
                        "id": "a2",
                        "title": "Microsoft News",
                        "content": f"# Microsoft deal\n\n{msft_body}",
                    },
                ]
            }
        )
    )

    cfg = CellConfig(
        cell_name="meta_e2e",
        source={"type": "json_corpus", "path": str(corpus_path)},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        extractor={
            "type": "composite",
            "extractors": [
                {
                    "type": "spacy_entities",
                    "label_whitelist": ["ORG", "PERSON", "GPE"],
                },
                {"type": "lang_detect"},
            ],
        },
        target={
            "dsn_env": DSN_ENV,
            "schema": "chunkshop_meta_e2e",
            "table": "articles",
            "mode": "create_if_missing",
            "source_tag": "news_meta",
            "promote_metadata": [
                {"path": "language", "type": "text"},
                {"path": "entities.ORG", "type": "text[]"},
            ],
            "hnsw": False,
        },
    )

    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.chunks_written >= 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT language, "entities__org" FROM chunkshop_meta_e2e.articles')
        rows = cur.fetchall()
        assert rows, "expected at least one row in the promoted table"

        # Every row should have been detected as English.
        assert all(lang == "en" for lang, _ in rows), [r[0] for r in rows]

        # Across rows, ORG entities should include Apple and Microsoft.
        orgs_flat = [org for _, orgs in rows if orgs for org in orgs]
        assert any("Apple" in o for o in orgs_flat), orgs_flat
        assert any("Microsoft" in o for o in orgs_flat), orgs_flat
