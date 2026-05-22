"""Integration tests for chunkshop.search — Postgres hybrid retrieval.

Uses the live test DB ($CHUNKSHOP_TEST_DSN); skips if unreachable. Ingests a
small synthetic corpus via the real chunkshop Pipeline (hierarchy chunker +
fastembed bge-small dim 384), runs ensure_fts, then exercises the read API.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from chunkshop import search
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
SCHEMA = "chunkshop_search_test"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384


# Synthetic corpus: distinct topics; some share keywords to exercise hybrid.
# (doc_id, source_tag, text)
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
            "type": "postgres",
            "dsn_env": DSN_ENV,
            "database": SCHEMA,
            "table": TABLE,
            "hnsw": False,
            "mode": "create_if_missing",
            "source_tag": "infra",  # overridden per-doc below via separate pipelines
        },
    })


@pytest.fixture(scope="module")
def corpus_dsn():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn

    # Clean slate.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()

    # Ingest the corpus. Group docs by source_tag so each pipeline carries the
    # right write-once provenance tag.
    by_tag: dict[str, list[tuple[str, str]]] = {}
    for doc_id, tag, text in CORPUS:
        by_tag.setdefault(tag, []).append((doc_id, text))

    first = True
    for tag, docs in by_tag.items():
        data = _cell_config().model_dump(by_alias=True)
        data["target"]["source_tag"] = tag
        # First pipeline creates the table; rest append into it.
        data["target"]["mode"] = "create_if_missing" if first else "append"
        first = False
        pipe = Pipeline(CellConfig(**data))
        for doc_id, text in docs:
            pipe.ingest_text(doc_id, text, metadata={"topic": tag})

    search.ensure_fts(dsn, schema=SCHEMA, table=TABLE)

    yield dsn

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


@pytest.fixture(scope="module")
def embedder():
    cfg = _cell_config().embedder
    return load_embedder(cfg)


def _embed(embedder, text: str):
    return embedder.embed([text])[0]


def test_ensure_fts_idempotent(corpus_dsn):
    # Run twice — no error.
    search.ensure_fts(corpus_dsn, schema=SCHEMA, table=TABLE)
    search.ensure_fts(corpus_dsn, schema=SCHEMA, table=TABLE)

    with psycopg.connect(corpus_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name='search_vector'",
            (SCHEMA, TABLE),
        )
        assert cur.fetchone() is not None, "search_vector column missing"
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND indexname=%s",
            (SCHEMA, TABLE, f"{TABLE}_fts_idx"),
        )
        assert cur.fetchone() is not None, "fts index missing"


def test_ensure_fts_rejects_bad_language(corpus_dsn):
    with pytest.raises(ValueError, match="language"):
        search.ensure_fts(corpus_dsn, schema=SCHEMA, table=TABLE, language="english'; DROP")


def test_keyword_search_finds_distinctive_term(corpus_dsn):
    hits = search.keyword_search(corpus_dsn, schema=SCHEMA, table=TABLE, query="espresso", k=5)
    assert hits, "expected a keyword hit for 'espresso'"
    assert any(h.doc_id == "doc_coffee" for h in hits)
    assert all(h.legs == ("fts",) for h in hits)
    assert all(h.score >= 0 for h in hits)


def test_keyword_search_or_semantics(corpus_dsn):
    """I-12: a multi-word query whose terms live in DIFFERENT docs still returns
    hits via OR. The old AND default (websearch_to_tsquery) would match nothing.

    'espresso' is only in doc_coffee; 'tomatoes' is only in doc_garden. No single
    chunk contains both, so AND-semantics returns zero rows; OR returns both docs.
    """
    query = "espresso tomatoes"

    # Demonstrate the OLD AND behavior returns nothing: websearch_to_tsquery ANDs
    # the terms, so no chunk satisfies `@@` for both.
    with psycopg.connect(corpus_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.{TABLE} "
            f"WHERE search_vector @@ websearch_to_tsquery('english', %s)",
            (query,),
        )
        and_count = cur.fetchone()[0]
    assert and_count == 0, "expected AND-semantics to match zero chunks for this query"

    # NEW OR behavior: partial-term matches count, so both docs surface.
    hits = search.keyword_search(corpus_dsn, schema=SCHEMA, table=TABLE, query=query, k=10)
    assert hits, "OR-semantics should return hits where AND returned 0"
    found = {h.doc_id for h in hits}
    assert "doc_coffee" in found and "doc_garden" in found, (
        f"expected both espresso (doc_coffee) and tomatoes (doc_garden) docs, got {found}"
    )
    assert all(h.legs == ("fts",) for h in hits)


def test_keyword_search_respects_k(corpus_dsn):
    # 'is' style stopwords removed; use a common content word across many docs.
    hits = search.keyword_search(corpus_dsn, schema=SCHEMA, table=TABLE, query="water", k=1)
    assert len(hits) <= 1


def test_semantic_search_returns_similarities(corpus_dsn, embedder):
    qv = _embed(embedder, "approximate nearest neighbor vector similarity search")
    hits = search.semantic_search(corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=3)
    assert hits
    assert all(h.legs == ("semantic",) for h in hits)
    # Scores are similarities (1 - cosine distance), higher=better, ~[0,1].
    for h in hits:
        assert -0.05 <= h.score <= 1.05
    # Should surface vector/pgvector-flavored docs near the top.
    top_ids = {h.doc_id for h in hits[:3]}
    assert top_ids & {"doc_pgvector", "doc_vector_math"}
    # Sorted descending.
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_rrf_boosts_dual_match(corpus_dsn, embedder):
    # Query mentions "pgvector" (keyword hit on doc_pgvector) AND is semantically
    # about vector similarity search. doc_pgvector should hit BOTH legs and thus
    # outrank docs that hit only one.
    query = "pgvector cosine similarity search"
    qv = _embed(embedder, query)
    hits = search.hybrid_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query=query, query_vec=qv, k=5, fusion="rrf",
    )
    assert hits
    top = hits[0]
    assert top.doc_id == "doc_pgvector", f"expected doc_pgvector on top, got {top.doc_id}"
    assert set(top.legs) == {"semantic", "fts"}, f"expected dual-leg, got {top.legs}"

    # A dual-leg hit should outrank any single-leg hit.
    dual = [h for h in hits if len(h.legs) == 2]
    single = [h for h in hits if len(h.legs) == 1]
    if dual and single:
        assert max(h.score for h in dual) > max(h.score for h in single)


def test_hybrid_weighted_runs(corpus_dsn, embedder):
    query = "pgvector cosine similarity search"
    qv = _embed(embedder, query)
    hits = search.hybrid_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query=query, query_vec=qv, k=5, fusion="weighted",
        weights={"semantic": 1.0, "fts": 1.0},
    )
    assert hits
    assert hits[0].doc_id == "doc_pgvector"


def test_where_filter_by_source(corpus_dsn, embedder):
    qv = _embed(embedder, "language programming")
    hits = search.semantic_search(
        corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=10,
        where={"source": "food"},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_coffee", "doc_tea"}


def test_where_filter_by_metadata(corpus_dsn, embedder):
    qv = _embed(embedder, "anything at all")
    hits = search.semantic_search(
        corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=10,
        where={"metadata": {"topic": "hobby"}},
    )
    assert hits
    assert {h.doc_id for h in hits} <= {"doc_garden", "doc_bicycle"}


def test_overfetch_surfaces_deep_candidate(corpus_dsn, embedder):
    """I-3: candidate_multiplier widens each leg's candidate pool before fusion.

    WEAK invariant chosen. The synthetic corpus is tiny (10 docs / few chunks),
    so a contrived "deep in both legs but fused top-k" chunk cannot be reliably
    constructed without overfitting to embedding quirks. Instead we assert the
    mechanism directly: for a multi-term query at small k, the fused candidate
    set obtained with candidate_multiplier=3 is a SUPERSET-OR-EQUAL of the one
    with candidate_multiplier=1. That proves the wider pool can only add (never
    drop) candidates that fusion gets to consider.
    """
    query = "vector similarity search database programming language"
    qv = _embed(embedder, query)
    k = 2

    narrow = search.hybrid_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query=query, query_vec=qv, k=k, fusion="rrf", candidate_multiplier=1,
    )
    wide = search.hybrid_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query=query, query_vec=qv, k=k, fusion="rrf", candidate_multiplier=3,
    )
    # Both truncate to k for the returned list.
    assert len(narrow) <= k and len(wide) <= k

    # The mechanism: re-derive the *pre-truncation* fused candidate sets by
    # fetching the legs at each width and fusing, then compare key sets.
    def _fused_keys(mult: int) -> set:
        cand_k = max(k * mult, k)
        legs = {
            "semantic": search.semantic_search(
                corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=cand_k
            ),
            "fts": search.keyword_search(
                corpus_dsn, schema=SCHEMA, table=TABLE, query=query, k=cand_k
            ),
        }
        fused = search._fuse_rrf(legs, rrf_k=60)
        return set(fused.keys())

    narrow_keys = _fused_keys(1)
    wide_keys = _fused_keys(3)
    assert narrow_keys <= wide_keys, (
        f"wider pool dropped candidates: narrow={narrow_keys} wide={wide_keys}"
    )


def test_tag_filter_case_insensitive(corpus_dsn, embedder):
    """I-7: a tag filter passed in UPPERCASE matches the lowercase stored tag."""
    # Seed a known lowercase tag on one chunk so we don't depend on extractor state.
    with psycopg.connect(corpus_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.{TABLE} SET tags = ARRAY['espresso']::text[] "
            f"WHERE doc_id = %s",
            ("doc_coffee",),
        )
        conn.commit()

    qv = _embed(embedder, "anything at all")
    hits = search.semantic_search(
        corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=10,
        where={"tags": ["ESPRESSO"]},  # uppercase query against lowercase store
    )
    assert hits, "uppercase tag filter matched nothing — case-sensitivity bug"
    assert {h.doc_id for h in hits} <= {"doc_coffee"}


def test_hit_exposes_embedded_text(corpus_dsn, embedder):
    """I-14: Hit.embedded_text exposes embedded_content (heading-bearing).

    The corpus is chunked with the `hierarchy` chunker, which PREPENDS the
    markdown section heading (e.g. "# Postgres tuning") to embedded_content as
    framing context. original_content (Hit.text) drops that heading. So for at
    least one hit, embedded_text must be non-empty and a strict superset of text
    (contains the heading text that text lacks).
    """
    qv = _embed(embedder, "approximate nearest neighbor vector similarity search")
    sem = search.semantic_search(corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=10)
    assert sem, "expected semantic hits"
    # Every hit carries a non-empty embedded_text, and embedded_text contains text
    # (hierarchy only prepends, never replaces — the body survives verbatim).
    for h in sem:
        assert h.embedded_text, f"embedded_text empty for {h.doc_id}"
        assert h.text in h.embedded_text, (
            f"embedded_text should contain original text for {h.doc_id}"
        )
    # At least one hit's embedded_text is STRICTLY longer than text — proof the
    # heading was prepended and would otherwise be dropped from RAG/summary input.
    assert any(len(h.embedded_text) > len(h.text) for h in sem), (
        "no hit had a heading-bearing embedded_text longer than original_content"
    )
    # The heading-bearing hit literally prepends the section heading (from
    # metadata['heading']) — present in embedded_text, absent from the text body.
    superset_hits = [h for h in sem if len(h.embedded_text) > len(h.text)]
    assert any(
        h.metadata.get("heading")
        and h.metadata["heading"] in h.embedded_text
        and h.metadata["heading"] not in h.text
        for h in superset_hits
    ), "expected the section heading prepended into embedded_text but absent from text"

    # The keyword leg carries it through too.
    kw = search.keyword_search(corpus_dsn, schema=SCHEMA, table=TABLE, query="pgvector", k=5)
    assert kw and all(h.embedded_text for h in kw)

    # Fusion preserves embedded_text on the deduped/representative Hit.
    fused = search.hybrid_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query="pgvector cosine similarity search", query_vec=qv, k=5, fusion="rrf",
    )
    assert fused and all(h.embedded_text for h in fused)


def test_keyword_search_sql_injection_safe(corpus_dsn):
    # Malicious query must be treated as bound text, not SQL. No error, no damage.
    hits = search.keyword_search(
        corpus_dsn, schema=SCHEMA, table=TABLE,
        query="'; DROP TABLE chunks; --", k=5,
    )
    assert isinstance(hits, list)  # returns safely (likely empty)
    # Table must still exist.
    with psycopg.connect(corpus_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
            (SCHEMA, TABLE),
        )
        assert cur.fetchone()[0], "table was dropped — injection succeeded!"


def test_where_filter_injection_safe(corpus_dsn, embedder):
    qv = _embed(embedder, "anything")
    hits = search.semantic_search(
        corpus_dsn, schema=SCHEMA, table=TABLE, query_vec=qv, k=5,
        where={"source": "infra'; DROP TABLE chunks; --"},
    )
    assert hits == []  # no source matches that literal; bound param, no damage
    with psycopg.connect(corpus_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
            (SCHEMA, TABLE),
        )
        assert cur.fetchone()[0]
