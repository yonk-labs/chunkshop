"""Structural test for the two-KB pattern (code + docs side by side).

Asserts that you CAN configure two cells with `symbol_aware` and
`sentence_aware` chunkers respectively, targeting different tables in
the same schema and sharing the same embedder, and that:

  * Both target tables co-exist without colliding on metadata schemas.
  * Each table holds the expected row counts.
  * ``hybrid_search`` against either table returns hits when content
    matches.

This is the test analog of ``examples/code_and_docs_kbs_demo.py``,
shrunk to 3 synthetic .py files and 3 synthetic .md files in a
``tmp_path`` so the suite stays under a minute. The real fastembed
bge-small model is used (already cached on dev boxes); the test
``@pytest.mark.slow`` so plain ``pytest`` runs skip it and CI opts in
via ``pytest -m slow``.

Skips cleanly if Postgres is unreachable or fastembed/lede/langdetect
aren't installed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
TEST_SCHEMA = "chunkshop_two_kb_test"
CODE_TABLE = "kb_code"
DOCS_TABLE = "kb_docs"


# ---------------------------------------------------------------------------
# Synthetic corpus
# ---------------------------------------------------------------------------


_PY_FIXTURES: dict[str, str] = {
    "alpha.py": (
        '"""Module alpha — text retrieval helpers."""\n'
        "import json\n"
        "\n"
        "def search_documents(query: str, k: int = 5) -> list[dict]:\n"
        '    """Return top-k document records matching the query."""\n'
        "    return [{'doc_id': i, 'score': 1.0 / (i + 1)} for i in range(k)]\n"
        "\n"
        "def normalize_score(score: float, min_score: float, max_score: float) -> float:\n"
        '    """Min-max-normalize a single score to [0, 1]."""\n'
        "    if max_score == min_score:\n"
        "        return 0.0\n"
        "    return (score - min_score) / (max_score - min_score)\n"
    ),
    "beta.py": (
        '"""Module beta — cursor handling for incremental sync."""\n'
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class Cursor:\n"
        '    """An opaque cursor for paginating a remote source."""\n'
        "    token: str = ''\n"
        "    page: int = 0\n"
        "\n"
        "def advance_cursor(cursor: Cursor, page_size: int) -> Cursor:\n"
        '    """Move the cursor forward by one page worth of documents."""\n'
        "    return Cursor(token=cursor.token, page=cursor.page + 1)\n"
    ),
    "gamma.py": (
        '"""Module gamma — fusion of multiple retrieval legs."""\n'
        "\n"
        "def rrf_fuse(rank_lists: list[list[str]], rrf_k: int = 60) -> list[str]:\n"
        '    """Reciprocal Rank Fusion across multiple ranked lists."""\n'
        "    scores: dict[str, float] = {}\n"
        "    for legs in rank_lists:\n"
        "        for rank, doc_id in enumerate(legs, start=1):\n"
        "            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)\n"
        "    return sorted(scores, key=lambda d: scores[d], reverse=True)\n"
    ),
}

_MD_FIXTURES: dict[str, str] = {
    "search-guide.md": (
        "# Searching the document store\n"
        "\n"
        "The retrieval API exposes three legs: a semantic vector search backed by pgvector, "
        "a full-text search backed by Postgres tsvector, and a hybrid fusion that combines them.\n"
        "\n"
        "To search the document store, supply a natural-language query and a top-k argument. "
        "The library returns ranked records ordered by fused score, with the leg provenance carried alongside.\n"
        "\n"
        "## Tuning the legs\n"
        "\n"
        "Reciprocal Rank Fusion weights each leg equally by default. Tune the weights when "
        "one leg is consistently noisier than the other.\n"
    ),
    "cursors.md": (
        "# Cursor semantics\n"
        "\n"
        "Cursor handling for incremental sync is conceptually similar to pagination tokens, "
        "but with provenance: the cursor identifies the last document the consumer observed, "
        "so the source can resume without re-emitting documents the consumer has already seen.\n"
        "\n"
        "When a cursor advances, the source guarantees forward progress: a stale cursor "
        "raises StaleCursorError so consumers can fall back to a full resync.\n"
    ),
    "embedders.md": (
        "# Embedders\n"
        "\n"
        "An embedder maps a chunk of text to a fixed-dimensional vector. The bundled fastembed "
        "embedder ships int8-quantized variants of bge-small and bge-base; both produce 384 and "
        "768 dimensions respectively.\n"
        "\n"
        "Sharing an embedder across multiple knowledge bases means every chunk lives in the same "
        "vector space, which lets you search across tables with one query and a merge step.\n"
    ),
}


def _write_fixtures(tmp_path: Path) -> Path:
    repo = tmp_path / "fake_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    for name, body in _PY_FIXTURES.items():
        (repo / "src" / name).write_text(body)
    for name, body in _MD_FIXTURES.items():
        (repo / "docs" / name).write_text(body)
    return repo


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


def _maybe_skip() -> str:
    psycopg = pytest.importorskip("psycopg")
    pytest.importorskip("fastembed")
    pytest.importorskip("lede")
    pytest.importorskip("langdetect")
    pytest.importorskip("rake_nltk")
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    return dsn


# ---------------------------------------------------------------------------
# Cell builders (mirror the demo but trimmed for tests)
# ---------------------------------------------------------------------------


def _code_cell(repo: Path, dsn_env: str, source_tag: str):
    from chunkshop.config import (
        CellConfig,
        CodeRelationshipsExtractor,
        CodeSummaryExtractor,
        CompositeExtractor,
        FastembedEmbedder,
        FilesSource,
        PromoteColumn,
        RuntimeConfig,
        SymbolAwareChunker,
        TargetConfig,
    )

    return CellConfig(
        cell_name=f"kb_code__{source_tag}",
        source=FilesSource(
            type="files",
            glob=str(repo / "src" / "*.py"),
            id_from="path",
        ),
        chunker=SymbolAwareChunker(
            type="symbol_aware",
            granularity="function",
            include_imports=True,
        ),
        extractor=CompositeExtractor(
            type="composite",
            extractors=[
                CodeSummaryExtractor(
                    type="code_summary",
                    backend="lede",
                    max_length=200,
                ),
                CodeRelationshipsExtractor(type="code_relationships"),
            ],
        ),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=32,
            threads=2,
        ),
        target=TargetConfig(
            type="postgres",
            dsn_env=dsn_env,
            database=TEST_SCHEMA,
            table=CODE_TABLE,
            mode="create_if_missing",
            source_tag=source_tag,
            hnsw=False,
            promote_metadata=[
                PromoteColumn(path="symbol_name", type="text"),
                PromoteColumn(path="fqn", type="text"),
                PromoteColumn(path="symbol_type", type="text"),
                PromoteColumn(path="language", type="text"),
                PromoteColumn(path="summary", type="text"),
                PromoteColumn(path="start_line", type="int"),
                PromoteColumn(path="end_line", type="int"),
            ],
        ),
        runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=50),
    )


def _docs_cell(repo: Path, dsn_env: str, source_tag: str):
    from chunkshop.config import (
        CellConfig,
        CompositeExtractor,
        FastembedEmbedder,
        FilesSource,
        LangDetectExtractor,
        PromoteColumn,
        RakeKeywordsExtractor,
        RuntimeConfig,
        SentenceAwareChunker,
        TargetConfig,
    )

    return CellConfig(
        cell_name=f"kb_docs__{source_tag}",
        source=FilesSource(
            type="files",
            glob=str(repo / "docs" / "*.md"),
            id_from="path",
        ),
        chunker=SentenceAwareChunker(
            type="sentence_aware",
            min_chars=150,
            max_chars=900,
        ),
        extractor=CompositeExtractor(
            type="composite",
            extractors=[
                LangDetectExtractor(type="lang_detect"),
                RakeKeywordsExtractor(
                    type="rake_keywords", top_k=6, min_chars=4
                ),
            ],
        ),
        embedder=FastembedEmbedder(
            # Same embedder as kb_code -> same vector space.
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=32,
            threads=2,
        ),
        target=TargetConfig(
            type="postgres",
            dsn_env=dsn_env,
            database=TEST_SCHEMA,
            table=DOCS_TABLE,
            mode="create_if_missing",
            source_tag=source_tag,
            hnsw=False,
            promote_metadata=[
                PromoteColumn(path="language", type="text"),
                PromoteColumn(path="source_path", type="text"),
            ],
        ),
        runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=50),
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_two_kb_pattern_co_exists_and_searches(tmp_path):
    """Two cells with different chunkers + same embedder + same schema -> two
    co-existing tables, both queryable, joint search works.
    """
    dsn = _maybe_skip()
    import psycopg

    # Ensure clean schema before / after.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        conn.commit()

    try:
        repo = _write_fixtures(tmp_path)

        os.environ[DSN_ENV] = dsn
        from chunkshop.runner import run_cell

        # --- Cell 1: code (one cell over 3 .py files) ----------------
        code_res = run_cell(_code_cell(repo, DSN_ENV, "code_py"))
        assert code_res.error is None, code_res.error
        assert code_res.docs_processed == 3
        # 3 files * ~2 top-level symbols each -> at least 5 chunks
        # (alpha: 2 fns, beta: 1 dataclass + 1 fn, gamma: 1 fn).
        assert code_res.chunks_written >= 5, code_res.chunks_written

        # --- Cell 2: docs (one cell over 3 .md files) ----------------
        docs_res = run_cell(_docs_cell(repo, DSN_ENV, "docs_md"))
        assert docs_res.error is None, docs_res.error
        assert docs_res.docs_processed == 3
        assert docs_res.chunks_written >= 3, docs_res.chunks_written

        # --- Co-existence: both tables exist with the expected
        # promoted columns and the expected source_tag rows. ----------
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY column_name",
                (TEST_SCHEMA, CODE_TABLE),
            )
            code_cols = {r[0] for r in cur.fetchall()}
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY column_name",
                (TEST_SCHEMA, DOCS_TABLE),
            )
            docs_cols = {r[0] for r in cur.fetchall()}

        # Promoted code-side columns are present.
        for col in (
            "symbol_name", "fqn", "symbol_type",
            "language", "summary", "start_line", "end_line",
        ):
            assert col in code_cols, f"missing promoted col {col!r} on {CODE_TABLE}: {code_cols}"

        # Promoted docs-side columns are present.
        for col in ("language", "source_path"):
            assert col in docs_cols, f"missing promoted col {col!r} on {DOCS_TABLE}: {docs_cols}"

        # Code-side columns DON'T leak onto the docs table (no schema collision):
        # symbol_name / fqn / symbol_type / start_line / end_line / summary
        # are kb_code-specific. The shared `language` column is fine.
        for col in ("symbol_name", "fqn", "symbol_type", "start_line", "end_line", "summary"):
            assert col not in docs_cols, (
                f"docs table unexpectedly has kb_code column {col!r}"
            )

        # Row counts match the run summary.
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{TEST_SCHEMA}"."{CODE_TABLE}"')
            n_code = cur.fetchone()[0]
            cur.execute(f'SELECT count(*) FROM "{TEST_SCHEMA}"."{DOCS_TABLE}"')
            n_docs = cur.fetchone()[0]
        assert n_code == code_res.chunks_written
        assert n_docs == docs_res.chunks_written

        # --- Search side: ensure_fts + hybrid_search against both ----
        from chunkshop.config import FastembedEmbedder
        from chunkshop.embedders import load_embedder
        from chunkshop.search import ensure_fts, hybrid_search

        ensure_fts(dsn, schema=TEST_SCHEMA, table=CODE_TABLE)
        ensure_fts(dsn, schema=TEST_SCHEMA, table=DOCS_TABLE)

        embedder = load_embedder(FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=32,
            threads=2,
        ))

        # Query 1: targets code (symbol-bounded chunks live there).
        q_code = "advance cursor pagination token"
        qv_code = embedder.embed([q_code])[0]
        code_hits = hybrid_search(
            dsn, schema=TEST_SCHEMA, table=CODE_TABLE,
            query=q_code, query_vec=qv_code, k=5,
        )
        assert code_hits, "expected at least one hit against kb_code"
        # Top hit should be a symbol_aware chunk (symbol_name set).
        top_code = code_hits[0]
        assert (top_code.metadata or {}).get("symbol_name"), (
            f"kb_code top hit lacks symbol_name: {top_code.metadata!r}"
        )

        # Query 2: targets docs (prose chunks live there).
        q_docs = "reciprocal rank fusion tune leg weights"
        qv_docs = embedder.embed([q_docs])[0]
        docs_hits = hybrid_search(
            dsn, schema=TEST_SCHEMA, table=DOCS_TABLE,
            query=q_docs, query_vec=qv_docs, k=5,
        )
        assert docs_hits, "expected at least one hit against kb_docs"
        # Top hit should be a sentence_aware chunk with source_path metadata.
        top_docs = docs_hits[0]
        meta_docs = top_docs.metadata or {}
        assert meta_docs.get("source_path"), (
            f"kb_docs top hit lacks source_path: {meta_docs!r}"
        )
        assert not meta_docs.get("symbol_name"), (
            f"kb_docs hit unexpectedly has symbol_name: {meta_docs!r}"
        )

        # Query 3: joint search across both tables — both KBs return hits
        # in their own ranked lists; the consumer merges.
        q_both = "search documents fuse ranking"
        qv_both = embedder.embed([q_both])[0]
        a = hybrid_search(
            dsn, schema=TEST_SCHEMA, table=CODE_TABLE,
            query=q_both, query_vec=qv_both, k=3,
        )
        b = hybrid_search(
            dsn, schema=TEST_SCHEMA, table=DOCS_TABLE,
            query=q_both, query_vec=qv_both, k=3,
        )
        # Both legs return something (this query string overlaps both fixtures).
        assert a, "joint search: expected hits on kb_code"
        assert b, "joint search: expected hits on kb_docs"
    finally:
        # Clean up — drop schema either way.
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
            conn.commit()
