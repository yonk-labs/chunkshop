"""SP-E Part 1: ``chunkshop impact-of`` subcommand.

Builds a synthetic ``code_edges`` table in a throwaway schema, populates a
small caller-graph, and exercises the CLI's depth=1, depth=N, and direction
modes (callers / callees / both) — both human-readable and ``--json`` output.

The cell YAML carries only the bits ``impact-of`` actually reads: the target
DSN + schema + table. Embedder, chunker, source — those are never touched by
this command.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from chunkshop.cli import cli

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


# Caller graph used by every test.
#
#   a.entry  -> b.middle  -> c.leaf
#                          \-> c.extra
#   x.alt    -> b.middle
#
# So:
#   callers of b.middle           = [a.entry, x.alt]            (depth=1)
#   callees of b.middle           = [c.leaf, c.extra]            (depth=1)
#   callers of c.leaf (depth=1)    = [b.middle]
#   callers of c.leaf (depth=2)    = [b.middle, a.entry, x.alt]
_EDGES = [
    # (edge_type, src_fqn, dst_fqn, confidence)
    ("CALLS", "a.entry", "b.middle", 0.9),
    ("CALLS", "x.alt", "b.middle", 0.9),
    ("CALLS", "b.middle", "c.leaf", 0.9),
    ("CALLS", "b.middle", "c.extra", 0.9),
    # An ambiguous edge (below the 0.7 default cutoff). impact-of's default
    # 0.7 floor MUST filter this out, so c.maybe should never appear in
    # high-confidence results.
    ("CALLS", "b.middle", "c.maybe", 0.5),
]


@pytest.fixture()
def impact_cell(tmp_path: Path):
    """Throwaway schema with a populated code_edges table + a CLI-loadable YAML."""
    import psycopg

    schema = f"chunkshop_impact_{uuid.uuid4().hex[:8]}"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.commit()

    # Use the production write_edges_schema so the indexes match what the
    # impact-of query actually depends on.
    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    project_id = "impact-test"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for et, src, dst, conf in _EDGES:
            src_id = f"node_{src}"
            dst_id = f"node_{dst}"
            cur.execute(
                f'INSERT INTO "{schema}".code_edges '
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, dst_node_id, confidence, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (project_id, et, src, dst, src_id, dst_id, conf, "{}"),
            )
        conn.commit()

    cell_yaml = {
        "cell_name": "impact_test",
        "source": {"type": "inline"},
        "chunker": {"type": "sentence_aware", "max_chars": 2000},
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
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "impact_test",
        },
    }
    cell_path = tmp_path / "cell.yaml"
    cell_path.write_text(yaml.dump(cell_yaml, sort_keys=False))

    yield cell_path, schema, project_id

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


def test_impact_callers_depth1_json(impact_cell):
    """callers of b.middle at depth=1 -> [a.entry, x.alt]."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "b.middle",
            "--direction",
            "callers",
            "--project-id",
            project_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["target"] == "b.middle"
    callers = {c["fqn"] for c in data.get("callers", [])}
    assert callers == {"a.entry", "x.alt"}
    # Each row has hop=1
    for c in data["callers"]:
        assert c["hop"] == 1


def test_impact_callees_depth1_json(impact_cell):
    """callees of b.middle at depth=1 (default 0.7 threshold) -> [c.leaf, c.extra]."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "b.middle",
            "--direction",
            "callees",
            "--project-id",
            project_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    callees = {c["fqn"] for c in data.get("callees", [])}
    assert callees == {"c.leaf", "c.extra"}
    # The 0.5-confidence ambiguous edge to c.maybe must NOT appear.
    assert "c.maybe" not in callees


def test_impact_both_directions(impact_cell):
    """direction=both emits both lists labeled."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "b.middle",
            "--direction",
            "both",
            "--project-id",
            project_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert "callers" in data and "callees" in data
    assert {c["fqn"] for c in data["callers"]} == {"a.entry", "x.alt"}
    assert {c["fqn"] for c in data["callees"]} == {"c.leaf", "c.extra"}


def test_impact_depth2_callers_transitive(impact_cell):
    """callers of c.leaf at depth=2 follows the chain via b.middle."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "c.leaf",
            "--direction",
            "callers",
            "--depth",
            "2",
            "--project-id",
            project_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    fqns = {c["fqn"] for c in data["callers"]}
    assert fqns == {"b.middle", "a.entry", "x.alt"}
    by_fqn = {c["fqn"]: c for c in data["callers"]}
    assert by_fqn["b.middle"]["hop"] == 1
    assert by_fqn["a.entry"]["hop"] == 2
    assert by_fqn["x.alt"]["hop"] == 2


def test_impact_depth_cap(impact_cell):
    """depth > 10 is rejected so a malformed CLI invocation can't runaway."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "c.leaf",
            "--depth",
            "11",
            "--project-id",
            project_id,
        ],
    )
    assert r.exit_code != 0
    assert "depth" in r.output.lower()


def test_impact_text_output_renders_tree(impact_cell):
    """Default (no --json) is a human-readable tree-style listing."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "b.middle",
            "--direction",
            "callers",
            "--project-id",
            project_id,
        ],
    )
    assert r.exit_code == 0, r.output
    # Header + at least one caller row.
    assert "b.middle" in r.output
    assert "a.entry" in r.output


def test_impact_unknown_fqn_empty_ok(impact_cell):
    """An FQN with no edges returns empty result, exits 0."""
    cell_path, _schema, project_id = impact_cell
    r = CliRunner().invoke(
        cli,
        [
            "impact-of",
            "--config",
            str(cell_path),
            "--fqn",
            "nonexistent.symbol",
            "--direction",
            "both",
            "--project-id",
            project_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["callers"] == []
    assert data["callees"] == []
