"""SC-005/006: chunkshop search CLI."""
from __future__ import annotations

import json
import os

import pytest
import yaml
from click.testing import CliRunner

from chunkshop.cli import cli

DSN = os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/chunkshop_test")
DSN_ENV = "CHUNKSHOP_TEST_DSN"
SCHEMA = "chunkshop_cli_search_test"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

# Small corpus with clear keyword anchors so both legs can fire.
CORPUS = [
    ("doc_alpha_1", "test",
     "# Alpha topic\n\nAlpha is the first letter of the Greek alphabet. "
     "It is used as a symbol in mathematics and science."),
    ("doc_alpha_2", "test",
     "# Alpha applications\n\nAlpha particles are emitted during radioactive decay. "
     "Alpha testing is the first stage of software quality assurance."),
    ("doc_beta_1", "test",
     "# Beta topic\n\nBeta is the second letter of the Greek alphabet. "
     "Beta testing follows alpha testing in software development."),
]


def _pg_up() -> bool:
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")


@pytest.fixture(scope="module")
def search_cell(tmp_path_factory):
    """Ingest a tiny FTS-enabled corpus and return a path to the cell YAML."""
    import psycopg
    from chunkshop.config import CellConfig
    from chunkshop.pipeline import Pipeline
    from chunkshop.search import ensure_fts

    # tmp_path_factory is module-scoped safe
    tmp_path = tmp_path_factory.mktemp("cli_search")

    os.environ[DSN_ENV] = DSN

    # Clean slate.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()

    # Ingest via Pipeline so we don't need the files source or a real glob.
    first = True
    for doc_id, tag, text in CORPUS:
        mode = "create_if_missing" if first else "append"
        first = False
        cfg = CellConfig(**{
            "cell_name": "cli_search_test",
            "source": {"type": "inline"},
            "chunker": {"type": "hierarchy", "max_chars": 2000},
            "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
            "target": {
                "type": "postgres",
                "dsn_env": DSN_ENV,
                "database": SCHEMA,
                "table": TABLE,
                "hnsw": False,
                "mode": mode,
                "source_tag": tag,
                "fts": {"enabled": True, "language": "english"},
            },
        })
        pipe = Pipeline(cfg)
        pipe.ingest_text(doc_id, text, metadata={"topic": tag})

    ensure_fts(DSN, schema=SCHEMA, table=TABLE)

    # Write a cell YAML that points at the ingested schema.
    # Uses a literal dsn so the CLI doesn't need DSN_ENV.
    cell_yaml = {
        "cell_name": "cli_search_test",
        "source": {"type": "inline"},
        "chunker": {"type": "hierarchy", "max_chars": 2000},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "postgres",
            "dsn": DSN,
            "database": SCHEMA,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "test",
            "fts": {"enabled": True, "language": "english"},
        },
    }
    cell_path = tmp_path / "search_cell.yaml"
    cell_path.write_text(yaml.dump(cell_yaml, sort_keys=False))

    yield cell_path

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def test_search_chunks_json(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["query"] == "alpha"
    assert "chunks" in data
    assert data["summary"] is None


def test_search_text_output(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "3",
    ])
    assert r.exit_code == 0, r.output
    assert r.output.strip()  # prints something


def test_search_accepts_vector_metric_override(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "3", "--vector-metric", "l2", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert "chunks" in data


def test_search_summary_plus_chunks(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha beta", "--k", "5",
        "--return", "summary+chunks", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["summary"] and data["chunks"]


def test_search_summary_only_text(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--return", "summary",
    ])
    assert r.exit_code == 0, r.output
    assert "SUMMARY:" in r.output


def test_search_where_source_filter(search_cell):
    # The fixture ingests all CORPUS docs with source_tag="test".
    # Filtering by a non-matching source_tag returns zero chunks.
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--json",
        "--where", "source=nonexistent_tag",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["chunks"] == []


def test_search_bad_where_exits_nonzero_no_traceback(search_cell):
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--where", "garbage",
    ])
    assert r.exit_code != 0
    assert "Traceback" not in r.output


def test_search_unreachable_db_exits_nonzero_no_traceback(tmp_path, search_cell):
    # Point the config at a dead DSN; expect a clean ClickException, not a traceback.
    import pathlib
    cfg = yaml.safe_load(pathlib.Path(search_cell).read_text())
    cfg["target"]["dsn"] = "postgresql://postgres:postgres@127.0.0.1:1/nope"
    dead = tmp_path / "dead.yaml"
    dead.write_text(yaml.dump(cfg, sort_keys=False))
    r = CliRunner().invoke(cli, [
        "search", "--config", str(dead),
        "--query", "alpha",
    ])
    assert r.exit_code != 0
    assert "Traceback" not in r.output


def test_validate_accepts_fts_cell(search_cell):
    """SC-005: `chunkshop validate` must accept a cell config that includes target.fts."""
    r = CliRunner().invoke(cli, ["validate", "--config", str(search_cell)])
    assert r.exit_code == 0, r.output


def test_search_e2e_summary_plus_chunks(search_cell):
    """SC-005: End-to-end — real ingest (via fixture) → search with summary → both present."""
    r = CliRunner().invoke(cli, [
        "search", "--config", str(search_cell),
        "--query", "alpha", "--k", "5",
        "--return", "summary+chunks", "--json",
    ])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["chunks"] and isinstance(data["summary"], str)
