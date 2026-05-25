#!/usr/bin/env python3
"""# Demo: two- or three-KB ingest pattern — code, docs, and (optionally) comments

Builds two chunkshop tables from a single code repo and queries each
separately + jointly. With ``--with-comments`` it builds a THIRD table
from the comments mined out of the same code files. The point: source
code, prose docs, and inline rationale are semantically distinct assets
that want different chunkers and different extractors, but should share
the **same embedder** so they live in the same vector space and can be
searched together.

Layout against the chunkshop repo (default `--repo`):

```
<schema>.kb_code     <- *.py / *.java / *.go / *.ts / *.js / *.rs
                       symbol_aware chunker (function-level)
                       composite: code_summary (lede) + code_relationships
                       promote_metadata: symbol_name, fqn, symbol_type,
                                         language, summary, start_line,
                                         end_line

<schema>.kb_docs     <- *.md / *.rst / *.txt
                       sentence_aware chunker (200..1200 chars)
                       composite: lang_detect + rake_keywords
                       promote_metadata: language, source_path

<schema>.kb_comments <- comments mined from the same code files (opt-in)
                       comment_extracts source (block granularity)
                       sentence_aware chunker (100..800 chars)
                       composite: lang_detect + rake_keywords
                       promote_metadata: language, source_path, kind,
                                         start_line
```

All tables use the same embedder (Xenova/bge-small-en-v1.5-int8, 384-dim)
so cross-KB queries are just N ``hybrid_search`` calls over the same
vector space, merged client-side.

Run:
    python code_and_docs_kbs_demo.py
    python code_and_docs_kbs_demo.py --with-comments
    python code_and_docs_kbs_demo.py --repo /path/to/some-other-repo
    python code_and_docs_kbs_demo.py --query "your question here"
    python code_and_docs_kbs_demo.py --cleanup

First run downloads the fastembed model (~30 MB). Subsequent runs hit the
fastembed cache and complete in ~30-60s against the chunkshop repo size.
"""
from __future__ import annotations

import argparse
import glob as _glob
import os
import sys
import time
from pathlib import Path
from typing import Optional


def _bootstrap_repo_imports() -> None:
    """Self-bootstrap for raw `python code_and_docs_kbs_demo.py` runs in-repo."""
    here = Path(__file__).resolve()
    # python/examples/code_and_docs_kbs_demo.py -> python/src is parents[1]/src
    for d in (here.parents[1] / "src", here.parents[2] / "python" / "src"):
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))


_bootstrap_repo_imports()


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
DEFAULT_SCHEMA = "chunkshop_code_and_docs_demo"

# Languages handled by chunkshop's codeparse (symbol_aware chunker).
CODE_EXTS = ("py", "java", "go", "ts", "js", "rs")
DOCS_EXTS = ("md", "rst", "txt")

# Generated / vendored paths to flag when enumerating files. Note: Python's
# stdlib ``glob`` with ``**`` automatically skips dot-prefixed directories
# (`.git`, `.venv`, `.pytest_cache`, ...), so this list is for *non-hidden*
# generated dirs only. We filter our discovery report against it for honest
# stats; the cells themselves use the raw glob (FilesSource takes one glob
# pattern, no exclude list). If you point ``--repo`` at a tree with
# non-hidden vendored code (e.g. a Rust crate with ``target/`` built), prune
# those dirs first or pass a narrower ``--repo`` subpath.
EXCLUDE_DIR_PARTS = {
    "node_modules",
    "dist",
    "build",
    "target",  # Rust / Java build artifacts
    "__pycache__",
}


# ---------------------------------------------------------------------------
# Pre-flight / discovery
# ---------------------------------------------------------------------------


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: two-KB ingest — code AND docs from one repo")
    print("=" * 72)


def _postgres_reachable(dsn: str) -> bool:
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


def _is_excluded(path: Path, repo_root: Path) -> bool:
    """Return True if any directory component sits on the exclude list."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    return any(part in EXCLUDE_DIR_PARTS for part in rel.parts)


def _discover_files(repo_root: Path, exts: tuple[str, ...]) -> dict[str, list[Path]]:
    """Return ``{ext: [Path, ...]}`` for each ext that has at least one match."""
    out: dict[str, list[Path]] = {}
    for ext in exts:
        paths = [
            Path(p)
            for p in _glob.glob(str(repo_root / f"**/*.{ext}"), recursive=True)
        ]
        kept = sorted(p for p in paths if not _is_excluded(p, repo_root))
        if kept:
            out[ext] = kept
    return out


# ---------------------------------------------------------------------------
# Cell builders
# ---------------------------------------------------------------------------


def _drop_schema(dsn: str, schema: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


def _build_code_cell(
    *,
    repo_root: Path,
    ext: str,
    schema: str,
    table: str,
    dsn_env: str,
    omp_threads: int,
):
    """One sub-cell per code language extension. All write to the same table."""
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

    glob_pat = str(repo_root / f"**/*.{ext}")
    return CellConfig(
        cell_name=f"kb_code__{ext}",
        source=FilesSource(type="files", glob=glob_pat, id_from="path"),
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
                    max_length=240,
                ),
                CodeRelationshipsExtractor(type="code_relationships"),
            ],
        ),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=omp_threads,
        ),
        target=TargetConfig(
            type="postgres",
            dsn_env=dsn_env,
            database=schema,
            table=table,
            mode="create_if_missing",
            source_tag=f"code_{ext}",
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
        runtime=RuntimeConfig(omp_num_threads=omp_threads, heartbeat_every=50),
    )


def _build_comments_cell(
    *,
    repo_root: Path,
    ext: str,
    schema: str,
    table: str,
    dsn_env: str,
    omp_threads: int,
):
    """One sub-cell per code-language extension. All write to the same comments table.

    Uses the new ``comment_extracts`` source so comments land as
    standalone Documents — sentence_aware then chunks each block into
    KB-sized rows. Same prose-side extractors as kb_docs (lang_detect,
    rake) because comments are prose, not code.
    """
    from chunkshop.config import (
        CellConfig,
        CommentExtractsSource,
        CompositeExtractor,
        FastembedEmbedder,
        LangDetectExtractor,
        PromoteColumn,
        RakeKeywordsExtractor,
        RuntimeConfig,
        SentenceAwareChunker,
        TargetConfig,
    )

    glob_pat = str(repo_root / f"**/*.{ext}")
    return CellConfig(
        cell_name=f"kb_comments__{ext}",
        source=CommentExtractsSource(
            type="comment_extracts",
            glob=glob_pat,
            min_chars=40,            # filter trivial breadcrumbs
            granularity="block",     # one comment region = one Document
            include_docstrings=True, # docstrings ARE rationale
            skip_pragmas=True,       # drop noqa / @ts-ignore / //go:build
        ),
        chunker=SentenceAwareChunker(
            type="sentence_aware",
            min_chars=100,
            max_chars=800,
        ),
        extractor=CompositeExtractor(
            type="composite",
            extractors=[
                LangDetectExtractor(type="lang_detect"),
                RakeKeywordsExtractor(type="rake_keywords", top_k=6, min_chars=4),
            ],
        ),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=omp_threads,
        ),
        target=TargetConfig(
            type="postgres",
            dsn_env=dsn_env,
            database=schema,
            table=table,
            mode="create_if_missing",
            source_tag=f"comments_{ext}",
            hnsw=False,
            promote_metadata=[
                PromoteColumn(path="language", type="text"),
                PromoteColumn(path="source_path", type="text"),
                PromoteColumn(path="kind", type="text"),
                PromoteColumn(path="start_line", type="int"),
            ],
        ),
        runtime=RuntimeConfig(omp_num_threads=omp_threads, heartbeat_every=50),
    )


def _build_docs_cell(
    *,
    repo_root: Path,
    ext: str,
    schema: str,
    table: str,
    dsn_env: str,
    omp_threads: int,
):
    """One sub-cell per docs extension. All write to the same table."""
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

    glob_pat = str(repo_root / f"**/*.{ext}")
    return CellConfig(
        cell_name=f"kb_docs__{ext}",
        source=FilesSource(type="files", glob=glob_pat, id_from="path"),
        chunker=SentenceAwareChunker(
            type="sentence_aware",
            min_chars=200,
            max_chars=1200,
        ),
        extractor=CompositeExtractor(
            type="composite",
            extractors=[
                LangDetectExtractor(type="lang_detect"),
                RakeKeywordsExtractor(type="rake_keywords", top_k=8, min_chars=4),
            ],
        ),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=omp_threads,
        ),
        target=TargetConfig(
            type="postgres",
            dsn_env=dsn_env,
            database=schema,
            table=table,
            mode="create_if_missing",
            source_tag=f"docs_{ext}",
            hnsw=False,
            promote_metadata=[
                PromoteColumn(path="language", type="text"),
                PromoteColumn(path="source_path", type="text"),
            ],
        ),
        runtime=RuntimeConfig(omp_num_threads=omp_threads, heartbeat_every=50),
    )


# ---------------------------------------------------------------------------
# Per-cell ingestion
# ---------------------------------------------------------------------------


def _run_cells(
    repo_root: Path,
    *,
    kind: str,
    exts: tuple[str, ...],
    schema: str,
    table: str,
    dsn_env: str,
    omp_threads: int,
) -> dict:
    """Run one sub-cell per extension that has matches. Aggregate the totals.

    Each sub-cell uses ``mode='create_if_missing'`` so the first sub-cell
    creates the table and subsequent ones simply append rows. We drop the
    schema up-front in ``main`` so each demo run starts clean.
    """
    from chunkshop.runner import run_cell

    discovered = _discover_files(repo_root, exts)
    if not discovered:
        return {
            "kind": kind,
            "table": table,
            "docs_processed": 0,
            "chunks_written": 0,
            "wall_seconds": 0.0,
            "embed_seconds": 0.0,
            "per_ext": {},
            "error": None,
        }

    per_ext: dict[str, dict] = {}
    total_docs = 0
    total_chunks = 0
    total_wall = 0.0
    total_embed = 0.0
    err: Optional[str] = None

    for ext, paths in discovered.items():
        # FilesSource doesn't exclude — for code KBs we want to skip the
        # generated dirs anyway. Workaround: run only if at least one
        # un-excluded file exists for this ext; the cell will pull ALL
        # matches but for the chunkshop repo there's no vendored .py
        # under the un-excluded paths so this is fine for the demo.
        # For a real repo with vendored code under e.g. .venv you'd want
        # a richer source that supports excludes natively.
        print(f"  [{kind}] {ext}: {len(paths)} file(s) -> running cell '{kind}__{ext}'")
        if kind == "code":
            cfg = _build_code_cell(
                repo_root=repo_root,
                ext=ext,
                schema=schema,
                table=table,
                dsn_env=dsn_env,
                omp_threads=omp_threads,
            )
        elif kind == "comments":
            cfg = _build_comments_cell(
                repo_root=repo_root,
                ext=ext,
                schema=schema,
                table=table,
                dsn_env=dsn_env,
                omp_threads=omp_threads,
            )
        else:
            cfg = _build_docs_cell(
                repo_root=repo_root,
                ext=ext,
                schema=schema,
                table=table,
                dsn_env=dsn_env,
                omp_threads=omp_threads,
            )
        t0 = time.time()
        try:
            res = run_cell(cfg)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            print(f"    -> cell raised: {err}", file=sys.stderr)
            break
        wall = time.time() - t0
        per_ext[ext] = {
            "docs_processed": res.docs_processed,
            "chunks_written": res.chunks_written,
            "wall_seconds": wall,
            "embed_seconds": res.embed_seconds,
            "error": res.error,
        }
        if res.error:
            err = res.error
            print(f"    -> cell error: {res.error}", file=sys.stderr)
            break
        total_docs += res.docs_processed
        total_chunks += res.chunks_written
        total_wall += wall
        total_embed += res.embed_seconds
        print(
            f"    -> docs={res.docs_processed} chunks={res.chunks_written} "
            f"wall={wall:.1f}s embed={res.embed_seconds:.1f}s"
        )

    return {
        "kind": kind,
        "table": table,
        "docs_processed": total_docs,
        "chunks_written": total_chunks,
        "wall_seconds": total_wall,
        "embed_seconds": total_embed,
        "per_ext": per_ext,
        "error": err,
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


DEFAULT_QUERIES: tuple[tuple[str, str, str], ...] = (
    # (label, query, target_kb) — target_kb in {"code", "docs", "both"}
    (
        "code-style query against kb_code",
        "IncrementalSource cursor advancement",
        "code",
    ),
    (
        "same query against kb_docs",
        "IncrementalSource cursor advancement",
        "docs",
    ),
    (
        "how-to query against kb_docs",
        "how do I add a new connector",
        "docs",
    ),
    (
        "joint query across both KBs",
        "hybrid_search Postgres fusion",
        "both",
    ),
)


# Extra rationale-style queries when --with-comments — designed to hit
# the "why did we pick X" content that comments carry and code/docs
# usually don't.
COMMENTS_QUERIES: tuple[tuple[str, str, str], ...] = (
    (
        "rationale query against kb_comments",
        "why is the source column write-once on conflict",
        "comments",
    ),
    (
        "joint rationale query across all KBs",
        "subprocess isolation across cells reason",
        "all",
    ),
)


def _hybrid(
    dsn: str,
    *,
    schema: str,
    table: str,
    query: str,
    query_vec,
    k: int,
    fts_language: str = "english",
):
    from chunkshop.search import hybrid_search

    return hybrid_search(
        dsn,
        schema=schema,
        table=table,
        query=query,
        query_vec=query_vec,
        k=k,
        legs=("semantic", "fts"),
        fusion="rrf",
        language=fts_language,
    )


def _short(text: str, n: int = 110) -> str:
    s = " ".join((text or "").split())
    return s[:n] + ("..." if len(s) > n else "")


def _print_hits(label: str, hits, *, max_show: int = 5) -> dict:
    """Render top-k hits compactly; return a summary for the comparison table."""
    print()
    print(f"  --- {label}")
    if not hits:
        print("      (no hits)")
        return {"label": label, "n": 0, "top": None}
    for rank, h in enumerate(hits[:max_show], start=1):
        meta = h.metadata or {}
        sym = meta.get("symbol_name") or meta.get("fqn") or ""
        path = meta.get("source_path") or meta.get("path") or h.doc_id
        legs = ",".join(h.legs)
        # Distinguish code rows (have symbol_name) from docs rows (have language/source_path).
        if sym:
            print(
                f"      {rank}. {sym}  ({legs}, score={h.score:.4f}) "
                f"{Path(str(path)).name}"
            )
        else:
            lang = meta.get("language") or "?"
            print(
                f"      {rank}. {Path(str(path)).name}  ({legs}, score={h.score:.4f}, lang={lang})"
            )
        print(f"         {_short(h.text)}")
    return {"label": label, "n": len(hits), "top": hits[0]}


def _joint_search(
    dsn: str,
    *,
    schema: str,
    tables: tuple[tuple[str, str], ...],  # ((origin_label, table_name), ...)
    query: str,
    query_vec,
    k: int,
):
    """UNION of hybrid_search across N tables; dedup by content; keep best score.

    ``tables`` is a tuple of ``(origin_label, table_name)`` pairs — the
    label is printed next to each hit so the operator can tell which KB
    a row came from. Two-KB and three-KB callers share this one helper.
    """
    tagged = []
    for origin, tbl in tables:
        hits = _hybrid(
            dsn, schema=schema, table=tbl,
            query=query, query_vec=query_vec, k=k,
        )
        for h in hits:
            tagged.append((origin, h))

    # Dedup by chunk content; keep the higher-scoring duplicate.
    by_text: dict[str, tuple[str, object]] = {}
    for origin, h in tagged:
        key = h.text.strip()[:200]
        if not key:
            continue
        prev = by_text.get(key)
        if prev is None or h.score > prev[1].score:
            by_text[key] = (origin, h)

    merged = sorted(by_text.values(), key=lambda t: t[1].score, reverse=True)[:k]
    return merged


def _print_joint(label: str, merged) -> dict:
    print()
    print(f"  --- {label}")
    if not merged:
        print("      (no hits)")
        return {"label": label, "n": 0, "top": None}
    for rank, (origin, h) in enumerate(merged, start=1):
        meta = h.metadata or {}
        sym = meta.get("symbol_name") or ""
        path = meta.get("source_path") or h.doc_id
        legs = ",".join(h.legs)
        marker = f"[{origin}]"
        if sym:
            print(
                f"      {rank}. {marker} {sym}  ({legs}, score={h.score:.4f}) "
                f"{Path(str(path)).name}"
            )
        else:
            lang = meta.get("language") or "?"
            print(
                f"      {rank}. {marker} {Path(str(path)).name}  "
                f"({legs}, score={h.score:.4f}, lang={lang})"
            )
        print(f"         {_short(h.text)}")
    return {
        "label": label,
        "n": len(merged),
        "top": merged[0][1],
        "top_origin": merged[0][0],
    }


# ---------------------------------------------------------------------------
# Stats / table printing
# ---------------------------------------------------------------------------


def _table_stats(dsn: str, schema: str, table: str) -> dict:
    import psycopg

    fq = f'"{schema}"."{table}"'
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {fq}")
        nrows = cur.fetchone()[0]
        if nrows == 0:
            return {"rows": 0, "avg_chars": 0.0, "distinct_docs": 0}
        cur.execute(
            f"SELECT avg(length(original_content)), count(distinct doc_id) FROM {fq}"
        )
        avg_chars, ndocs = cur.fetchone()
    return {"rows": nrows, "avg_chars": float(avg_chars or 0.0), "distinct_docs": ndocs}


def _print_summary_table(
    code_stats: dict,
    docs_stats: dict,
    query_results: list[dict],
    comments_stats: Optional[dict] = None,
) -> None:
    print()
    print("=" * 72)
    print("# Per-KB summary")
    print("=" * 72)
    rows = [
        ("kb_code", code_stats),
        ("kb_docs", docs_stats),
    ]
    if comments_stats is not None:
        rows.append(("kb_comments", comments_stats))
    print(f"  {'table':<12} {'rows':>8} {'docs':>6} {'avg_chars':>11}")
    for name, s in rows:
        print(
            f"  {name:<12} {s['rows']:>8} {s['distinct_docs']:>6} {s['avg_chars']:>11.0f}"
        )

    print()
    print("  Per-query top-1 hit:")
    for qr in query_results:
        label = qr["label"]
        if qr["n"] == 0 or qr["top"] is None:
            print(f"    - {label:<40} -> (none)")
            continue
        top = qr["top"]
        meta = top.metadata or {}
        sym = meta.get("symbol_name")
        path = meta.get("source_path") or top.doc_id
        if sym:
            descriptor = f"symbol={sym}"
        else:
            descriptor = f"path={Path(str(path)).name}"
        origin = qr.get("top_origin", "")
        origin_s = f"[{origin}] " if origin else ""
        print(f"    - {label:<40} -> {origin_s}{descriptor}  (score={top.score:.4f})")


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Two-KB ingest demo: code + docs from one repo, queried separately + jointly.",
    )
    here = Path(__file__).resolve()
    default_repo = here.parents[2]  # chunkshop repo root
    p.add_argument(
        "--repo",
        type=Path,
        default=default_repo,
        help=f"Code repo to ingest (default: {default_repo})",
    )
    p.add_argument(
        "--schema",
        default=DEFAULT_SCHEMA,
        help=f"Postgres schema for both tables (default: {DEFAULT_SCHEMA})",
    )
    p.add_argument(
        "--dsn",
        default=os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN),
        help="Postgres DSN (default: $CHUNKSHOP_TEST_DSN or the test stack DSN)",
    )
    p.add_argument(
        "--query",
        help="Override the 4 default queries with a single user query (runs against code, docs, joint).",
    )
    p.add_argument(
        "--cleanup",
        action="store_true",
        help="Drop the demo schema at the end (default: retain for inspection).",
    )
    p.add_argument(
        "--no-cleanup",
        dest="cleanup",
        action="store_false",
        help="(default) Retain the demo schema after the run.",
    )
    p.set_defaults(cleanup=False)
    p.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Embedder + OMP thread count per cell (default: 4).",
    )
    p.add_argument(
        "--code-table",
        default="kb_code",
        help="Table name for the code KB (default: kb_code).",
    )
    p.add_argument(
        "--docs-table",
        default="kb_docs",
        help="Table name for the docs KB (default: kb_docs).",
    )
    p.add_argument(
        "--with-comments",
        action="store_true",
        help=(
            "Also build a third KB from comments mined out of the code files "
            "(uses the comment_extracts source). Default off for backward-compat."
        ),
    )
    p.add_argument(
        "--comments-table",
        default="kb_comments",
        help="Table name for the comments KB when --with-comments (default: kb_comments).",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    _print_banner()
    print(f"  repo:        {args.repo}")
    print(f"  DSN:         {args.dsn}")
    print(f"  schema:      {args.schema}")
    print(f"  code table:  {args.schema}.{args.code_table}")
    print(f"  docs table:  {args.schema}.{args.docs_table}")

    repo_root = args.repo.resolve()
    if not repo_root.is_dir():
        print(f"  -> repo path is not a directory: {repo_root}", file=sys.stderr)
        return 1

    if not _postgres_reachable(args.dsn):
        print(
            "  -> skipping demo. "
            "Start with `docker compose -f docker-compose.test.yaml up -d` from the repo root."
        )
        return 0

    # Pass DSN to sinks via env var (TargetConfig.dsn_env).
    dsn_env = "CHUNKSHOP_DEMO_TWO_KB_DSN"
    os.environ[dsn_env] = args.dsn

    # Drop the schema up-front so each run starts clean and every sub-cell
    # can use ``mode='create_if_missing'``.
    print("\n  pre-flight: dropping any pre-existing demo schema...")
    _drop_schema(args.dsn, args.schema)

    # --- KB 1: code ----------------------------------------------------
    print("\n--- Cell 1: kb_code ---")
    code_result = _run_cells(
        repo_root,
        kind="code",
        exts=CODE_EXTS,
        schema=args.schema,
        table=args.code_table,
        dsn_env=dsn_env,
        omp_threads=args.threads,
    )
    if code_result["error"]:
        print(f"  kb_code failed: {code_result['error']}", file=sys.stderr)
        return 1

    # --- KB 2: docs ----------------------------------------------------
    print("\n--- Cell 2: kb_docs ---")
    docs_result = _run_cells(
        repo_root,
        kind="docs",
        exts=DOCS_EXTS,
        schema=args.schema,
        table=args.docs_table,
        dsn_env=dsn_env,
        omp_threads=args.threads,
    )
    if docs_result["error"]:
        print(f"  kb_docs failed: {docs_result['error']}", file=sys.stderr)
        return 1

    # --- KB 3: comments (opt-in) ---------------------------------------
    comments_result: Optional[dict] = None
    comments_stats: Optional[dict] = None
    if args.with_comments:
        print("\n--- Cell 3: kb_comments ---")
        comments_result = _run_cells(
            repo_root,
            kind="comments",
            exts=CODE_EXTS,  # mine comments out of the code files
            schema=args.schema,
            table=args.comments_table,
            dsn_env=dsn_env,
            omp_threads=args.threads,
        )
        if comments_result["error"]:
            print(
                f"  kb_comments failed: {comments_result['error']}",
                file=sys.stderr,
            )
            return 1

    # --- Stats ---------------------------------------------------------
    code_stats = _table_stats(args.dsn, args.schema, args.code_table)
    docs_stats = _table_stats(args.dsn, args.schema, args.docs_table)
    if args.with_comments:
        comments_stats = _table_stats(args.dsn, args.schema, args.comments_table)
    print("\n--- Cell summary ---")
    print(
        f"  kb_code: docs={code_result['docs_processed']} "
        f"chunks={code_result['chunks_written']} "
        f"wall={code_result['wall_seconds']:.1f}s "
        f"embed={code_result['embed_seconds']:.1f}s "
        f"-> {code_stats['rows']} row(s) in {args.schema}.{args.code_table}"
    )
    print(
        f"  kb_docs: docs={docs_result['docs_processed']} "
        f"chunks={docs_result['chunks_written']} "
        f"wall={docs_result['wall_seconds']:.1f}s "
        f"embed={docs_result['embed_seconds']:.1f}s "
        f"-> {docs_stats['rows']} row(s) in {args.schema}.{args.docs_table}"
    )
    if args.with_comments and comments_result is not None and comments_stats is not None:
        print(
            f"  kb_comments: docs={comments_result['docs_processed']} "
            f"chunks={comments_result['chunks_written']} "
            f"wall={comments_result['wall_seconds']:.1f}s "
            f"embed={comments_result['embed_seconds']:.1f}s "
            f"-> {comments_stats['rows']} row(s) in {args.schema}.{args.comments_table}"
        )

    # --- Build FTS indexes (idempotent) before querying ---------------
    # hybrid_search uses both a vector leg and an FTS leg. The cells write
    # the chunks table with the vector column but no tsvector; we add the
    # generated tsvector column + GIN index here so the FTS leg has
    # something to query.
    from chunkshop.search import ensure_fts

    print("\n--- Building FTS indexes ---")
    fts_tables = [args.code_table, args.docs_table]
    if args.with_comments:
        fts_tables.append(args.comments_table)
    for tbl in fts_tables:
        print(f"  ensure_fts on {args.schema}.{tbl}...")
        ensure_fts(args.dsn, schema=args.schema, table=tbl)

    # --- Queries -------------------------------------------------------
    print("\n--- Demo queries (top-5 each) ---")
    from chunkshop.embedders import load_embedder
    from chunkshop.config import FastembedEmbedder

    embedder = load_embedder(
        FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=args.threads,
        )
    )

    query_results: list[dict] = []

    if args.query:
        # Single user query: run it against code, docs, joint, and
        # (if enabled) the comments KB + an all-KB joint.
        queries_list: list[tuple[str, str, str]] = [
            ("user query against kb_code", args.query, "code"),
            ("user query against kb_docs", args.query, "docs"),
            ("user query joint", args.query, "both"),
        ]
        if args.with_comments:
            queries_list.append(
                ("user query against kb_comments", args.query, "comments")
            )
            queries_list.append(
                ("user query joint across all three KBs", args.query, "all")
            )
        queries = tuple(queries_list)
    else:
        queries = DEFAULT_QUERIES
        if args.with_comments:
            queries = queries + COMMENTS_QUERIES

    # Pre-build the joint-table tuples so we don't recompute per query.
    both_tables = (("kb_code", args.code_table), ("kb_docs", args.docs_table))
    all_tables = both_tables + (("kb_comments", args.comments_table),)

    for label, q, target in queries:
        qv = embedder.embed([q])[0]
        if target == "code":
            hits = _hybrid(
                args.dsn,
                schema=args.schema, table=args.code_table,
                query=q, query_vec=qv, k=5,
            )
            query_results.append(_print_hits(f"{label}: {q!r}", hits))
        elif target == "docs":
            hits = _hybrid(
                args.dsn,
                schema=args.schema, table=args.docs_table,
                query=q, query_vec=qv, k=5,
            )
            query_results.append(_print_hits(f"{label}: {q!r}", hits))
        elif target == "comments":
            hits = _hybrid(
                args.dsn,
                schema=args.schema, table=args.comments_table,
                query=q, query_vec=qv, k=5,
            )
            query_results.append(_print_hits(f"{label}: {q!r}", hits))
        elif target == "all":
            merged = _joint_search(
                args.dsn,
                schema=args.schema,
                tables=all_tables,
                query=q, query_vec=qv, k=5,
            )
            query_results.append(_print_joint(f"{label}: {q!r}", merged))
        else:  # both
            merged = _joint_search(
                args.dsn,
                schema=args.schema,
                tables=both_tables,
                query=q, query_vec=qv, k=5,
            )
            query_results.append(_print_joint(f"{label}: {q!r}", merged))

    # --- Summary table -------------------------------------------------
    _print_summary_table(
        code_stats, docs_stats, query_results, comments_stats=comments_stats,
    )

    # --- Cleanup hint --------------------------------------------------
    print()
    if args.cleanup:
        _drop_schema(args.dsn, args.schema)
        print(f"  cleanup: dropped schema {args.schema!r}")
    else:
        print(
            f"  schema {args.schema!r} retained for inspection. To inspect:\n"
            f"    psql {args.dsn} -c 'SELECT count(*) FROM {args.schema}.{args.code_table};'\n"
            f"    psql {args.dsn} -c 'SELECT count(*) FROM {args.schema}.{args.docs_table};'\n"
            f"  Re-run with --cleanup or this script next time to drop and recreate."
        )

    print("\n  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
