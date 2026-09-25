"""``chunkshop search`` against a sqlite target — CLI-level backend dispatch.

Two regressions live here.

1. ``chunkshop search`` was hardcoded to the Postgres backend
   (``chunkshop.search`` imports ``psycopg`` and expects a Postgres DSN)
   regardless of ``target.type``, so every sqlite-backed cell failed with a
   psycopg DSN-parse error.

2. The DEFAULT search path — no ``--include-facts`` — was broken for EVERY
   sqlite target: ``cli.py`` injects ``{"metadata_not": {"kind": "fact"}}``
   unless the caller opts in, and ``search_sqlite._build_where`` did not
   implement ``metadata_not``, so it raised
   ``unsupported where keys: ['metadata_not']``. Every test here previously
   passed ``--include-facts``, which is precisely why it shipped; the
   default path now has coverage of its own.

No external infra needed — sqlite is file-backed.
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

# (doc_id, source_tag, extra metadata, text). `doc_fact` carries
# ``kind='fact'`` — the row the default filter is supposed to hide, and
# without it "the default path works" would be untestable.
CORPUS = [
    ("doc_alpha", "test", {},
     "# Alpha topic\n\nAlpha is the first letter of the Greek alphabet. "
     "It is used as a symbol in mathematics, science, and engineering "
     "notation across many technical fields."),
    ("doc_beta", "test", {},
     "# Beta topic\n\nBeta is the second letter of the Greek alphabet. "
     "Beta testing follows alpha testing in software development."),
    ("doc_fact", "test", {"kind": "fact"},
     "# Fact\n\nAlpha is the first letter of the Greek alphabet and is "
     "widely used as a symbol in mathematics and engineering notation "
     "across many technical fields."),
]


@pytest.fixture(scope="module")
def search_cell(tmp_path_factory):
    """Ingest a tiny corpus into a file-backed sqlite db, return a cell YAML path."""
    tmp_path = tmp_path_factory.mktemp("cli_search_sqlite")
    dsn_env = "SQLITE_CLI_SEARCH_TEST_PATH"
    db_path = tmp_path / "chunks.db"
    os.environ[dsn_env] = str(db_path)

    first = True
    for doc_id, tag, extra_meta, text in CORPUS:
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
        Pipeline(cfg).ingest_text(doc_id, text, metadata={"topic": tag, **extra_meta})
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


def test_cli_search_sqlite_default_path_works_without_include_facts(search_cell):
    """The DEFAULT invocation — the one every user types first.

    ``cli.py`` injects ``metadata_not={"kind": "fact"}`` unless
    ``--include-facts`` is passed, and sqlite's `where` builder rejected the
    key outright, so plain ``chunkshop search`` failed against every sqlite
    target with ``unsupported where keys: ['metadata_not']``.
    """
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    doc_ids = [c["doc_id"] for c in data["chunks"]]
    # Rows that LACK the `kind` key are kept (IS NOT is NULL-safe, mirroring
    # Postgres' IS DISTINCT FROM) ...
    assert "doc_alpha" in doc_ids
    # ... and the fact row is excluded.
    assert "doc_fact" not in doc_ids


def test_cli_search_sqlite_include_facts_admits_the_fact_row(search_cell):
    """The opt-in flag must still widen the result set — proving the default
    above filters rather than merely returning nothing."""
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5", "--include-facts", "--json",
    ])
    assert r.exit_code == 0, r.output
    doc_ids = [c["doc_id"] for c in json.loads(r.output)["chunks"]]
    assert "doc_fact" in doc_ids


def test_sqlite_metadata_not_keeps_rows_missing_the_key():
    """Unit-level mirror of Postgres' `IS DISTINCT FROM` semantics.

    `<>` would drop every row that lacks the key; `IS NOT` keeps them, which
    is what makes the default fact filter a no-op for ordinary chunk rows.
    """
    from chunkshop.search_sqlite import _build_where

    sql, params = _build_where({"metadata_not": {"kind": "fact"}})
    assert "IS NOT" in sql
    assert params == ["kind", "fact"]


def test_sqlite_where_still_rejects_genuinely_unsupported_keys():
    """`column_in`/`column_like` (--by-symbol) remain Postgres-only, and must
    stay a NAMED error rather than a silently-ignored filter."""
    from chunkshop.search_sqlite import _build_where

    with pytest.raises(ValueError, match="unsupported where keys"):
        _build_where({"column_in": {"symbol_name": ["x"]}})
