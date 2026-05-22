"""Light correctness tests for chunkshop.search_clickhouse — CH hybrid retrieval.

Skipped unless $CHUNKSHOP_TEST_DSN_CH is set and points to a reachable
ClickHouse 24.10+ instance. Ingests a small synthetic corpus via the real
Pipeline, runs ensure_fts (tokenbf_v1 skip-index), then exercises the read API.

NOTE: ClickHouse FTS is intentionally DEGRADED (binary token match, score 1.0 —
see chunkshop.search_clickhouse module docstring / LD-3). So the hybrid test
asserts the union is returned sensibly and dual-leg matches still surface, NOT
that the fts leg supplies graded relevance.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("clickhouse_connect")

from chunkshop import search_clickhouse as search
from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline


DSN_ENV = "CHUNKSHOP_TEST_DSN_CH"
DSN = os.environ.get(DSN_ENV)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_ENV} not set")

DB = "chunkshop_search_test_ch"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


CORPUS = [
    ("doc_postgres", "infra",
     "# Postgres tuning\n\nPostgres is a relational database. To tune Postgres "
     "you adjust shared_buffers and work_mem. The query planner uses statistics "
     "gathered by ANALYZE to choose index scans over sequential scans."),
    ("doc_pgvector", "infra",
     "# pgvector\n\npgvector adds vector similarity search to Postgres. It "
     "stores embeddings and supports cosine distance with HNSW indexes for fast "
     "approximate nearest neighbor lookups over high-dimensional vectors."),
    ("doc_redis", "infra",
     "# Redis caching\n\nRedis is an in-memory key-value store used for caching. "
     "It keeps hot data in RAM to reduce database load and serves reads with "
     "sub-millisecond latency."),
    ("doc_python", "lang",
     "# Python\n\nPython is a high-level programming language known for "
     "readability. It is widely used for data science, web backends, and "
     "automation scripting."),
    ("doc_rust", "lang",
     "# Rust\n\nRust is a systems programming language focused on memory safety "
     "without a garbage collector. Its borrow checker enforces ownership rules "
     "at compile time."),
    ("doc_coffee", "food",
     "# Coffee brewing\n\nCoffee is brewed by passing hot water through ground "
     "beans. Espresso uses high pressure while pour-over relies on gravity and "
     "a slow controlled drip."),
    ("doc_tea", "food",
     "# Tea steeping\n\nTea is made by steeping leaves in hot water. Green tea "
     "steeps at a lower temperature than black tea to avoid a bitter taste."),
    ("doc_garden", "hobby",
     "# Gardening\n\nGardening involves planting seeds, watering soil, and "
     "managing sunlight. Tomatoes need full sun while ferns prefer shade."),
    ("doc_bicycle", "hobby",
     "# Bicycle maintenance\n\nBicycle maintenance includes lubricating the "
     "chain, checking tire pressure, and adjusting brakes for safe riding."),
    ("doc_vector_math", "lang",
     "# Vector math\n\nA vector has magnitude and direction. The cosine of the "
     "angle between two vectors measures their similarity, which is the basis "
     "for many embedding search systems."),
]


def _cell_config() -> CellConfig:
    return CellConfig(**{
        "cell_name": "search_test",
        "source": {"type": "inline"},
        "chunker": {"type": "hierarchy", "max_chars": 2000},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "clickhouse",
            "dsn_env": DSN_ENV,
            "database": DB,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "infra",
        },
    })


@pytest.fixture(scope="module")
def corpus_dsn():
    dsn = os.environ[DSN_ENV]
    be = ClickHouseBackend(dsn=dsn)
    try:
        with be.connect() as client:
            client.query("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse at {dsn} not reachable: {exc}")

    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{DB}` SYNC")

    by_tag: dict[str, list[tuple[str, str]]] = {}
    for doc_id, tag, text in CORPUS:
        by_tag.setdefault(tag, []).append((doc_id, text))

    first = True
    for tag, docs in by_tag.items():
        data = _cell_config().model_dump(by_alias=True)
        data["target"]["source_tag"] = tag
        data["target"]["mode"] = "create_if_missing" if first else "append"
        first = False
        pipe = Pipeline(CellConfig(**data))
        for doc_id, text in docs:
            pipe.ingest_text(doc_id, text, metadata={"topic": tag})

    search.ensure_fts(dsn, schema=DB, table=TABLE)
    yield dsn

    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{DB}` SYNC")


@pytest.fixture(scope="module")
def embedder():
    return load_embedder(_cell_config().embedder)


def _embed(embedder, text: str):
    return embedder.embed([text])[0]


def _skip_index_exists(dsn: str) -> bool:
    with ClickHouseBackend(dsn=dsn).connect() as client:
        r = client.query(
            "SELECT count() FROM system.data_skipping_indices "
            "WHERE database = {db:String} AND table = {tbl:String} AND name = 'original_content_tok'",
            parameters={"db": DB, "tbl": TABLE},
        )
        return int(r.result_rows[0][0]) > 0


def test_ensure_fts_idempotent(corpus_dsn):
    search.ensure_fts(corpus_dsn, schema=DB, table=TABLE)
    search.ensure_fts(corpus_dsn, schema=DB, table=TABLE)
    assert _skip_index_exists(corpus_dsn), "tokenbf_v1 skip index missing"


def test_keyword_search_finds_distinctive_term(corpus_dsn):
    # Degraded FTS: binary token match, score 1.0.
    hits = search.keyword_search(corpus_dsn, schema=DB, table=TABLE, query="espresso", k=5)
    assert hits, "expected a token match for 'espresso'"
    assert any(h.doc_id == "doc_coffee" for h in hits)
    assert all(h.legs == ("fts",) for h in hits)
    assert all(h.score == 1.0 for h in hits), "CH fts is filter-grade — score must be binary 1.0"


def test_semantic_search_returns_similarities(corpus_dsn, embedder):
    qv = _embed(embedder, "approximate nearest neighbor vector similarity search")
    hits = search.semantic_search(corpus_dsn, schema=DB, table=TABLE, query_vec=qv, k=3)
    assert hits
    assert all(h.legs == ("semantic",) for h in hits)
    for h in hits:
        assert -0.05 <= h.score <= 1.05
    top_ids = {h.doc_id for h in hits[:3]}
    assert top_ids & {"doc_pgvector", "doc_vector_math"}
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_returns_union_sensibly(corpus_dsn, embedder):
    # CH fts is binary, so we don't assert dual-leg-outranks via fts relevance.
    # We assert: the union is returned, doc_pgvector (hit by both legs) appears
    # and is flagged dual-leg, and nothing crashes.
    query = "pgvector cosine similarity search"
    qv = _embed(embedder, query)
    hits = search.hybrid_search(
        corpus_dsn, schema=DB, table=TABLE, query=query, query_vec=qv, k=5, fusion="rrf",
    )
    assert hits
    by_id = {h.doc_id: h for h in hits}
    assert "doc_pgvector" in by_id, "expected doc_pgvector in fused union"
    assert set(by_id["doc_pgvector"].legs) == {"semantic", "fts"}, (
        f"doc_pgvector should be dual-leg, got {by_id['doc_pgvector'].legs}"
    )
    # A dual-leg hit should still float to the top of the fused list.
    assert hits[0].doc_id == "doc_pgvector"


def test_where_filter_by_source(corpus_dsn, embedder):
    qv = _embed(embedder, "language programming")
    hits = search.semantic_search(
        corpus_dsn, schema=DB, table=TABLE, query_vec=qv, k=10, where={"source": "food"},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_coffee", "doc_tea"}


def test_where_filter_by_metadata(corpus_dsn, embedder):
    qv = _embed(embedder, "anything at all")
    hits = search.semantic_search(
        corpus_dsn, schema=DB, table=TABLE, query_vec=qv, k=10,
        where={"metadata": {"topic": "hobby"}},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_garden", "doc_bicycle"}


def test_keyword_search_sql_injection_safe(corpus_dsn):
    # Tokenizer strips punctuation/quotes; bound params anyway. No damage.
    hits = search.keyword_search(
        corpus_dsn, schema=DB, table=TABLE, query="'; DROP TABLE chunks; --", k=5,
    )
    assert isinstance(hits, list)
    with ClickHouseBackend(dsn=corpus_dsn).connect() as client:
        r = client.query(
            "SELECT count() FROM system.tables WHERE database={db:String} AND name={tbl:String}",
            parameters={"db": DB, "tbl": TABLE},
        )
        assert int(r.result_rows[0][0]) == 1, "table was dropped — injection succeeded!"
