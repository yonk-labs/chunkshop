"""SP-E Part 1: ``chunkshop search --by-symbol NAME`` filter.

The ``symbol_aware`` chunker (SP-B) emits ``metadata.symbol_name`` for every
chunk. When that key is promoted to a Postgres column via
``target.promote_metadata`` it becomes filterable directly. This test ingests
a small Python corpus through the symbol_aware chunker with promoted symbol
metadata, then verifies the new ``--by-symbol`` CLI flag scopes the result
set to chunks whose ``symbol_name`` matches.

Requires a live Postgres at ``CHUNKSHOP_TEST_DSN``; skips cleanly otherwise.
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
SCHEMA = f"chunkshop_cli_bysymbol_{uuid.uuid4().hex[:8]}"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


def _pg_up() -> bool:
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")


# Three Python "files" with distinct top-level symbols so we can probe by name.
_FILES = {
    "alpha.py": (
        "def alpha_one(x):\n"
        "    return x + 1\n"
        "\n\n"
        "def shared_helper(x):\n"
        "    return x * 2\n"
    ),
    "beta.py": (
        "class BetaThing:\n"
        "    def run(self):\n"
        "        return 'beta'\n"
        "\n\n"
        "def beta_func(y):\n"
        "    return y - 1\n"
    ),
    "gamma.py": (
        "def gamma_proc(z):\n"
        "    return shared_helper(z) + beta_func(z)\n"
    ),
}


@pytest.fixture(scope="module")
def by_symbol_cell(tmp_path_factory):
    """Ingest the three-file corpus through the Pipeline with promoted symbol metadata."""
    import psycopg

    from chunkshop.config import CellConfig
    from chunkshop.pipeline import Pipeline
    from chunkshop.search import ensure_fts

    tmp_path = tmp_path_factory.mktemp("cli_bysymbol")
    os.environ[DSN_ENV] = DSN

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()

    # Build a CellConfig once with create_if_missing mode + promoted symbol_name.
    # Pipeline.ingest_text feeds each file through symbol_aware chunker.
    cfg = CellConfig(
        **{
            "cell_name": "by_symbol_test",
            "source": {"type": "inline"},
            "chunker": {"type": "symbol_aware"},
            "embedder": {
                "type": "fastembed",
                "model_name": MODEL,
                "dim": DIM,
            },
            "target": {
                "type": "postgres",
                "dsn_env": DSN_ENV,
                "database": SCHEMA,
                "table": TABLE,
                "hnsw": False,
                "mode": "create_if_missing",
                "source_tag": "cli_bysymbol",
                "fts": {"enabled": True, "language": "english"},
                "promote_metadata": [
                    {"path": "symbol_name", "type": "text"},
                    {"path": "fqn", "type": "text"},
                    {"path": "symbol_type", "type": "text"},
                    {"path": "language", "type": "text"},
                    {"path": "start_line", "type": "int"},
                    {"path": "end_line", "type": "int"},
                ],
            },
        }
    )
    pipe = Pipeline(cfg)
    for name, text in _FILES.items():
        pipe.ingest_text(name, text, metadata={"path": name})

    ensure_fts(DSN, schema=SCHEMA, table=TABLE)

    # Write a CLI-loadable YAML that points at the freshly-ingested schema.
    cell_yaml = {
        "cell_name": "by_symbol_test",
        "source": {"type": "inline"},
        "chunker": {"type": "symbol_aware"},
        "embedder": {
            "type": "fastembed",
            "model_name": MODEL,
            "dim": DIM,
        },
        "target": {
            "type": "postgres",
            "dsn": DSN,
            "database": SCHEMA,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "cli_bysymbol",
            "fts": {"enabled": True, "language": "english"},
            "promote_metadata": [
                {"path": "symbol_name", "type": "text"},
                {"path": "fqn", "type": "text"},
                {"path": "symbol_type", "type": "text"},
                {"path": "language", "type": "text"},
                {"path": "start_line", "type": "int"},
                {"path": "end_line", "type": "int"},
            ],
        },
    }
    cell_path = tmp_path / "cell.yaml"
    cell_path.write_text(yaml.dump(cell_yaml, sort_keys=False))

    yield cell_path

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def test_by_symbol_exact_match(by_symbol_cell: Path) -> None:
    """--by-symbol returns ONLY chunks whose symbol_name matches."""
    r = CliRunner().invoke(
        cli,
        [
            "search",
            "--config",
            str(by_symbol_cell),
            "--query",
            "function",
            "--k",
            "10",
            "--by-symbol",
            "alpha_one",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    chunks = data["chunks"]
    assert chunks, "expected at least one chunk for alpha_one"
    for c in chunks:
        # The promoted symbol_name column lives in chunk metadata too because the
        # sink writes both — but the filter is on the column. Look at the
        # returned text snippet for the symbol's signature.
        assert "alpha_one" in c["text"], (
            f"chunk does not look like alpha_one: {c['text'][:80]}"
        )


def test_by_symbol_comma_separated(by_symbol_cell: Path) -> None:
    """Comma-separated names act as IN (...)."""
    r = CliRunner().invoke(
        cli,
        [
            "search",
            "--config",
            str(by_symbol_cell),
            "--query",
            "function",
            "--k",
            "10",
            "--by-symbol",
            "alpha_one,beta_func",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    chunks = json.loads(r.output)["chunks"]
    assert chunks, "expected hits for at least one of the two symbols"
    symbol_blobs = [c["text"] for c in chunks]
    # Every hit must be one or the other; no spurious gamma_proc.
    assert all(("alpha_one" in t or "beta_func" in t) for t in symbol_blobs)
    assert not any("gamma_proc" in t for t in symbol_blobs)


def test_by_symbol_glob_prefix(by_symbol_cell: Path) -> None:
    """Trailing ``*`` enables prefix match via LIKE."""
    r = CliRunner().invoke(
        cli,
        [
            "search",
            "--config",
            str(by_symbol_cell),
            "--query",
            "function",
            "--k",
            "10",
            "--by-symbol",
            "alpha_*",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    chunks = json.loads(r.output)["chunks"]
    assert chunks, "expected at least one alpha_* hit"
    for c in chunks:
        assert "alpha_" in c["text"], c["text"][:80]


def test_by_symbol_no_match_returns_empty(by_symbol_cell: Path) -> None:
    """Unknown symbol name -> empty hit list, no error."""
    r = CliRunner().invoke(
        cli,
        [
            "search",
            "--config",
            str(by_symbol_cell),
            "--query",
            "anything",
            "--k",
            "5",
            "--by-symbol",
            "nonexistent_symbol_xyz",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["chunks"] == []


def test_by_symbol_composes_with_query(by_symbol_cell: Path) -> None:
    """--by-symbol narrows the pool BUT the query still scores; both fire."""
    r = CliRunner().invoke(
        cli,
        [
            "search",
            "--config",
            str(by_symbol_cell),
            "--query",
            "return",
            "--k",
            "5",
            "--by-symbol",
            "shared_helper",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    chunks = json.loads(r.output)["chunks"]
    # Only shared_helper survives the filter; the rank still uses the query.
    assert chunks, "expected shared_helper to match"
    for c in chunks:
        assert "shared_helper" in c["text"], c["text"][:80]
