"""Light correctness tests for chunkshop.search_sqlite — SQLite hybrid retrieval.

No external infra: file-backed SQLite in tmp_path, FTS5 + sqlite-vec built in.
Ingests a small synthetic corpus via the real Pipeline (hierarchy chunker +
fastembed bge-small dim 384), runs ensure_fts, then exercises the read API.

Mirrors test_search_pg.py but smaller — proves semantic + keyword + fusion work.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlite_vec")

from chunkshop import search_sqlite as search
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline


TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


# Same synthetic corpus as the PG test: distinct topics, some shared keywords.
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


def _cell_config(dsn_env: str) -> CellConfig:
    return CellConfig(**{
        "cell_name": "search_test",
        "source": {"type": "inline"},
        "chunker": {"type": "hierarchy", "max_chars": 2000},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": {
            "type": "sqlite",
            "dsn_env": dsn_env,
            "database": "ignored",
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "infra",
        },
    })


@pytest.fixture(scope="module")
def corpus_dsn(tmp_path_factory):
    import os
    db_path = tmp_path_factory.mktemp("sqlite_search") / "chunks.db"
    dsn = str(db_path)
    dsn_env = "SQLITE_SEARCH_TEST_PATH"
    os.environ[dsn_env] = dsn

    by_tag: dict[str, list[tuple[str, str]]] = {}
    for doc_id, tag, text in CORPUS:
        by_tag.setdefault(tag, []).append((doc_id, text))

    first = True
    for tag, docs in by_tag.items():
        data = _cell_config(dsn_env).model_dump(by_alias=True)
        data["target"]["source_tag"] = tag
        data["target"]["mode"] = "create_if_missing" if first else "append"
        first = False
        pipe = Pipeline(CellConfig(**data))
        for doc_id, text in docs:
            pipe.ingest_text(doc_id, text, metadata={"topic": tag})

    search.ensure_fts(dsn, table=TABLE)
    yield dsn


@pytest.fixture(scope="module")
def embedder():
    cfg = _cell_config("SQLITE_SEARCH_TEST_PATH").embedder
    return load_embedder(cfg)


def _embed(embedder, text: str):
    return embedder.embed([text])[0]


def _table_exists(dsn: str, name: str) -> bool:
    with SQLiteBackend(dsn=dsn).connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,))
        return cur.fetchone() is not None


def test_ensure_fts_idempotent(corpus_dsn):
    search.ensure_fts(corpus_dsn, table=TABLE)
    search.ensure_fts(corpus_dsn, table=TABLE)
    assert _table_exists(corpus_dsn, f"{TABLE}_fts")


def test_keyword_search_finds_distinctive_term(corpus_dsn):
    hits = search.keyword_search(corpus_dsn, table=TABLE, query="espresso", k=5)
    assert hits, "expected a keyword hit for 'espresso'"
    assert any(h.doc_id == "doc_coffee" for h in hits)
    assert all(h.legs == ("fts",) for h in hits)


def test_keyword_search_or_semantics(corpus_dsn):
    """I-12: a multi-word query whose terms live in DIFFERENT docs still returns
    hits via OR. The old bare-string MATCH (implicit AND) matched nothing.

    'espresso' is only in doc_coffee; 'tomatoes' is only in doc_garden. No single
    chunk contains both, so the implicit-AND default returns zero rows; OR both.
    """
    query = "espresso tomatoes"

    # Demonstrate the OLD implicit-AND behavior returns nothing: a bare multi-word
    # MATCH string ANDs the terms in FTS5.
    with SQLiteBackend(dsn=corpus_dsn).connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f'SELECT count(*) FROM "{TABLE}_fts" WHERE "{TABLE}_fts" MATCH ?',
            (query,),
        )
        and_count = cur.fetchone()[0]
    assert and_count == 0, "expected implicit-AND MATCH to match zero chunks"

    # NEW OR behavior: partial-term matches count, so both docs surface.
    hits = search.keyword_search(corpus_dsn, table=TABLE, query=query, k=10)
    assert hits, "OR-semantics should return hits where AND returned 0"
    found = {h.doc_id for h in hits}
    assert "doc_coffee" in found and "doc_garden" in found, (
        f"expected both espresso (doc_coffee) and tomatoes (doc_garden) docs, got {found}"
    )
    assert all(h.legs == ("fts",) for h in hits)


def test_semantic_search_returns_similarities(corpus_dsn, embedder):
    qv = _embed(embedder, "approximate nearest neighbor vector similarity search")
    hits = search.semantic_search(corpus_dsn, table=TABLE, query_vec=qv, k=3)
    assert hits
    assert all(h.legs == ("semantic",) for h in hits)
    for h in hits:
        assert -0.05 <= h.score <= 1.05
    top_ids = {h.doc_id for h in hits[:3]}
    assert top_ids & {"doc_pgvector", "doc_vector_math"}
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_rrf_boosts_dual_match(corpus_dsn, embedder):
    query = "pgvector cosine similarity search"
    qv = _embed(embedder, query)
    hits = search.hybrid_search(
        corpus_dsn, table=TABLE, query=query, query_vec=qv, k=5, fusion="rrf",
    )
    assert hits
    top = hits[0]
    assert top.doc_id == "doc_pgvector", f"expected doc_pgvector on top, got {top.doc_id}"
    assert set(top.legs) == {"semantic", "fts"}, f"expected dual-leg, got {top.legs}"

    dual = [h for h in hits if len(h.legs) == 2]
    single = [h for h in hits if len(h.legs) == 1]
    if dual and single:
        assert max(h.score for h in dual) > max(h.score for h in single)


def test_where_filter_by_source(corpus_dsn, embedder):
    qv = _embed(embedder, "language programming")
    hits = search.semantic_search(
        corpus_dsn, table=TABLE, query_vec=qv, k=10, where={"source": "food"},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_coffee", "doc_tea"}


def test_where_filter_by_metadata(corpus_dsn, embedder):
    qv = _embed(embedder, "anything at all")
    hits = search.semantic_search(
        corpus_dsn, table=TABLE, query_vec=qv, k=10, where={"metadata": {"topic": "hobby"}},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_garden", "doc_bicycle"}


def test_hit_exposes_embedded_text(corpus_dsn, embedder):
    """I-14: Hit.embedded_text exposes heading-bearing embedded_content (sqlite).

    The hierarchy chunker prepends the markdown heading to embedded_content. So
    every hit's embedded_text is non-empty and contains the original text; at
    least one is strictly longer (the prepended heading would otherwise be lost).
    """
    qv = _embed(embedder, "approximate nearest neighbor vector similarity search")
    sem = search.semantic_search(corpus_dsn, table=TABLE, query_vec=qv, k=10)
    assert sem
    for h in sem:
        assert h.embedded_text, f"embedded_text empty for {h.doc_id}"
        assert h.text in h.embedded_text
    assert any(len(h.embedded_text) > len(h.text) for h in sem), (
        "no hit had a heading-bearing embedded_text longer than original_content"
    )
    # keyword leg + fusion carry it through.
    kw = search.keyword_search(corpus_dsn, table=TABLE, query="pgvector", k=5)
    assert kw and all(h.embedded_text for h in kw)
    fused = search.hybrid_search(
        corpus_dsn, table=TABLE,
        query="pgvector cosine similarity search", query_vec=qv, k=5, fusion="rrf",
    )
    assert fused and all(h.embedded_text for h in fused)


def test_keyword_search_sql_injection_safe(corpus_dsn):
    # Malicious query must be treated as bound text. No error, no damage.
    hits = search.keyword_search(
        corpus_dsn, table=TABLE, query="'; DROP TABLE chunks; --", k=5,
    )
    assert isinstance(hits, list)
    assert _table_exists(corpus_dsn, TABLE), "table was dropped — injection succeeded!"
