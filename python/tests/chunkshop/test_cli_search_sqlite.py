"""``chunkshop search`` against a sqlite target — CLI-level backend dispatch.

Regression test for a gap found while verifying the SC-1 images-keyless
quickstart (Task 11): ``chunkshop search`` was hardcoded to the Postgres
backend (``chunkshop.search`` imports ``psycopg`` and expects a Postgres
DSN) regardless of ``target.type``, so every sqlite-backed cell failed with
a psycopg DSN-parse error. No external infra needed — sqlite is file-backed.
"""
from __future__ import annotations

import json
import os

import pytest
import yaml
from click.testing import CliRunner

pytest.importorskip("sqlite_vec")

from chunkshop.cli import cli
from chunkshop.config import CellConfig
from chunkshop.pipeline import Pipeline

TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

CORPUS = [
    ("doc_alpha", "test",
     "# Alpha topic\n\nAlpha is the first letter of the Greek alphabet. "
     "It is used as a symbol in mathematics, science, and engineering "
     "notation across many technical fields."),
    ("doc_beta", "test",
     "# Beta topic\n\nBeta is the second letter of the Greek alphabet. "
     "Beta testing follows alpha testing in software development."),
]


@pytest.fixture(scope="module")
def search_cell(tmp_path_factory):
    """Ingest a tiny corpus into a file-backed sqlite db, return a cell YAML path."""
    tmp_path = tmp_path_factory.mktemp("cli_search_sqlite")
    dsn_env = "SQLITE_CLI_SEARCH_TEST_PATH"
    db_path = tmp_path / "chunks.db"
    os.environ[dsn_env] = str(db_path)

    first = True
    for doc_id, tag, text in CORPUS:
        cfg = CellConfig(**{
            "cell_name": "cli_search_sqlite_test",
            "source": {"type": "inline"},
            "chunker": {"type": "hierarchy", "max_chars": 2000},
            "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
            "target": {
                "type": "sqlite",
                "dsn_env": dsn_env,
                "database": "ignored",
                "table": TABLE,
                "hnsw": False,
                "mode": "create_if_missing" if first else "append",
                "source_tag": tag,
            },
        })
        Pipeline(cfg).ingest_text(doc_id, text, metadata={"topic": tag})
        first = False

    cell_yaml = {
        "cell_name": "cli_search_sqlite_test",
        "source": {"type": "inline"},
        "chunker": {"type": "hierarchy", "max_chars": 2000},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "sqlite",
            "dsn": str(db_path),
            "database": "ignored",
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "test",
        },
    }
    cell_path = tmp_path / "search_cell.yaml"
    cell_path.write_text(yaml.dump(cell_yaml, sort_keys=False))
    return cell_path


def test_cli_search_sqlite_target_does_not_try_postgres(search_cell):
    """Before the fix this failed with a psycopg DSN-parse error on the raw file path."""
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5", "--include-facts", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["query"] == "alpha"
    assert "chunks" in data
    assert any(c["doc_id"] == "doc_alpha" for c in data["chunks"])


def test_cli_search_sqlite_without_include_facts_reports_the_known_gap(search_cell):
    """Default fact-row exclusion filter isn't supported by sqlite's `where` builder yet."""
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5", "--json",
    ])
    assert r.exit_code != 0
    assert "metadata_not" in r.output
