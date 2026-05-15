"""End-to-end bakeoff test (SC-013): real Postgres, 2x2 matrix, samples corpus.

Mirrors the skip-if-PG-unreachable pattern from `test_end_to_end_samples_corpus.py`.
First run downloads ~85 MB of model weights (int8 bge-small + bge-base) to
`~/.cache/fastembed/`. Asserts the leaderboard has 4 combos with populated
recall+MRR and that `recommended.yaml` round-trips through `CellConfig`.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
import yaml

from chunkshop.bakeoff.config import (
    BakeoffConfig,
    GoldQuery,
    MatrixConfig,
    PostgresBakeoffTarget,
)
from chunkshop.bakeoff.output import (
    write_recommended_yaml,
    write_report_md,
    write_results_json,
)
from chunkshop.bakeoff.runner import run_bakeoff
from chunkshop.config import (
    CellConfig,
    FastembedEmbedder,
    FilesSource,
    HierarchyChunker,
    IdentityFramerConfig,
    SentenceAwareChunker,
)


DSN_ENV = "CHUNKSHOP_BAKEOFF_E2E_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES_GLOB = os.path.join(os.path.dirname(REPO_ROOT), "docs", "samples", "*-*.md")


@pytest.fixture
def ensure_pg():
    dsn = os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    # Drop the schema before AND after — the runner no longer cleans up.
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_bakeoff_e2e CASCADE")
    yield dsn
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_bakeoff_e2e CASCADE")


def test_bakeoff_e2e_2x2_samples(ensure_pg, tmp_path):
    cfg = BakeoffConfig(
        name="bakeoff_e2e",
        source=FilesSource(type="files", glob=SAMPLES_GLOB, id_from="stem"),
        framer=IdentityFramerConfig(),
        gold_queries=[
            GoldQuery(query="how do we rotate API keys", gold_doc_id="handbook-security"),
            GoldQuery(query="who approves a pull request", gold_doc_id="handbook-engineering"),
            GoldQuery(query="what changed in the April release", gold_doc_id="release-notes"),
        ],
        matrix=MatrixConfig(
            embedders=[
                FastembedEmbedder(
                    type="fastembed",
                    model_name="Xenova/bge-small-en-v1.5-int8",
                    dim=384,
                    threads=2,
                ),
                FastembedEmbedder(
                    type="fastembed",
                    model_name="Xenova/bge-base-en-v1.5-int8",
                    dim=768,
                    threads=2,
                ),
            ],
            chunkers=[
                HierarchyChunker(type="hierarchy"),
                SentenceAwareChunker(type="sentence_aware"),
            ],
        ),
        targets=[PostgresBakeoffTarget(
            type="postgres",
            dsn_env=DSN_ENV,
            **{"database": "chunkshop_bakeoff_e2e"},
        )],
    )

    results = run_bakeoff(cfg)

    # Shape assertions: 2x2 = 4 combos, 3 gold queries per combo.
    assert results.n_combos == 4
    assert len(results.combos) == 4
    assert results.n_queries == 3

    for combo in results.combos:
        assert combo.ingest_chunks > 0, f"{combo.table} ingested 0 chunks"
        assert "recall_at_1" in combo.aggregate
        assert "recall_at_3" in combo.aggregate
        assert "recall_at_5" in combo.aggregate
        assert "mrr" in combo.aggregate
        assert len(combo.per_query) == 3

    # Outputs land and are valid.
    json_path = write_results_json(results, tmp_path)
    md_path = write_report_md(cfg, results, tmp_path)
    yaml_path = write_recommended_yaml(cfg, results, tmp_path)

    assert json_path.exists()
    assert "Cross-backend comparison" in md_path.read_text()
    assert "Statistical power" in md_path.read_text()

    # recommended.yaml round-trips through CellConfig.
    recommended = yaml.safe_load(yaml_path.read_text())
    recommended.pop("# NOTE", None)
    CellConfig.model_validate(recommended)
