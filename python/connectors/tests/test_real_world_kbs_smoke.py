"""Smoke companion to ``connectors/examples/e2e_real_world_5kbs.py``.

The full real-world script clones GitHub repos and downloads arxiv PDFs.
The smoke version stays on loopback (so it plays nicely with the
``_block_non_loopback_sockets`` autouse guard in ``conftest.py``):

* Uses the LOCAL chunkshop checkout as the file-corpus (not a network clone).
* Generates a small in-process PDF and stages it alongside two
  hand-written .md briefs — exercises the PDF parser path without
  hitting arxiv.
* Inserts 3 rows into ClickHouse and re-ingests them via the
  ``clickhouse_table`` source (loopback-only).
* Builds three KBs (``kb_smoke_files``, ``kb_smoke_pdf``,
  ``kb_smoke_clickhouse``) and runs one hybrid_search against each.

Marked ``@pytest.mark.slow`` so it doesn't run on every ``pytest``
invocation. Tests gate themselves on Postgres + ClickHouse + local
file reachability and skip cleanly if any prerequisite is missing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Gating: skip the entire module on missing infrastructure.
# ---------------------------------------------------------------------------

PG_DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)
CH_DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN_CLICKHOUSE",
    "clickhouse://default:chpw@localhost:8124/chunkshop_test",
)
SCHEMA = "chunkshop_smoke_real_world"


def _pg_reachable() -> bool:
    try:
        import psycopg
        with psycopg.connect(PG_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


def _ch_reachable() -> bool:
    try:
        from chunkshop.backends.clickhouse import ClickHouseBackend
        with ClickHouseBackend(dsn=CH_DSN).connect() as client:
            client.query("SELECT 1")
            return True
    except Exception:
        return False


def _langdetect_available() -> bool:
    try:
        import langdetect  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Module-level skip if anything's missing — keeps each test reason clear
# when the row in the report says SKIPPED.
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _pg_reachable(),
        reason="Postgres unreachable (set $CHUNKSHOP_TEST_DSN or start docker-compose)",
    ),
    pytest.mark.skipif(
        not _ch_reachable(),
        reason="ClickHouse unreachable (set $CHUNKSHOP_TEST_DSN_CLICKHOUSE or start docker-compose)",
    ),
    pytest.mark.skipif(
        not _langdetect_available(),
        reason="langdetect not installed (run `uv pip install langdetect`)",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHUNKSHOP_ROOT = Path(__file__).resolve().parents[3]  # repo root


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Iterator[Path]:
    """Module-scoped tmp dir; teardown drops the smoke schema + CH table."""
    ws = tmp_path_factory.mktemp("real_world_smoke")
    yield ws
    # Cleanup — best effort.
    try:
        import psycopg
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            conn.commit()
    except Exception:
        pass
    try:
        from chunkshop.backends.clickhouse import ClickHouseBackend
        with ClickHouseBackend(dsn=CH_DSN).connect() as client:
            client.command("DROP TABLE IF EXISTS chunkshop_test.kb_smoke_clickhouse_seed")
    except Exception:
        pass


@pytest.fixture(scope="module")
def file_corpus(workspace: Path) -> Path:
    """Stage a small flat corpus of .md + .py from the LOCAL chunkshop repo."""
    import hashlib

    staging = workspace / "files"
    staging.mkdir(exist_ok=True)
    src_root = CHUNKSHOP_ROOT / "python" / "src" / "chunkshop"
    candidates: list[Path] = []
    for ext in (".py", ".md"):
        for p in src_root.rglob(f"*{ext}"):
            if "__pycache__" in p.parts:
                continue
            candidates.append(p)
    candidates.sort(key=lambda p: str(p))
    candidates = candidates[:30]  # tiny corpus for smoke
    for src in candidates:
        rel = src.relative_to(src_root)
        short = hashlib.sha1(str(rel).encode()).hexdigest()[:8]
        try:
            shutil.copyfile(src, staging / f"{short}_{rel.name}")
        except Exception:
            continue
    assert any(staging.iterdir()), "no files staged from local chunkshop repo"
    return staging


@pytest.fixture(scope="module")
def pdf_and_md_corpus(workspace: Path) -> Path:
    """Stage a tiny PDF (via pypdf, in-process) + 2 .md briefs.

    pypdf is also chunkshop's PDFParser dep — if it's missing, the parser
    couldn't read PDFs anyway, so we skip rather than error.
    """
    pypdf = pytest.importorskip("pypdf", reason="pypdf required for PDF generation + chunkshop PDFParser")

    pmix = workspace / "pdf_and_md"
    pmix.mkdir(exist_ok=True)

    # Two .md briefs covering retrieval / embeddings — content matters because
    # the hybrid query below is "vector embedding retrieval" and the test
    # asserts hits > 0.
    (pmix / "rag_brief.md").write_text(
        "# RAG\n\nRetrieval-augmented generation pairs a language model with "
        "an external retriever. The retriever surfaces query-conditioned "
        "passages from a vector index. Embedding models turn passages into "
        "high-dimensional vectors so an approximate nearest-neighbor search "
        "can rank them. Hybrid retrieval combines vector and keyword legs "
        "via reciprocal rank fusion.\n"
    )
    (pmix / "embeddings_brief.md").write_text(
        "# Embedding Models\n\nbge-small-en-v1.5 is a 384-dimensional "
        "embedding model that runs efficiently on CPU. It is the pragmatic "
        "open-source default for self-hosted retrieval workloads. Larger "
        "variants (base, large) offer higher recall at the cost of bigger "
        "vectors and slower throughput. Mixing embedders across one index "
        "is meaningless — vectors must come from the same model family.\n"
    )

    # Build a tiny PDF in-process so we don't touch the network.
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_path = pmix / "tiny.pdf"
    with pdf_path.open("wb") as f:
        writer.write(f)

    return pmix


# ---------------------------------------------------------------------------
# Common cell-config builder
# ---------------------------------------------------------------------------

def _build_files_cell(*, cell_name: str, glob: str, table: str, mode: str,
                     source_tag: str | None):
    os.environ["CHUNKSHOP_SMOKE_DSN"] = PG_DSN
    from chunkshop.config import (
        CellConfig,
        FastembedEmbedder,
        FilesSource,
        LangDetectExtractor,
        PromoteColumn,
        RuntimeConfig,
        SentenceAwareChunker,
        TargetConfig,
    )

    return CellConfig(
        cell_name=cell_name,
        source=FilesSource(type="files", glob=glob, id_from="stem"),
        chunker=SentenceAwareChunker(type="sentence_aware", min_chars=80, max_chars=800),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384, batch_size=32, threads=2,
        ),
        extractor=LangDetectExtractor(type="lang_detect"),
        target=TargetConfig(
            type="postgres",
            dsn_env="CHUNKSHOP_SMOKE_DSN",
            database=SCHEMA,
            table=table,
            mode=mode,
            source_tag=source_tag,
            hnsw=False,
            promote_metadata=[PromoteColumn(path="language", type="text")],
        ),
        runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=5),
    )


def _embed_query(text: str) -> np.ndarray:
    from chunkshop.config import FastembedEmbedder
    from chunkshop.embedders import load_embedder
    emb = load_embedder(FastembedEmbedder(
        type="fastembed",
        model_name="Xenova/bge-small-en-v1.5-int8",
        dim=384, batch_size=1, threads=2,
    ))
    return emb.embed([text])[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRealWorldSmoke:
    """Three KBs + 1 hybrid query each. Asserts chunks > 0 and hits > 0."""

    def test_files_kb_ingests_chunks(self, file_corpus: Path):
        """File-corpus KB ingests > 0 chunks and the language tag promotes."""
        from chunkshop.runner import run_cell
        from chunkshop import search
        import psycopg

        cfg = _build_files_cell(
            cell_name="smoke_files",
            glob=str(file_corpus / "*"),
            table="kb_smoke_files",
            mode="overwrite",
            source_tag=None,
        )
        res = run_cell(cfg)
        assert res.error is None, f"files cell errored: {res.error}"
        assert res.chunks_written > 0
        assert res.docs_processed > 0

        # FTS index for the hybrid leg.
        search.ensure_fts(PG_DSN, schema=SCHEMA, table="kb_smoke_files", language="english")

        # Language column was promoted.
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s",
                (SCHEMA, "kb_smoke_files"),
            )
            cols = {r[0] for r in cur.fetchall()}
            assert "language" in cols, f"language column not promoted; got {cols}"

    def test_pdf_and_md_kb_ingests_chunks(self, pdf_and_md_corpus: Path):
        """Mixed PDF + .md corpus exercises the PDF parser path."""
        from chunkshop.runner import run_cell
        from chunkshop import search

        cfg = _build_files_cell(
            cell_name="smoke_pdf_md",
            glob=str(pdf_and_md_corpus / "*"),
            table="kb_smoke_pdf",
            mode="overwrite",
            source_tag=None,
        )
        res = run_cell(cfg)
        assert res.error is None, f"pdf cell errored: {res.error}"
        # The blank PDF emits no text; the two .md briefs do. Just assert > 0 chunks.
        assert res.chunks_written > 0
        search.ensure_fts(PG_DSN, schema=SCHEMA, table="kb_smoke_pdf", language="english")

    def test_clickhouse_seed_ingests_into_pgvector(self):
        """ClickHouse rows → chunkshop's clickhouse_table source → pgvector."""
        from chunkshop.backends.clickhouse import ClickHouseBackend
        from chunkshop.config import (
            CellConfig,
            ClickhouseTableSource,
            FastembedEmbedder,
            LangDetectExtractor,
            PromoteColumn,
            RuntimeConfig,
            SentenceAwareChunker,
            TargetConfig,
        )
        from chunkshop.runner import run_cell
        from chunkshop import search

        # Seed 3 rows.
        rows = [
            ("smk_1", "Vector Search",
             "Vector search uses dense embeddings to retrieve semantically "
             "similar passages from a corpus. Approximate nearest neighbor "
             "indexes like HNSW make this fast at scale."),
            ("smk_2", "Reciprocal Rank Fusion",
             "Reciprocal rank fusion combines multiple ranked lists by "
             "summing 1/(k+rank) contributions per leg. It is the default "
             "hybrid-search fusion strategy because it does not require "
             "score normalization."),
            ("smk_3", "Chunking",
             "Chunk boundaries determine the granularity of retrieval. "
             "Sentence-aware chunkers split on sentence boundaries; "
             "hierarchy chunkers split on section structure."),
        ]
        with ClickHouseBackend(dsn=CH_DSN).connect() as client:
            client.command("DROP TABLE IF EXISTS chunkshop_test.kb_smoke_clickhouse_seed")
            client.command(
                "CREATE TABLE chunkshop_test.kb_smoke_clickhouse_seed ("
                "  id String, title String, content String, "
                "  updated_at DateTime DEFAULT now()"
                ") ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                "chunkshop_test.kb_smoke_clickhouse_seed",
                rows,
                column_names=["id", "title", "content"],
            )

        os.environ["CHUNKSHOP_SMOKE_DSN"] = PG_DSN
        os.environ["CHUNKSHOP_SMOKE_CH_DSN"] = CH_DSN
        cfg = CellConfig(
            cell_name="smoke_clickhouse",
            source=ClickhouseTableSource(
                type="clickhouse_table",
                dsn_env="CHUNKSHOP_SMOKE_CH_DSN",
                database="chunkshop_test",
                table="kb_smoke_clickhouse_seed",
                id_column="id",
                content_column="content",
                title_column="title",
            ),
            chunker=SentenceAwareChunker(type="sentence_aware", min_chars=50, max_chars=600),
            embedder=FastembedEmbedder(
                type="fastembed",
                model_name="Xenova/bge-small-en-v1.5-int8",
                dim=384, batch_size=8, threads=2,
            ),
            extractor=LangDetectExtractor(type="lang_detect"),
            target=TargetConfig(
                type="postgres",
                dsn_env="CHUNKSHOP_SMOKE_DSN",
                database=SCHEMA,
                table="kb_smoke_clickhouse",
                mode="overwrite",
                hnsw=False,
                promote_metadata=[PromoteColumn(path="language", type="text")],
            ),
            runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=5),
        )
        res = run_cell(cfg)
        assert res.error is None, f"clickhouse cell errored: {res.error}"
        assert res.docs_processed == 3
        assert res.chunks_written > 0
        search.ensure_fts(PG_DSN, schema=SCHEMA, table="kb_smoke_clickhouse", language="english")

    def test_hybrid_search_returns_hits_across_kbs(
        self, file_corpus: Path, pdf_and_md_corpus: Path
    ):
        """Single hybrid query (semantic + FTS, RRF) hits >=1 of the 3 KBs.

        Depends on the three ingest tests above — they're module-scoped and
        run in declaration order under pytest's default collection.
        """
        from chunkshop import search

        # Files corpus is .py + chunkshop docs — chance of zero hits is real.
        # PDF corpus has the .md briefs about RAG — strong hits expected.
        # CH corpus has explicit retrieval/RRF/chunking content — strong hits.
        query = "vector embedding retrieval"
        qvec = _embed_query(query)

        kb_hit_counts: dict[str, int] = {}
        for table in ("kb_smoke_files", "kb_smoke_pdf", "kb_smoke_clickhouse"):
            hits = search.hybrid_search(
                PG_DSN,
                schema=SCHEMA,
                table=table,
                query=query,
                query_vec=qvec,
                k=5,
                legs=("semantic", "fts"),
                fusion="rrf",
            )
            kb_hit_counts[table] = len(hits)

        # At least one KB should return hits. The two corpora with explicit
        # retrieval content (pdf+md, ch) almost certainly do.
        total = sum(kb_hit_counts.values())
        assert total > 0, f"no hybrid hits across any KB: {kb_hit_counts}"

        # Specifically: the PDF+MD KB and the ClickHouse KB both contain text
        # about vectors and retrieval — at least one must hit.
        assert (kb_hit_counts["kb_smoke_pdf"] > 0
                or kb_hit_counts["kb_smoke_clickhouse"] > 0), (
            f"retrieval-relevant corpora produced no hits: {kb_hit_counts}"
        )
