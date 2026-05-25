#!/usr/bin/env python3
"""# Demo: Full Source → Chunker → Embedder → Sink pipeline (real Postgres)

End-to-end pipeline against the chunkshop test Postgres
(``localhost:5434``):

  - **Source**: ``FilesSource`` over the docs/samples markdown fixtures
  - **Chunker**: ``sentence_aware`` (prose splitter, tight chunks)
  - **Embedder**: ``fastembed`` Xenova/bge-small-en-v1.5-int8 (~30 MB, fastest)
  - **Sink**: pgvector against ``chunkshop_test``, table
    ``e2e_pipeline_demo`` (overwrite mode — table is dropped at end)

First-run note: fastembed downloads the model (~30 MB, ~1 min). The
script prints a "loading model..." status so the user knows it isn't
hung. fastembed caches the model in ``~/.cache/fastembed`` by default
so re-runs are instant.

Run:
    python e2e_pipeline_full.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _bootstrap_repo_imports() -> None:
    """Self-bootstrap for raw `python e2e_*.py` runs in-repo."""
    here = Path(__file__).resolve()
    for d in (here.parents[1] / "src", here.parents[2] / "src"):
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))


_bootstrap_repo_imports()

# Default DSN must match the chunkshop test stack (`docker-compose.test.yaml`).
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: Full pipeline — files → sentence_aware → fastembed → pgvector")
    print("=" * 72)


def _reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        print("  psycopg is not installed; cannot demo the pgvector sink.", file=sys.stderr)
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception as exc:
        print(f"  Postgres at {dsn} is unreachable: {exc}", file=sys.stderr)
        return False


def _resolve_glob() -> str:
    # Demos run from any cwd; resolve docs/samples relative to the repo root.
    here = Path(__file__).resolve()
    # python/connectors/examples/e2e_pipeline_full.py  →  repo root is parents[3]
    repo_root = here.parents[3]
    glob = repo_root / "docs" / "samples" / "*-*.md"
    return str(glob)


def main() -> int:
    _print_banner()
    dsn = os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN)
    print(f"  DSN: {dsn}")
    if not _reachable(dsn):
        print("  -> skipping demo. Start with `docker compose -f docker-compose.test.yaml up -d`.")
        return 0

    samples_glob = _resolve_glob()
    print(f"  samples glob: {samples_glob}")
    if not list(Path(samples_glob).parent.glob(Path(samples_glob).name)):
        print(f"  -> no files matched {samples_glob}; cannot demo.", file=sys.stderr)
        return 0

    # Pass DSN to the sink via env var since CHUNKSHOP_TEST_DSN may differ.
    os.environ.setdefault("CHUNKSHOP_DEMO_DSN", dsn)

    from chunkshop.config import (
        CellConfig,
        FastembedEmbedder,
        FilesSource,
        NoneExtractor,
        RuntimeConfig,
        SentenceAwareChunker,
        TargetConfig,
    )
    from chunkshop.runner import run_cell

    cfg = CellConfig(
        cell_name="e2e_pipeline_demo",
        source=FilesSource(type="files", glob=samples_glob, id_from="stem"),
        chunker=SentenceAwareChunker(type="sentence_aware", min_chars=200, max_chars=1200),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=2,
        ),
        extractor=NoneExtractor(type="none"),
        target=TargetConfig(
            type="postgres",
            dsn_env="CHUNKSHOP_DEMO_DSN",
            database="chunkshop_e2e_demo",
            table="pipeline_demo",
            mode="overwrite",
            hnsw=False,  # 4 docs is too small for HNSW to beat seq scan
        ),
        runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=5),
    )

    print("\n  loading fastembed model (Xenova/bge-small-en-v1.5-int8; ~30 MB on first run)...")
    t0 = time.time()
    result = run_cell(cfg)
    print(f"  run_cell wall time: {time.time() - t0:.1f}s")
    print(
        f"\n  cell={result.cell_name!r} docs={result.docs_processed} "
        f"chunks={result.chunks_written} embed_seconds={result.embed_seconds:.2f}"
    )
    if result.error:
        print(f"  ERROR during ingest: {result.error}", file=sys.stderr)
        return 1

    # Query the sink to verify chunks landed, then drop the table.
    import psycopg

    table_q = '"chunkshop_e2e_demo"."pipeline_demo"'
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*), count(distinct doc_id) FROM {table_q}")
        nchunks, ndocs = cur.fetchone()
        print(f"\n  pgvector SELECT: {nchunks} chunk row(s) across {ndocs} document(s)")
        cur.execute(f"SELECT doc_id, seq_num, length(original_content) FROM {table_q} ORDER BY doc_id, seq_num LIMIT 5")
        rows = cur.fetchall()
        print("  first 5 rows:")
        for doc_id, seq, n in rows:
            print(f"    {doc_id!r:32}  seq={seq:<3}  len={n}")
        # Cleanup
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_e2e_demo CASCADE")
        conn.commit()
        print("\n  cleanup: dropped schema chunkshop_e2e_demo")

    print("\n  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
