"""Known-issue regression test — bento#953.

chunkshop's hybrid/FTS retrieval has a per-backend tokenizer bug class: a
hyphen-numeric ID (e.g. "INC-0001") does not behave the same way across
backends when a user searches for it verbatim.

Root cause (bento#953, filed against this repo's consumer):

  - **Postgres** (``chunkshop/search.py:73``): the query tokenizer
    ``_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")`` splits "INC-0001" into
    ``["inc", "0001"]`` and OR-joins them into ``to_tsquery``. But
    ``to_tsvector('english', 'INC-0001')`` stores the lexeme ``'-0001'``
    (hyphen retained) plus ``'inc'`` — it does NOT store a bare ``'0001'``
    lexeme. Verified directly against this repo's test Postgres::

        SELECT to_tsvector('english', 'INC-0001');
        --  '-0001':2 'inc':1

        SELECT to_tsvector('english','INC-0001') @@ to_tsquery('english','0001');
        --  f   (no match)

    So the query's "0001" token never matches, and the only surviving
    signal is the generic "inc" token — which matches every other
    "INC-xxxx" doc in the corpus identically. The doc-specific part of the
    ID contributes ZERO ranking signal; ``ts_rank`` cannot tell the gold
    document apart from any other document that merely contains "inc".

  - **SQLite**: FTS5's default tokenizer splits the SAME way on both the
    index and query side, so the FTS leg alone ranks the gold doc #1 with
    an overwhelming bm25 margin (no tokenizer bug here — confirmed below).
    But RRF fusion (``search_common.fuse``, default ``rrf_k=60``) combines
    per-leg **rank**, not score magnitude: a document's contribution is
    ``1.0 / (rrf_k + rank)`` regardless of how decisive that rank-1 hit
    was. A document appearing in BOTH legs — even at mediocre ranks in
    each — can out-score a document that is a decisive #1 in exactly ONE
    leg. In a corpus with many topically-similar documents (the realistic
    case for near-duplicate incident reports), such "dual-leg rivals" are
    common, and the exact-ID lookup gets buried in the fused top-k despite
    being lexically perfect.

This module surfaces both failure modes directly against each sink's real
code path so the regression is caught if either narrows or widens. Where the
bug currently reproduces, the test asserts the CORRECT/desired behavior and
is marked ``xfail(strict=True)`` — if the underlying bug is ever fixed, the
unexpected pass (XPASS) will fail the suite and this file must be revisited.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from chunkshop import search
from chunkshop import search_sqlite
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.pipeline import Pipeline
from chunkshop.search_common import Hit, fuse


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
SCHEMA = "chunkshop_issue953_test"
TABLE = "chunks"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384

GOLD_ID = "doc_inc_0001"
GOLD_TEXT = (
    "Incident INC-0001: the payment service experienced a connection pool "
    "exhaustion at 03:14 UTC. On-call resolved it by restarting the pool "
    "manager."
)
N_DISTRACTORS = 8  # doc_inc_0002 .. doc_inc_0009 — same template, different ID


def _distractor_text(i: int) -> str:
    return (
        f"Incident INC-{i:04d}: the payment service experienced a connection "
        f"pool exhaustion at 03:14 UTC. On-call resolved it by restarting the "
        f"pool manager."
    )


def _cell_config(target: dict) -> CellConfig:
    return CellConfig(**{
        "cell_name": "issue953",
        "source": {"type": "inline"},
        "chunker": {"type": "sentence_aware", "max_chars": 500},
        "embedder": {"type": "fastembed", "model_name": MODEL, "dim": DIM},
        "target": target,
    })


# --------------------------------------------------------------------------
# Postgres
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_corpus():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()

    cfg = _cell_config({
        "type": "postgres", "dsn_env": DSN_ENV, "database": SCHEMA,
        "table": TABLE, "hnsw": False, "mode": "create_if_missing",
        "source_tag": "t",
    })
    pipe = Pipeline(cfg)
    pipe.ingest_text(GOLD_ID, GOLD_TEXT)
    for i in range(2, N_DISTRACTORS + 2):
        pipe.ingest_text(f"doc_inc_{i:04d}", _distractor_text(i))

    search.ensure_fts(dsn, schema=SCHEMA, table=TABLE)
    yield dsn

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def test_pg_tsvector_drops_hyphen_lexeme_953(pg_corpus):
    """Root cause, isolated: Postgres stores '-0001' (hyphen attached), not a
    bare '0001' lexeme — so chunkshop's stripped query token can never match it.
    """
    with psycopg.connect(pg_corpus) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_tsvector('english', 'INC-0001')::text")
        lexemes = cur.fetchone()[0]
        assert "'-0001'" in lexemes, f"expected a hyphen-attached lexeme, got {lexemes!r}"
        assert "'0001'" not in lexemes, (
            f"if Postgres ever stores a bare '0001' lexeme too, bento#953's "
            f"root cause on the index side has changed — got {lexemes!r}"
        )

        cur.execute("SELECT to_tsvector('english','INC-0001') @@ to_tsquery('english','0001')")
        assert cur.fetchone()[0] is False, (
            "chunkshop's query tokenizer (search.py _TOKEN_RE) strips the "
            "hyphen and asks to_tsquery for bare '0001' — this must NOT match "
            "the stored '-0001' lexeme, or the described mismatch has closed"
        )


@pytest.mark.xfail(
    reason="bento#953: search.py's [A-Za-z0-9]+ query tokenizer drops the "
    "hyphen, so 'INC-0001' loses its only exact-match signal ('0001' never "
    "matches the stored '-0001' lexeme) and ties with every other INC-xxxx "
    "doc on the generic 'inc' token — ts_rank cannot tell the gold doc apart",
    strict=True,
)
def test_pg_keyword_search_loses_hyphen_id_specificity_953(pg_corpus):
    hits = search.keyword_search(
        pg_corpus, schema=SCHEMA, table=TABLE, query="INC-0001", k=N_DISTRACTORS + 1
    )
    by_id = {h.doc_id: h.score for h in hits}
    assert GOLD_ID in by_id, "gold doc should be an FTS hit at all"
    gold_score = by_id[GOLD_ID]
    other_scores = [s for doc_id, s in by_id.items() if doc_id != GOLD_ID]
    assert other_scores, "expected distractor hits to compare against"
    assert gold_score > max(other_scores), (
        f"gold doc's ts_rank ({gold_score}) should be strictly higher than "
        f"every distractor's ({other_scores}) since only the gold doc's ID "
        f"exactly matches the query — instead they tie, because the '0001' "
        f"token contributes nothing (bento#953)"
    )


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sqlite_corpus(tmp_path_factory):
    pytest.importorskip("sqlite_vec")
    db_path = tmp_path_factory.mktemp("issue953_sqlite") / "chunks.db"
    dsn = str(db_path)
    dsn_env = "SQLITE_ISSUE953_TEST_PATH"
    os.environ[dsn_env] = dsn

    cfg = _cell_config({
        "type": "sqlite", "dsn_env": dsn_env, "database": "ignored",
        "table": TABLE, "hnsw": False, "mode": "create_if_missing",
        "source_tag": "t",
    })
    pipe = Pipeline(cfg)
    pipe.ingest_text(GOLD_ID, GOLD_TEXT)
    for i in range(2, N_DISTRACTORS + 2):
        pipe.ingest_text(f"doc_inc_{i:04d}", _distractor_text(i))

    search_sqlite.ensure_fts(dsn, table=TABLE)
    yield dsn


def test_sqlite_keyword_search_decisively_ranks_hyphen_id_953(sqlite_corpus):
    """Contrast case (documents the CORRECT half of #953): FTS5 tokenizes the
    index and the query the same way, so the gold doc's exclusive '0001' match
    gives it a decisive #1 rank with an overwhelming bm25 margin over every
    'inc'-only distractor. No tokenizer bug on sqlite's FTS leg.
    """
    hits = search_sqlite.keyword_search(
        sqlite_corpus, table=TABLE, query="INC-0001", k=N_DISTRACTORS + 1
    )
    assert hits and hits[0].doc_id == GOLD_ID, f"expected gold doc #1, got {hits[:3]}"
    other_scores = [h.score for h in hits[1:]]
    assert other_scores, "expected distractor hits to compare against"
    assert hits[0].score > 100 * max(other_scores), (
        f"expected an overwhelming bm25 margin (gold={hits[0].score}, "
        f"best distractor={max(other_scores)}) — this is the '~460,000x' "
        f"margin bento#953 measured; sqlite's FTS leg is NOT the broken half"
    )


@pytest.mark.xfail(
    reason="bento#953: RRF (search_common.fuse, rrf_k=60) fuses per-leg RANK, "
    "not score magnitude — a document that is a decisive #1 in exactly ONE "
    "leg (1/(60+1)) can be mathematically outranked by any document present "
    "in BOTH legs even at mediocre per-leg ranks (e.g. 1/62 + 1/79 > 1/61). "
    "A rank-1-only lexical hit is 'arithmetically un-rescuable' whenever the "
    "corpus has topically-similar dual-leg rivals, which real incident-report "
    "corpora do.",
    strict=True,
)
def test_sqlite_hybrid_rrf_buries_decisive_lexical_hit_953(sqlite_corpus):
    # Real FTS leg for the gold ID: sqlite's FTS is decisive (previous test),
    # so use the ACTUAL keyword_search output — no synthetic fts data.
    fts_hits = search_sqlite.keyword_search(
        sqlite_corpus, table=TABLE, query="INC-0001", k=N_DISTRACTORS + 1
    )
    assert fts_hits[0].doc_id == GOLD_ID

    # The semantic leg: model "N topically-similar rivals" that a realistic
    # corpus produces (bento#953 measured this on a 62-doc corpus; a small
    # unit-test fixture ties the near-duplicate distractors in embedding
    # space, which masks rather than reproduces the effect — see this repo's
    # test-build report). Constructed directly from the SAME distractor Hit
    # objects the FTS leg already returned, at semantic ranks 1..N — i.e.
    # "these documents are ALSO found by the semantic leg", while the gold
    # doc (an opaque ID reference with weak semantic signal) is not among
    # its candidates. legs is what `fuse()` actually reads.
    distractor_hits = [h for h in fts_hits if h.doc_id != GOLD_ID]
    semantic_hits = [
        Hit(
            doc_id=h.doc_id, seq_num=h.seq_num, text=h.text, score=1.0 - i * 0.01,
            metadata=h.metadata, legs=("semantic",), embedded_text=h.embedded_text,
        )
        for i, h in enumerate(distractor_hits)
    ]

    fused = fuse(
        {"fts": fts_hits, "semantic": semantic_hits},
        k=5, fusion="rrf", weights=None, rrf_k=60,
    )
    fused_ids = [h.doc_id for h in fused]
    assert GOLD_ID in fused_ids, (
        f"gold doc (decisive #1 in FTS, absent from semantic) should survive "
        f"RRF fusion into the top-5, but default rrf_k=60 buried it: {fused_ids}"
    )
