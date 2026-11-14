"""PR-004: default hybrid search must work on a table ingested without target.fts.

A table with no ``search_vector`` column (the first documented sample's shape)
used to fail the default ``--legs semantic,fts`` search with
``column "search_vector" does not exist``. Now the FTS leg is skipped with a
single warning when a semantic leg is also requested, and fts-only raises a
clear error. Tables with the column are unchanged.

Needs the live test DB ($CHUNKSHOP_TEST_DSN); skips if unreachable.
"""
from __future__ import annotations

import os
import uuid
import warnings

import psycopg
import pytest
import yaml
from click.testing import CliRunner

from chunkshop import search
from chunkshop.cli import cli
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline

DSN_ENV = "CHUNKSHOP_TEST_DSN"
DSN = os.environ.get(
    DSN_ENV, "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
)
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

DOCS = [
    ("doc_vacation", "# Vacation policy\n\nEmployees accrue vacation days monthly "
                     "and may carry over five days into the next year. Unused "
                     "vacation beyond the carry-over limit is forfeited at year end, "
                     "and managers must approve any request longer than two weeks."),
    ("doc_expense", "# Expense reports\n\nSubmit expense reports within thirty "
                    "days with receipts attached. Reimbursement is paid in the "
                    "next payroll cycle once the finance team has approved the "
                    "report and verified every receipt."),
    ("doc_security", "# Security\n\nRotate passwords every ninety days and use "
                     "hardware keys for production access. Report any suspected "
                     "phishing message to the security team immediately and do not "
                     "click links inside it."),
    ("doc_onboarding", "# Onboarding\n\nNew hires receive a laptop and a buddy "
                       "during their first week. The buddy walks them through the "
                       "tooling, the team calendar, and the first small task they "
                       "will ship."),
]


def _pg_up() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_up(), reason="pg unreachable")


def _cell_dict(schema: str) -> dict:
    return {
        "cell_name": "no_fts_test",
        "source": {"type": "inline"},
        "chunker": {"type": "hierarchy", "max_chars": 2000},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "postgres",
            "dsn_env": DSN_ENV,
            "database": schema,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "docs",
        },
    }


def _build(schema: str, *, with_fts: bool) -> dict:
    os.environ[DSN_ENV] = DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
    data = _cell_dict(schema)
    pipe = Pipeline(CellConfig(**data))
    for doc_id, text in DOCS:
        pipe.ingest_text(doc_id, text, metadata={})
    if with_fts:
        search.ensure_fts(DSN, schema=schema, table=TABLE)
    return data


def _drop(schema: str) -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()


@pytest.fixture(scope="module")
def no_fts():
    schema = f"chunkshop_nofts_{uuid.uuid4().hex[:8]}"
    data = _build(schema, with_fts=False)
    yield schema, data
    _drop(schema)


@pytest.fixture(scope="module")
def with_fts():
    schema = f"chunkshop_hasfts_{uuid.uuid4().hex[:8]}"
    data = _build(schema, with_fts=True)
    yield schema, data
    _drop(schema)


@pytest.fixture(scope="module")
def qvec():
    emb = load_embedder(CellConfig(**_cell_dict("x")).embedder)
    return emb.embed(["vacation policy"])[0]


def _ids(hits):
    return [(h.doc_id, h.seq_num) for h in hits]


def test_default_legs_degrade_to_semantic_with_warning(no_fts, qvec):
    schema, _ = no_fts
    kw = dict(schema=schema, table=TABLE, query="vacation policy",
              query_vec=qvec, k=4)
    expected = search.hybrid_search(DSN, legs=("semantic",), **kw)
    with pytest.warns(search.FtsSkippedWarning, match="target.fts") as rec:
        got = search.hybrid_search(DSN, **kw)  # default legs
    assert _ids(got) == _ids(expected) and got
    assert len(rec) == 1
    assert f"{schema}.{TABLE}" in str(rec[0].message)
    assert all(h.legs == ("semantic",) for h in got)


def test_fts_only_without_column_raises_clear_error(no_fts, qvec):
    schema, _ = no_fts
    with pytest.raises(search.FtsUnavailableError, match="target.fts"):
        search.hybrid_search(
            DSN, schema=schema, table=TABLE, query="vacation policy",
            query_vec=qvec, k=4, legs=("fts",),
        )


def test_table_with_fts_is_hybrid_and_silent(with_fts, qvec):
    schema, _ = with_fts
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        hits = search.hybrid_search(
            DSN, schema=schema, table=TABLE, query="vacation policy",
            query_vec=qvec, k=4,
        )
    assert hits
    assert any("fts" in h.legs for h in hits)


def test_other_db_errors_still_propagate(no_fts, qvec):
    # Missing table is a different error; it must not be swallowed or reworded.
    with pytest.raises(psycopg.errors.UndefinedTable):
        search.hybrid_search(
            DSN, schema=no_fts[0], table="does_not_exist",
            query="vacation policy", query_vec=qvec, k=4,
        )


def _write_cell(tmp_path, data) -> str:
    p = tmp_path / "cell.yaml"
    p.write_text(yaml.dump(data, sort_keys=False))
    return str(p)


def test_cli_default_search_without_fts_exits_zero(no_fts, tmp_path):
    _, data = no_fts
    r = CliRunner().invoke(cli, [
        "search", "--config", _write_cell(tmp_path, data),
        "--query", "vacation policy", "--json",
    ])
    assert r.exit_code == 0, r.output
    import json
    assert json.loads(r.stdout)["chunks"]  # stdout is pure JSON
    assert "target.fts" in r.stderr
    assert "target.fts" not in r.stdout


def test_cli_fts_only_without_fts_fails_cleanly(no_fts, tmp_path):
    _, data = no_fts
    r = CliRunner().invoke(cli, [
        "search", "--config", _write_cell(tmp_path, data),
        "--query", "vacation policy", "--legs", "fts",
    ])
    assert r.exit_code != 0
    assert "target.fts" in r.stderr
    assert "Traceback" not in r.output
