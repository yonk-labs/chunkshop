"""Sanity test for A/B-gate emission shapes (SC-004 of the mission brief).

Runs ``bakeoff-ntsb-ab.yaml`` end-to-end against the test DSN, asserts that
fact rows + cooccur edges land in pgvector with the shape documented in
``docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md``,
and dumps a JSON summary under ``skill-output/ab-gate/`` so pg-raggraph
day-one is "run the experiment," not "debug chunkshop output."

Why ntsb (not scotus): both A/B-ready configs share the identical chunker /
extractor / consolidator pipeline. NTSB is 20 docs (~13s to ingest) vs scotus
772 docs (~5min). Shape equivalence between the two was verified in Task 9;
this sanity test is about contract conformance, not throughput.

Skips cleanly when ``CHUNKSHOP_TEST_DSN`` is unset. The module-scoped fixture
amortizes the ingest across all three test functions and drops the table on
teardown.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

DSN_ENV = "CHUNKSHOP_TEST_DSN"
CHUNKSHOP_TEST_DSN = os.environ.get(DSN_ENV)

# python/tests/chunkshop/test_ab_gate_emission.py -> repo root is 3 parents up
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_REL_PATH = "docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml"

# YAML writes to schema=chunkshop_test (from `database:` field), table=ntsb_ab.
# `database` on the Postgres backend = CREATE SCHEMA IF NOT EXISTS
# (see backends/postgres.py::create_database_sql). The DSN itself connects to
# Postgres db `chunkshop_test`; the schema named `chunkshop_test` lives inside it.
TARGET_SCHEMA = "chunkshop_test"
TARGET_TABLE = "ntsb_ab"
FQ_TABLE = f'"{TARGET_SCHEMA}"."{TARGET_TABLE}"'

SUMMARY_DIR = REPO_ROOT / "skill-output" / "ab-gate"
SUMMARY_PATH = SUMMARY_DIR / "ntsb-emission-summary.json"

pytestmark = pytest.mark.skipif(
    not CHUNKSHOP_TEST_DSN,
    reason=f"{DSN_ENV} not set — A/B-gate sanity test requires test Postgres",
)


def _connect():
    """Import psycopg lazily so collection works without the dep installed."""
    import psycopg  # noqa: PLC0415

    return psycopg.connect(CHUNKSHOP_TEST_DSN)


@pytest.fixture(scope="module")
def ingested_table():
    """Run the ntsb-ab ingest once; drop the table on teardown.

    Uses a CLI subprocess so the test exercises the same code path a user
    would invoke. Slow path (~13-30s on a warm cache) is module-scoped so the
    three test functions amortize the ingest cost.
    """
    # Clean slate — drop any leftover table from a previous failed run.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {FQ_TABLE} CASCADE;")
        conn.commit()

    # Run the ingest from repo root so the YAML's relative corpus glob
    # (docs/samples/bakeoff-ntsb/corpus/*.md) resolves — files source globs
    # against cwd (sources/files.py:50). `--project python` points uv at the
    # installed chunkshop package; `--no-sync` preserves the already-synced
    # extras (lede + lede_spacy + extractors).
    result = subprocess.run(
        [
            "uv", "run", "--no-sync", "--project", "python",
            "chunkshop", "ingest",
            "--config", CONFIG_REL_PATH,
        ],
        cwd=str(REPO_ROOT),
        env={**os.environ, DSN_ENV: CHUNKSHOP_TEST_DSN},
        capture_output=True,
        text=True,
        timeout=600,  # NTSB is normally <30s; allow headroom for cold venvs / first-run model download
    )
    if result.returncode != 0:
        pytest.fail(
            f"chunkshop ingest failed (rc={result.returncode}):\n"
            f"STDOUT (last 2k):\n{result.stdout[-2000:]}\n"
            f"STDERR (last 2k):\n{result.stderr[-2000:]}"
        )

    yield FQ_TABLE

    # Teardown — drop the table so successive runs start clean.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {FQ_TABLE} CASCADE;")
        conn.commit()


def test_fact_rows_present_and_well_shaped(ingested_table):
    """Contract §1.2: every kind='fact' row carries the 10 required keys.

    Required: kind, subject, predicate, object, support_span, confidence,
    truncated, source_chunk_seq, consolidator, extractor.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata FROM {ingested_table} "
                f"WHERE metadata->>'kind' = 'fact' LIMIT 5;"
            )
            rows = cur.fetchall()

    assert rows, "no kind='fact' rows emitted — consolidation pipeline broken"

    required = (
        "kind",
        "subject",
        "predicate",
        "object",
        "support_span",
        "confidence",
        "truncated",
        "source_chunk_seq",
        "consolidator",
        "extractor",
    )
    for (sample,) in rows:
        for key in required:
            assert key in sample, (
                f"contract §1.2: fact row missing required key {key!r} "
                f"(sample keys: {sorted(sample.keys())})"
            )
        assert sample["kind"] == "fact"
        assert isinstance(sample["support_span"], str), (
            f"contract §1.2 requires support_span: str, "
            f"got {type(sample['support_span']).__name__}"
        )
        assert isinstance(sample["source_chunk_seq"], int), (
            f"contract §1.2 requires source_chunk_seq: int, "
            f"got {type(sample['source_chunk_seq']).__name__}"
        )
        assert isinstance(sample["confidence"], (int, float)), (
            f"contract §1.2 requires confidence: number, "
            f"got {type(sample['confidence']).__name__}"
        )
        assert isinstance(sample["truncated"], bool), (
            f"contract §1.2 requires truncated: bool, "
            f"got {type(sample['truncated']).__name__}"
        )


def test_cooccur_edges_well_shaped(ingested_table):
    """Contract §2.2 + §2.3: every cooccur edge has {a, b, weight} with a<b."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->'cooccur' FROM {ingested_table} "
                f"WHERE metadata ? 'cooccur' "
                f"  AND jsonb_typeof(metadata->'cooccur') = 'array' "
                f"  AND jsonb_array_length(metadata->'cooccur') > 0 "
                f"LIMIT 10;"
            )
            rows = cur.fetchall()

    assert rows, (
        "no chunks with non-empty cooccur edges — extractor not wired or "
        "contract §2.1 'cooccur on every chunk' invariant broken"
    )

    for (edges,) in rows:
        assert isinstance(edges, list), (
            f"contract §2.2: cooccur must be a JSON array, got {type(edges).__name__}"
        )
        for edge in edges:
            assert {"a", "b", "weight"} <= set(edge.keys()), (
                f"contract §2.2: cooccur edge missing required fields, got {edge!r}"
            )
            assert isinstance(edge["a"], str)
            assert isinstance(edge["b"], str)
            assert isinstance(edge["weight"], int), (
                f"contract §2.2: weight must be int, got {type(edge['weight']).__name__}"
            )
            assert edge["a"] < edge["b"], (
                f"contract §2.3 invariant violated (a<b lexicographic): {edge!r}"
            )


def test_dump_summary_for_pg_raggraph_handoff(ingested_table):
    """Write a JSON summary pg-raggraph can sanity-check before A/B day.

    Lands at skill-output/ab-gate/ntsb-emission-summary.json. This is the
    primary deliverable of SC-004: shape evidence pg-raggraph reviews before
    wiring its resolve_entity + graph-leg reader.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT "
                f"  COUNT(*) FILTER (WHERE metadata->>'kind' = 'fact') AS facts, "
                f"  COUNT(*) FILTER (WHERE metadata->>'kind' IS DISTINCT FROM 'fact') AS episodes, "
                f"  COUNT(*) FILTER (WHERE metadata ? 'cooccur') AS rows_with_cooccur, "
                f"  COUNT(*) AS total "
                f"FROM {ingested_table};"
            )
            facts, episodes, rows_with_cooccur, total = cur.fetchone()

            cur.execute(
                f"SELECT metadata FROM {ingested_table} "
                f"WHERE metadata->>'kind' = 'fact' LIMIT 1;"
            )
            sample_fact = cur.fetchone()[0]

            cur.execute(
                f"SELECT metadata->'cooccur'->0 FROM {ingested_table} "
                f"WHERE metadata ? 'cooccur' "
                f"  AND jsonb_typeof(metadata->'cooccur') = 'array' "
                f"  AND jsonb_array_length(metadata->'cooccur') > 0 "
                f"LIMIT 1;"
            )
            sample_edge = cur.fetchone()[0]

    summary = {
        "corpus": "ntsb",
        "config": CONFIG_REL_PATH,
        "table": ingested_table,
        "counts": {
            "facts": facts,
            "episodes": episodes,
            "rows_with_cooccur": rows_with_cooccur,
            "total": total,
        },
        "sample_fact": sample_fact,
        "sample_edge": sample_edge,
        "contract_ref": (
            "docs/superpowers/specs/"
            "2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md"
        ),
    }
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str))

    assert facts > 0, "no fact rows — consolidator not emitting"
    assert episodes > 0, "no episode rows — base chunker not emitting"
    assert rows_with_cooccur > 0, "no chunks carry cooccur — extractor not wired"
    assert SUMMARY_PATH.exists()
