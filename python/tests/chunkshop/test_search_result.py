"""Integration tests for SearchResult + search() entry point (SC-008, SC-009, SC-010).

Uses the live test DB ($CHUNKSHOP_TEST_DSN); skips if unreachable. Ingests a
small synthetic corpus, runs ensure_fts, then exercises the three return modes.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline
from chunkshop.search import ensure_fts

DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
DSN = os.environ.get(DSN_ENV, DEFAULT_DSN)
SCHEMA = "chunkshop_sr_test"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


def _pg_up() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


# Synthetic corpus — small but with distinct keyword anchors (alpha/beta).
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
    ("doc_beta_2", "test",
     "# Beta radiation\n\nBeta radiation consists of fast electrons or positrons. "
     "It has intermediate penetrating power between alpha and gamma."),
    ("doc_gamma", "test",
     "# Gamma rays\n\nGamma rays are high-energy electromagnetic radiation. "
     "They are produced by radioactive decay and nuclear reactions."),
    ("doc_delta", "test",
     "# Delta symbol\n\nDelta is the fourth letter of the Greek alphabet. "
     "In calculus delta represents a small change in a variable."),
]


def _cell_config(mode: str = "create_if_missing") -> CellConfig:
    return CellConfig(**{
        "cell_name": "sr_test",
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
            "source_tag": "test",
        },
    })


@pytest.fixture(scope="module")
def seeded():
    if not _pg_up():
        pytest.skip(f"PG at {DSN} not reachable")

    os.environ[DSN_ENV] = DSN

    # Clean slate.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()

    # Ingest all docs in one pipeline (create_if_missing for first, append for rest).
    first = True
    for doc_id, tag, text in CORPUS:
        mode = "create_if_missing" if first else "append"
        first = False
        data = _cell_config(mode).model_dump(by_alias=True)
        data["target"]["source_tag"] = tag
        pipe = Pipeline(CellConfig(**data))
        pipe.ingest_text(doc_id, text, metadata={"topic": tag})

    ensure_fts(DSN, schema=SCHEMA, table=TABLE)

    cfg = _cell_config()
    emb = load_embedder(cfg.embedder)
    yield (SCHEMA, emb)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def test_chunks_mode_returns_hits_no_summary(seeded):
    from chunkshop.search_common import SearchResult, search

    schema, emb = seeded
    qv = emb.embed(["alpha"])[0]
    res = search(DSN, schema=schema, table=TABLE, query="alpha", query_vec=qv,
                 k=5, return_mode="chunks")
    assert isinstance(res, SearchResult)
    assert res.summary is None
    assert res.query == "alpha"
    assert all(hasattr(h, "doc_id") for h in res.chunks)


def test_summary_mode_summarizes_and_drops_chunks(seeded):
    from chunkshop.search_common import search
    from chunkshop.summarizers.lede import summarize

    schema, emb = seeded
    qv = emb.embed(["alpha beta"])[0]
    res = search(DSN, schema=schema, table=TABLE, query="alpha beta", query_vec=qv,
                 k=5, return_mode="summary", summarize_fn=summarize)
    assert res.chunks == []
    assert isinstance(res.summary, str) and res.summary


def test_summary_plus_chunks_has_both(seeded):
    from chunkshop.search_common import search
    from chunkshop.summarizers.lede import summarize

    schema, emb = seeded
    qv = emb.embed(["alpha"])[0]
    res = search(DSN, schema=schema, table=TABLE, query="alpha", query_vec=qv,
                 k=5, return_mode="summary+chunks", summarize_fn=summarize)
    assert res.chunks and res.summary


def test_summary_requires_summarize_fn(seeded):
    from chunkshop.search_common import search

    schema, emb = seeded
    qv = emb.embed(["alpha"])[0]
    with pytest.raises(ValueError):
        search(DSN, schema=schema, table=TABLE, query="alpha", query_vec=qv,
               k=5, return_mode="summary")  # no summarize_fn


def test_explicit_summary_hints_override(seeded):
    from chunkshop.search_common import search
    from chunkshop.summarizers.lede import summarize

    schema, emb = seeded
    qv = emb.embed(["alpha"])[0]
    res = search(DSN, schema=schema, table=TABLE, query="alpha", query_vec=qv,
                 k=5, return_mode="summary", summarize_fn=summarize,
                 summary_hints=["beta"])
    assert isinstance(res.summary, str)


def test_chunks_mode_no_lede_import(seeded, monkeypatch):
    """SC-010/SC-011: chunks return mode must NOT trigger a lede import."""
    import builtins
    import sys

    schema, emb = seeded

    # Evict any cached lede modules so a fresh import would be observable.
    for m in [k for k in sys.modules if k == "lede" or k.startswith("lede.")]:
        monkeypatch.delitem(sys.modules, m, raising=False)

    real_import = builtins.__import__

    def guard(name, *a, **k):
        assert not (name == "lede" or name.startswith("lede.")), (
            f"chunks mode imported {name!r} — lede must not be imported in this path"
        )
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", guard)

    qv = emb.embed(["alpha"])[0]
    from chunkshop.search_common import search

    res = search(DSN, schema=schema, table=TABLE, query="alpha", query_vec=qv,
                 k=5, return_mode="chunks")  # must not trip the guard
    assert res.summary is None
