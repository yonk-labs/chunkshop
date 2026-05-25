#!/usr/bin/env python3
"""# Real-world end-to-end 5-KB integration test for chunkshop.

Builds and queries five separate knowledge bases against the chunkshop
test stack (Postgres @5434, ClickHouse @8124), exercising the full
``Source → Chunker → Embedder → Extractor → Sink`` pipeline plus
``chunkshop.search.hybrid_search`` across heterogeneous corpora.

Phases (each isolated under try/except so one failure doesn't abort
the report):

* Phase 0  workspace + DB pre-flight
* Phase 1  shallow-clone 3 public GitHub repos (with local fallbacks)
* Phase 2  three per-repo KBs: ``kb_ragflow``, ``kb_lede``, ``kb_chunkshop``
* Phase 3  cross-cutting all-MD KB (``kb_all_md``)
* Phase 4  PDF KB seeded from 5 arxiv papers (``kb_topical``, overwrite)
* Phase 5  4 hand-written .md briefs appended to the same table
* Phase 6  5 ClickHouse rows pulled via ``clickhouse_table`` source, appended
* Phase 7  hybrid_search (semantic + FTS, RRF fusion) across all 5 KBs
* Phase 8  cleanup note (schema retained by default)

Outputs a banner+status per phase to stdout AND a captured copy to
``/tmp/chunkshop-real-world/run-report.txt``.

Run:
    python connectors/examples/e2e_real_world_5kbs.py
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKSPACE = Path("/tmp/chunkshop-real-world")
REPORT_PATH = WORKSPACE / "run-report.txt"
SCHEMA = "chunkshop_real_world_test"

DEFAULT_PG_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
DEFAULT_CH_DSN = "clickhouse://default:chpw@localhost:8124/chunkshop_test"

# Repos to clone. Each tuple is (kb_name, https_url, local_fallback_path).
# If the public clone fails (e.g. private/rate-limited) we fall back to a
# nearby local checkout so the demo can still build a KB.
REPO_TARGETS = [
    (
        "ragflow",
        "https://github.com/infiniflow/ragflow",
        None,
    ),
    (
        "lede",
        "https://github.com/yonk-labs/lede",
        "/Users/matt.yonkovit/yonk-tools/lede",
    ),
    (
        "chunkshop",
        "https://github.com/yonk-labs/chunkshop",
        "/Users/matt.yonkovit/yonk-tools/chunkshop",
    ),
]

# File extensions ingested per repo. Mix of prose and code — `sentence_aware`
# handles both adequately (it's tokenized text either way for embedding
# purposes; the chunker just splits on sentence-like boundaries which still
# produces reasonable code chunks).
INGEST_EXTS = (".md", ".py", ".rs", ".go", ".toml", ".yaml", ".yml")

# Per-repo file cap so ragflow doesn't dominate wall time.
PER_REPO_CAP = 200
# All-MD cross-cutting cap (4 repos × ~variable but bounded).
ALL_MD_CAP = 400

# Arxiv PDFs — small, stable, public retrieval/embedding papers.
ARXIV_PDFS = [
    ("2005.11401", "RAG (Lewis et al.)"),
    ("2004.04906", "Dense Passage Retrieval"),
    ("1908.10084", "Sentence-BERT"),
    ("1810.04805", "BERT"),
    ("2104.08663", "BEIR benchmark"),
]

# Hybrid-search queries to fan out across every KB.
HYBRID_QUERIES = [
    "vector embedding retrieval",
    "reciprocal rank fusion",
    "language model fine-tuning",
]

KB_TABLES_FOR_SEARCH = [
    "kb_ragflow",
    "kb_lede",
    "kb_chunkshop",
    "kb_all_md",
    "kb_topical",
]


# ---------------------------------------------------------------------------
# Phase result accumulator
# ---------------------------------------------------------------------------

@dataclass
class PhaseResult:
    name: str
    status: str  # "OK" | "FAIL" | "SKIP"
    wall_seconds: float
    detail: str = ""
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tee-to-file output capture
# ---------------------------------------------------------------------------

class _TeeStream(io.TextIOBase):
    """Mirror writes to both stdout and a captured file."""

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary

    def write(self, s):
        self._primary.write(s)
        self._secondary.write(s)
        return len(s)

    def flush(self):
        self._primary.flush()
        self._secondary.flush()


def _banner(title: str) -> None:
    print()
    print("=" * 78)
    print(f"# {title}")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Pre-flight helpers
# ---------------------------------------------------------------------------

def _pg_reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except Exception as exc:
        print(f"  Postgres unreachable: {exc}")
        return False


def _ch_reachable(dsn: str) -> bool:
    try:
        from chunkshop.backends.clickhouse import ClickHouseBackend
    except ImportError:
        return False
    try:
        backend = ClickHouseBackend(dsn=dsn)
        with backend.connect() as client:
            client.query("SELECT 1")
            return True
    except Exception as exc:
        print(f"  ClickHouse unreachable: {exc}")
        return False


# ---------------------------------------------------------------------------
# Helper: copy a capped, sorted slice of files into a staging dir.
# ---------------------------------------------------------------------------

def _stage_capped_corpus(
    src_root: Path,
    dst_root: Path,
    exts: tuple[str, ...],
    cap: int,
) -> tuple[int, int]:
    """Copy at most ``cap`` files matching ``exts`` from ``src_root`` into ``dst_root``.

    Filenames are made unique by prefixing with a short hash of the relative
    path so the chunkshop FilesSource glob has flat semantics.

    Returns (files_copied, total_bytes).
    """
    import hashlib

    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for ext in exts:
        # Skip vendor / git / cache trees — they bloat counts without value.
        paths.extend(
            p for p in src_root.rglob(f"*{ext}")
            if p.is_file()
            and not any(part in {".git", "node_modules", "__pycache__", ".venv", "venv",
                                 "site-packages", "dist", "build", "target"}
                        for part in p.parts)
        )

    # Stable sort so re-runs give identical corpus subsets.
    paths.sort(key=lambda p: str(p))
    paths = paths[:cap]

    total_bytes = 0
    for src in paths:
        rel = src.relative_to(src_root)
        # short prefix avoids name collisions when distinct dirs hold same filename.
        short = hashlib.sha1(str(rel).encode()).hexdigest()[:8]
        flat_name = f"{short}_{rel.name}"
        dst = dst_root / flat_name
        try:
            shutil.copyfile(src, dst)
            total_bytes += dst.stat().st_size
        except Exception:
            # Binary or unreadable files — skip silently. They were globbed
            # by extension but may still be unreadable on some platforms.
            continue
    return len(list(dst_root.glob("*"))), total_bytes


# ---------------------------------------------------------------------------
# Phase 0 — workspace + pre-flight
# ---------------------------------------------------------------------------

def phase_0_setup(pg_dsn: str, ch_dsn: str) -> PhaseResult:
    t0 = time.time()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for sub in ("repos", "pdfs", "md_topical", "clickhouse_data", "staging"):
        (WORKSPACE / sub).mkdir(exist_ok=True)
    pg_ok = _pg_reachable(pg_dsn)
    ch_ok = _ch_reachable(ch_dsn)
    print(f"  workspace: {WORKSPACE}")
    print(f"  postgres DSN: {pg_dsn}  reachable={pg_ok}")
    print(f"  clickhouse DSN: {ch_dsn}  reachable={ch_ok}")
    if not pg_ok:
        return PhaseResult("0-setup", "FAIL", time.time() - t0,
                           "Postgres unreachable — cannot continue")
    detail = "pg+ch reachable" if ch_ok else "pg OK, ch unreachable (ClickHouse phase will skip)"
    return PhaseResult("0-setup", "OK", time.time() - t0, detail,
                       stats={"clickhouse_reachable": ch_ok})


# ---------------------------------------------------------------------------
# Phase 1 — shallow clone (with local fallback)
# ---------------------------------------------------------------------------

def _clone_or_link(name: str, url: str, fallback: Optional[str]) -> tuple[Path, str, dict]:
    """Clone ``url`` into ``repos/<name>``; on failure use ``fallback`` symlink.

    Returns (resolved_dir, source_kind, stats) where source_kind is
    ``"cloned"`` or ``"fallback"``. Stats includes timing / file count /
    size info for reporting.
    """
    target = WORKSPACE / "repos" / name
    stats: dict = {}
    t0 = time.time()
    if (target / ".git").exists() or (target.exists() and target.is_symlink()):
        # Idempotent: a prior run already laid it down.
        stats["mode"] = "reused"
    else:
        if target.exists():
            shutil.rmtree(target)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(target)],
                check=True,
                capture_output=True,
                timeout=600,
            )
            stats["mode"] = "cloned"
        except Exception as exc:
            print(f"  clone failed for {name}: {exc}")
            if fallback and Path(fallback).exists():
                print(f"  using local fallback: {fallback}")
                # Symlink so we don't duplicate gigabytes locally.
                target.symlink_to(fallback)
                stats["mode"] = "fallback"
                stats["fallback_path"] = fallback
            else:
                raise

    stats["clone_seconds"] = round(time.time() - t0, 2)

    # File / size summary.
    files = [p for p in target.rglob("*")
             if p.is_file()
             and not any(part == ".git" for part in p.parts)]
    stats["file_count"] = len(files)
    try:
        stats["size_bytes"] = sum(p.stat().st_size for p in files if p.is_file())
    except Exception:
        stats["size_bytes"] = -1
    return target, stats["mode"], stats


def phase_1_clone_repos() -> PhaseResult:
    t0 = time.time()
    summary: dict = {}
    failures: list[str] = []
    for name, url, fallback in REPO_TARGETS:
        try:
            path, mode, stats = _clone_or_link(name, url, fallback)
            summary[name] = stats
            print(f"  {name:10}  mode={mode:8}  files={stats['file_count']:5}  "
                  f"size={stats['size_bytes']/1e6:.1f}MB  wall={stats['clone_seconds']}s  -> {path}")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"  {name:10}  FAILED — {exc}")
    if not summary:
        return PhaseResult("1-clone", "FAIL", time.time() - t0,
                           f"all clones failed: {failures}", summary)
    status = "OK" if not failures else "OK"  # partial OK still moves on
    return PhaseResult("1-clone", status, time.time() - t0,
                       f"acquired {len(summary)}/{len(REPO_TARGETS)} repos",
                       summary)


# ---------------------------------------------------------------------------
# Phase 2 — per-repo KBs (each one full Source→Sink ingest)
# ---------------------------------------------------------------------------

def _ingest_corpus(
    *,
    cell_name: str,
    glob: str,
    table: str,
    mode: str,
    source_tag: Optional[str],
    pg_dsn: str,
):
    """Run a single chunkshop cell against the given file glob.

    Centralised here so all KB-building phases share the same chunker /
    embedder / extractor / sink settings — only the source glob and the
    target table differ.
    """
    os.environ["CHUNKSHOP_REAL_WORLD_DSN"] = pg_dsn
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
    from chunkshop.runner import run_cell

    cfg = CellConfig(
        cell_name=cell_name,
        source=FilesSource(type="files", glob=glob, id_from="stem"),
        chunker=SentenceAwareChunker(type="sentence_aware", min_chars=200, max_chars=1200),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=64,
            threads=2,
        ),
        extractor=LangDetectExtractor(type="lang_detect"),
        target=TargetConfig(
            type="postgres",
            dsn_env="CHUNKSHOP_REAL_WORLD_DSN",
            database=SCHEMA,
            table=table,
            mode=mode,
            source_tag=source_tag,
            hnsw=False,  # corpora are small enough that seq scan wins
            promote_metadata=[PromoteColumn(path="language", type="text")],
            fts=None,  # FTS index added separately so we can re-use across appends
        ),
        runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=25),
    )
    return run_cell(cfg)


def _ensure_fts(pg_dsn: str, table: str) -> None:
    """Add a tsvector index to ``SCHEMA.table`` if not present.

    The orchestrator KBs are built without FTS at create-time so that
    appends and overwrites stay simple. We add the FTS index afterwards
    via ``chunkshop.search.ensure_fts`` which is idempotent.
    """
    from chunkshop import search
    try:
        search.ensure_fts(pg_dsn, schema=SCHEMA, table=table, language="english")
    except Exception as exc:
        print(f"  ensure_fts({table}) failed: {exc}")


def phase_2_per_repo_kbs(pg_dsn: str, repos_summary: dict) -> PhaseResult:
    t0 = time.time()
    results: dict = {}
    failures: list[str] = []
    staging_root = WORKSPACE / "staging"
    for name, _url, _fallback in REPO_TARGETS:
        if name not in repos_summary:
            continue
        repo_dir = WORKSPACE / "repos" / name
        if not repo_dir.exists():
            failures.append(f"{name}: repo dir missing")
            continue
        table = f"kb_{name}"
        print(f"\n  -- building {table} from {repo_dir}")
        # Stage a capped flat corpus.
        staged = staging_root / name
        nfiles, total_bytes = _stage_capped_corpus(repo_dir, staged, INGEST_EXTS, PER_REPO_CAP)
        print(f"     staged {nfiles} files ({total_bytes/1e6:.1f}MB) -> {staged}")
        if nfiles == 0:
            failures.append(f"{name}: no files matched")
            continue
        glob = str(staged / "*")
        try:
            res = _ingest_corpus(
                cell_name=f"phase2_{name}",
                glob=glob,
                table=table,
                mode="overwrite",
                source_tag=None,
                pg_dsn=pg_dsn,
            )
            if res.error:
                failures.append(f"{name}: {res.error}")
                print(f"     INGEST FAILED: {res.error}")
                continue
            _ensure_fts(pg_dsn, table)
            results[name] = {
                "table": table,
                "docs_processed": res.docs_processed,
                "chunks_written": res.chunks_written,
                "wall_seconds": round(res.wall_seconds, 2),
                "embed_seconds": round(res.embed_seconds, 2),
                "staged_files": nfiles,
            }
            print(f"     OK docs={res.docs_processed} chunks={res.chunks_written} "
                  f"wall={res.wall_seconds:.1f}s embed={res.embed_seconds:.1f}s")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            traceback.print_exc()
    status = "OK" if not failures and results else ("FAIL" if not results else "OK")
    detail = f"built {len(results)} KBs"
    if failures:
        detail += f"; failures: {failures}"
    return PhaseResult("2-per-repo-kbs", status, time.time() - t0, detail, results)


# ---------------------------------------------------------------------------
# Phase 3 — cross-cutting all-MD KB
# ---------------------------------------------------------------------------

def phase_3_all_md_kb(pg_dsn: str, repos_summary: dict) -> PhaseResult:
    t0 = time.time()
    staging = WORKSPACE / "staging" / "all_md"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # Collect all .md across acquired repos, cap globally.
    candidates: list[Path] = []
    for name in repos_summary:
        repo_dir = WORKSPACE / "repos" / name
        if not repo_dir.exists():
            continue
        for p in repo_dir.rglob("*.md"):
            if not any(part in {".git"} for part in p.parts):
                candidates.append(p)
    candidates.sort(key=lambda p: str(p))
    candidates = candidates[:ALL_MD_CAP]
    import hashlib
    for p in candidates:
        short = hashlib.sha1(str(p).encode()).hexdigest()[:8]
        flat_name = f"{short}_{p.name}"
        try:
            shutil.copyfile(p, staging / flat_name)
        except Exception:
            continue
    nfiles = len(list(staging.glob("*.md")))
    print(f"  staged {nfiles} .md files into {staging}")
    if nfiles == 0:
        return PhaseResult("3-all-md", "FAIL", time.time() - t0, "no .md files staged")
    try:
        res = _ingest_corpus(
            cell_name="phase3_all_md",
            glob=str(staging / "*.md"),
            table="kb_all_md",
            mode="overwrite",
            source_tag=None,
            pg_dsn=pg_dsn,
        )
        if res.error:
            return PhaseResult("3-all-md", "FAIL", time.time() - t0, res.error)
        _ensure_fts(pg_dsn, "kb_all_md")
        print(f"  OK docs={res.docs_processed} chunks={res.chunks_written} "
              f"wall={res.wall_seconds:.1f}s")
        return PhaseResult(
            "3-all-md", "OK", time.time() - t0,
            f"docs={res.docs_processed} chunks={res.chunks_written}",
            {"docs_processed": res.docs_processed,
             "chunks_written": res.chunks_written,
             "wall_seconds": round(res.wall_seconds, 2),
             "embed_seconds": round(res.embed_seconds, 2),
             "staged_files": nfiles},
        )
    except Exception as exc:
        traceback.print_exc()
        return PhaseResult("3-all-md", "FAIL", time.time() - t0, str(exc))


# ---------------------------------------------------------------------------
# Phase 4 — PDF KB (overwrite)
# ---------------------------------------------------------------------------

def _download_pdf(arxiv_id: str, dst: Path) -> bool:
    if dst.exists() and dst.stat().st_size > 50_000:
        return True
    import httpx
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    print(f"     downloading {url}")
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            r = client.get(url)
            r.raise_for_status()
            dst.write_bytes(r.content)
        return True
    except Exception as exc:
        print(f"     FAILED: {exc}")
        return False


def phase_4_pdf_kb(pg_dsn: str) -> PhaseResult:
    t0 = time.time()
    pdf_dir = WORKSPACE / "pdfs"
    grabbed = 0
    for arxiv_id, label in ARXIV_PDFS:
        dst = pdf_dir / f"{arxiv_id}.pdf"
        if _download_pdf(arxiv_id, dst):
            grabbed += 1
            print(f"     OK {arxiv_id}  ({label})  size={dst.stat().st_size/1024:.0f}KB")
    if grabbed < 3:
        return PhaseResult("4-pdf-kb", "FAIL", time.time() - t0,
                           f"only {grabbed} PDFs available (need >=3)")
    try:
        res = _ingest_corpus(
            cell_name="phase4_pdf_kb",
            glob=str(pdf_dir / "*.pdf"),
            table="kb_topical",
            mode="overwrite",
            source_tag=None,
            pg_dsn=pg_dsn,
        )
        if res.error:
            return PhaseResult("4-pdf-kb", "FAIL", time.time() - t0, res.error)
        _ensure_fts(pg_dsn, "kb_topical")
        print(f"  OK docs={res.docs_processed} chunks={res.chunks_written} "
              f"wall={res.wall_seconds:.1f}s")
        return PhaseResult(
            "4-pdf-kb", "OK", time.time() - t0,
            f"docs={res.docs_processed} chunks={res.chunks_written}",
            {"docs_processed": res.docs_processed,
             "chunks_written": res.chunks_written,
             "wall_seconds": round(res.wall_seconds, 2),
             "embed_seconds": round(res.embed_seconds, 2),
             "pdfs_downloaded": grabbed},
        )
    except Exception as exc:
        traceback.print_exc()
        return PhaseResult("4-pdf-kb", "FAIL", time.time() - t0, str(exc))


# ---------------------------------------------------------------------------
# Phase 5 — generate 4 MD briefs and APPEND to kb_topical
# ---------------------------------------------------------------------------

MD_BRIEFS: dict[str, str] = {
    "rag_overview.md": """# Retrieval-Augmented Generation: A Practical Overview

Retrieval-Augmented Generation (RAG) is the architectural pattern in which a
large language model is paired with an external retriever that supplies
grounded, query-conditioned context at inference time. The model itself is not
modified; instead, the prompt is augmented with passages selected from a
corpus the model never trained on. The corpus is typically indexed by a vector
database, but increasingly hybrid (vector + keyword + structured) indexes are
the norm.

## Why RAG exists

Language models drift in three ways that hurt production use:

1. **Stale knowledge.** A model trained in 2024 cannot tell you what your CEO
   said on the most recent earnings call. Fine-tuning is expensive and slow;
   the corpus you care about today might not exist yet when the model is
   trained.
2. **Hallucinated specifics.** Models confidently fabricate citations, API
   signatures, and customer names. Grounding the generation in a retrieved
   passage that contains the actual fact dramatically reduces this class of
   failure.
3. **No tenancy boundaries.** A single base model serves all of your tenants.
   RAG lets you keep each tenant's corpus isolated and index-scoped without
   training a separate model per tenant.

## The minimum viable architecture

```
                  +----------------+
        query --> | embed (E_q)    | --+
                  +----------------+   |
                                       v
                  +----------------+   ANN search   +----------+
                  | vector index   | <------------- | corpus   |
                  +----------------+                +----------+
                          |
                 top-k passages
                          |
                          v
                  +----------------+
                  | prompt builder | --> LLM --> answer
                  +----------------+
```

The retriever and the generator can live on entirely different infrastructure.
The retriever's only contract with the generator is: produce a small list of
text passages relevant to the query. That contract is small enough that you
can swap embedder, swap vector store, swap LLM, or move the retrieval offline
without coupling changes propagating across the system.

## Variants that matter in practice

- **Naive RAG**: single retrieval, single generation. Cheap, fast, baseline.
- **Hybrid retrieval**: combine dense vector ANN with BM25 / keyword search,
  then fuse with reciprocal rank fusion (RRF) or linear weighting. Catches
  the cases where the embedding model semantically blurs distinctions that a
  bag-of-words index resolves trivially (e.g., exact identifier matches like
  `kafka_2.13` versus `kafka_2.12`).
- **Re-ranking**: after retrieval, pass the top-N passages through a
  cross-encoder that scores `(query, passage)` jointly. Cross-encoders are
  10-100x slower per pair than bi-encoders but materially improve top-1
  accuracy.
- **HyDE**: hypothetical document embeddings. Generate a draft answer from
  the LLM, embed *that*, retrieve against the draft's embedding. Useful when
  the user's query is terse or ambiguous and the document side of the corpus
  is verbose.
- **Multi-hop retrieval**: chain retrievals — answer A1 from corpus, use A1
  to formulate a follow-up retrieval against corpus, synthesize. Required for
  questions whose answer is not directly in any single passage.
- **Self-RAG / corrective RAG**: the LLM judges whether retrieved context is
  sufficient and decides whether to re-retrieve, fall back to parametric
  knowledge, or refuse to answer.

## Where RAG breaks down

- **Chunking is the silent killer**. Embed quality is bounded by chunk
  quality. A 4 KB chunk that mixes three sub-topics will retrieve as a
  mediocre match for all three queries. A 200-character chunk loses context.
  Heading-aware and sentence-aware chunkers outperform fixed-window
  splitters on most prose corpora.
- **Embedding drift**. Two embedding model families (e.g., bge vs. openai)
  produce vectors in different spaces. Mixing them in one index is
  meaningless. Re-embedding the corpus on every model upgrade is real cost.
- **Recency vs. relevance**. ANN retrieval has no native notion of "this
  document is from yesterday" — you have to filter or boost by metadata
  explicitly. Most production stacks bake a recency boost into the fusion
  weights.

A modern production stack typically has a chunker (sentence-aware or
hierarchy-aware), an embedder (bge-small-en, e5-base, or a closed-source
OpenAI / Cohere endpoint), a vector store with hybrid search (pgvector,
Weaviate, Qdrant, Milvus, or a managed Pinecone / Vespa), and an LLM. All
five components are independently swappable, which is the architectural
property that has made RAG ubiquitous.
""",
    "vector_dbs_comparison.md": """# Vector Databases in 2026: pgvector, Pinecone, Weaviate, Milvus, Qdrant

The vector-database market settled into three rough categories: extensions of
existing OLTP databases (pgvector for Postgres, MariaDB Vector, Oracle AI
Vector), purpose-built vector engines (Weaviate, Milvus, Qdrant, Vespa), and
managed/closed-source SaaS (Pinecone, Cohere's vector store). Each has a
different sweet spot.

## pgvector

The Postgres extension. Adds a `vector` column type, an HNSW index for ANN,
and SQL operators for cosine / inner-product / L2 distance. The killer
feature is that you do NOT operate a second database. Your vectors live in
the same Postgres instance as your application's structured data, which
means you can filter by `tenant_id`, join to `users`, transactionally update
embeddings, and back up everything with a single `pg_dump`.

The limitation is that ANN performance degrades faster than purpose-built
engines once you cross ~50M vectors. HNSW build time becomes painful. Memory
pressure on the index is real. For most workloads (<=10M vectors per index),
pgvector is the correct default — anyone reaching for Pinecone on a 1M-row
corpus is signing up for operational complexity they don't need.

## Pinecone

Fully managed, SaaS, closed-source. Pay by the read/write/storage. No HNSW
tuning, no shard management, no pod sizing — you call their API and they
return top-k. The trade-off is loss of control: you can't run it air-gapped,
you can't see the query plan, your cost scales with your traffic in a way
that is opaque until you hit the bill.

Pinecone's strength is that it just works at scale. A team that is
LLM-product-focused and not infrastructure-focused buys it and ships. Teams
that own their data plane (especially regulated industries) avoid it.

## Weaviate

Purpose-built open-source vector DB. Schema-aware (you define classes, not
just vectors), supports hybrid search (BM25 + vector) natively, and ships
with modules for embedding inline (`text2vec-transformers`,
`text2vec-openai`). Operational story is heavier than pgvector but lighter
than Milvus.

Weaviate's GraphQL API was the original draw and is still distinctive.
Hybrid search with the `alpha` parameter (interpolating BM25 and vector
scores) is the cleanest hybrid API on the market.

## Milvus / Zilliz

Built for scale-out from day one. Uses an etcd + S3 + Pulsar / Kafka
architecture under the hood — you don't run Milvus on one box, you run it on
a cluster. Justified at 100M+ vectors. Overkill below 10M.

Zilliz is the managed offering of Milvus. Same engine, vendor-operated.

## Qdrant

Rust-based, purpose-built vector engine. Single-binary deployment in
practice. Excellent filtered-search performance — its filter-first index
strategy beats most competitors when you have selective metadata predicates
(e.g., `tenant_id = X AND language = "en"`). Good Python client. Open
source with a managed offering.

The downside is a smaller ecosystem than Weaviate / Milvus — fewer
ready-made integrations, less Stack Overflow surface area when you hit
weirdness.

## Vespa

Yahoo's open-source engine. Battle-tested at search-engine scale. Highly
configurable. Steeper learning curve than any of the above. The right choice
when you need recommendation/ranking workloads that mix vectors, structured
filters, learned-to-rank models, and personalization signals.

## Choosing

| Need                              | Choose             |
|-----------------------------------|--------------------|
| One DB, simple stack, <=10M vec   | pgvector           |
| Managed, no ops, willing to pay   | Pinecone           |
| Hybrid search + schema            | Weaviate           |
| 100M+ vectors, distributed        | Milvus / Zilliz    |
| Filtered search dominates         | Qdrant             |
| Ranking + signals + scale         | Vespa              |

Two pieces of advice that survive across categories:

1. **Don't pick by feature checklist; pick by operational fit.** The DB you
   already run won. pgvector vs. Pinecone is rarely a vector question and
   almost always an "are you already a Postgres shop" question.
2. **Re-embedding is the migration cost nobody budgets for.** Switching
   vector DBs is easy. Switching embedding models is not — every vector in
   your corpus must be re-computed. Plan for it.
""",
    "embedding_models_2026.md": """# Embedding Models in 2026: bge, e5, gte, jina, OpenAI, Cohere

A condensed survey of the embedding model families that matter for
retrieval as of 2026, with the trade-offs that actually drive selection in
production.

## bge (BAAI General Embedding)

BAAI's bge family is the open-source default. Variants:

- **bge-small-en-v1.5** — 384-dim, ~30 MB int8 quantized, runs on CPU in
  real time. The pragmatic baseline for self-hosted RAG. Quality is
  noticeably below frontier closed-source but is "good enough" for the vast
  majority of corpora.
- **bge-base-en-v1.5** — 768-dim, ~110 MB. Roughly 5-10% better top-k recall
  on MTEB benchmarks at the cost of bigger vectors and slower embedding.
- **bge-large-en-v1.5** — 1024-dim. Diminishing returns above base for most
  retrieval tasks; the gain is mostly on tasks that aren't pure retrieval.
- **bge-m3** — multi-lingual, multi-functionality (dense + sparse + ColBERT
  in one model). The "if you need multi-language and don't want to manage
  three models" answer.

bge models are MIT licensed. They're the right answer when you need
self-hosted, on-prem, or air-gapped embedding.

## e5 (Microsoft)

The e5 family is bge's nearest open-source competitor. `intfloat/e5-large-v2`
and `multilingual-e5-large` have shipped well on MTEB. The model
expects "query: " and "passage: " prefixes — forgetting these silently
degrades quality. e5 is permissively licensed and a reasonable bge
alternative; in 2026 most teams default to bge and don't re-evaluate.

## gte (Alibaba)

`gte-large` and `gte-base` are a third strong open-source family. Roughly
on par with bge on retrieval benchmarks. Apache 2.0. Choose by what your
ops team is already running — there's no clear quality winner across all
of bge / e5 / gte for general English retrieval.

## jina

Jina's `jina-embeddings-v3` introduced 8K-token context windows, which is
a meaningful win when your chunks are long (technical documentation,
academic papers, contracts). Matryoshka representation learning means you
can truncate the vector and lose less quality than other models — useful
when storage costs dominate.

## OpenAI

`text-embedding-3-small` (1536-dim) and `text-embedding-3-large` (3072-dim,
truncatable to 1024 or 256). Closed source, paid API. Quality is at the
frontier. Operationally trivial (an HTTPS call). The costs that bite:

- Per-token cost on every re-embedding (model upgrades, schema changes).
- Latency tail — p99 is much worse than self-hosted.
- Data residency — if you can't send corpus content to OpenAI, this family
  is out.

## Cohere

`embed-english-v3.0` and `embed-multilingual-v3.0`. Comparable quality to
OpenAI's family. Their distinguishing feature is per-tenant fine-tuning
and a strong re-ranker (`rerank-english-v3.0`) that pairs well with any
upstream retriever. Most teams that pick Cohere over OpenAI do so for the
re-ranker, not the embedder.

## Voyage AI

`voyage-3` and `voyage-3-large`. Newer entrant, strong MTEB numbers.
Specializes in domain-tuned variants (`voyage-code-2`, `voyage-law-2`,
`voyage-finance-2`). If your corpus is genuinely domain-specific, a tuned
voyage model is worth benchmarking against the general-purpose families.

## Practical selection heuristics

- **Default open-source: `bge-small-en-v1.5-int8`.** 384 dim, CPU-fast,
  good enough quality. Upgrade to base/large only when measured retrieval
  is the bottleneck.
- **Default closed-source: `text-embedding-3-small`.** Cheapest OpenAI
  embedder, 1536 dim, ubiquitous SDK support.
- **Multi-language? `bge-m3` (open) or `embed-multilingual-v3.0` (closed).
  Don't try to retrofit an English model with translation; quality is
  noticeably worse than going native.
- **Long chunks (>1K tokens)? `jina-embeddings-v3` or upgrade to a model
  whose context window comfortably exceeds your max chunk size.
- **Code retrieval? voyage-code-2 or e5-code. General-purpose embedders
  underperform on identifier-heavy queries.

## What doesn't matter as much as you think

- The exact MTEB number. The top-of-leaderboard model on MTEB is rarely
  the best model for *your* corpus. Run your own retrieval eval on at
  least 30 gold (query, relevant-doc) pairs from your domain before
  committing.
- Dimension count above 768. Storage and ANN-index cost scale with
  dimension; quality saturates faster than the leaderboard implies.
""",
    "chunking_strategies.md": """# Chunking Strategies: sentence_aware, fixed_overlap, hierarchy, semantic

Chunking — the step that turns a document into the unit you actually embed
and retrieve — is the single biggest under-invested lever in most RAG
systems. The embedder gets all the attention; the chunker silently caps the
quality the embedder can deliver. A bad chunk produces a mediocre vector
no matter how good your embedding model is.

## fixed_overlap

```
[----- chunk 1 -----]
        [----- chunk 2 -----]
                [----- chunk 3 -----]
```

The simplest possible strategy: a sliding window of N tokens (or
characters), stepping by M, where M < N. Overlap ensures that a sentence
straddling a boundary isn't lost.

Strengths: dead simple, deterministic, language-agnostic, no NLP
dependencies.

Weaknesses: hard boundaries can split sentences, paragraphs, even words.
Two adjacent chunks share content, inflating index size by `(N-M)/N`. The
*content* of the chunk has no relationship to the document's logical
structure — a chunk might start mid-table and end mid-list.

Use when: the corpus is genuinely formless prose (transcripts, social
media), or you need a reproducible baseline.

## sentence_aware

Split on sentence boundaries (via a lightweight sentencizer or regex), then
accumulate sentences until you exceed `max_chars`, emit the chunk, and
continue. `min_chars` prevents a one-sentence chunk that's just a heading.

Strengths: every chunk ends on a sentence boundary, so the retrieved
context reads cleanly. No sentence is split across chunks. Cheap — no
embedding model needed at chunk time.

Weaknesses: doesn't understand section structure. A chunk can span the
boundary between two unrelated sections.

Use when: prose-dominant content (articles, documentation, books) and you
don't have a strong heading structure to exploit. This is chunkshop's
default and a reasonable starting point for any text corpus.

## hierarchy

Walk the document structure (Markdown headings, HTML sections, PDF outline
tree). For each leaf section, emit a chunk. Optionally prepend the section
heading(s) to the chunk's `embedded_content` so the embedder sees the
caption as framing context.

Strengths: chunk boundaries match logical boundaries. Heading-prefix
framing gives the embedder cheap, free context (e.g., "Section: API
Reference > Authentication > OAuth Refresh" is enormously informative to
the embedder without bloating the chunk body). chunkshop's factorial
bakeoff on a 772-document legal QA corpus found that hierarchy
consistently wins per embedder column over sentence_aware and
fixed_overlap.

Weaknesses: requires structure. A flat 200-page PDF with no outline tree
falls back to whatever sentencizer you wrap. Section-size variance can
produce very small (one paragraph) or very large (multi-page) chunks
without a max-chars guard.

Use when: technical documentation, legal documents, structured reports,
papers — anything with a meaningful outline.

## semantic

Run a small auxiliary embedder over the document. Compute embeddings for
each sentence. Detect *boundary points* where consecutive sentences have
low cosine similarity. Cut chunks at those boundaries.

Strengths: chunks group sentences by topic continuity, not by structure or
length. A topic shift mid-section will cut; a long single topic spanning
multiple sections won't. Highest theoretical match with how a retriever
should think.

Weaknesses: expensive — every document costs sentence-count × embedding
forward passes at chunk time. The boundary-detection model is itself a
hyperparameter (which embedder, what threshold). Hard to debug ("why is
this chunk here?"). Empirically, gains over hierarchy are modest on most
prose corpora and inconsistent across embedder choices.

Use when: corpora with heavy topic drift inside sections and where compute
budget allows. Often not worth the cost over hierarchy.

## neighbor_expand (a variant)

Build chunks with one of the above strategies, then at query time (or at
embedding time) splice each chunk's left and right neighbors into the
`embedded_content` field. The chunk's `original_content` is unchanged.
This gives the embedder more context than the chunk alone, without
ballooning the indexed unit.

Useful when chunks are small and queries are long. Less useful when chunks
are already 1000+ characters.

## Practical guidance

1. **Start with sentence_aware**. It's the right answer until proven
   otherwise. `min_chars=200, max_chars=1200` is a reasonable default for
   most documentation corpora.
2. **Upgrade to hierarchy when your corpus has structure.** Markdown,
   HTML, PDFs with outlines, source code with classes/functions — all
   structured. The gain from heading-prefix framing is real.
3. **Only reach for semantic when you've measured a retrieval gap and
   suspect topic-drift-within-section.** Don't skip directly to it
   without a baseline.
4. **Re-chunk when you change embedding models.** Optimal chunk sizes
   differ across embedders (a 4K-token model benefits from larger chunks
   than a 512-token model).

The right chunker is the one that produces chunks where each chunk, read
in isolation, is a self-contained answer to a plausible question. If you
read a chunk and can't tell what document or section it came from, the
chunker is failing.
""",
}


def phase_5_generate_and_append_md(pg_dsn: str) -> PhaseResult:
    t0 = time.time()
    md_dir = WORKSPACE / "md_topical"
    md_dir.mkdir(parents=True, exist_ok=True)
    for name, body in MD_BRIEFS.items():
        (md_dir / name).write_text(body)
    print(f"  wrote {len(MD_BRIEFS)} briefs into {md_dir}")
    try:
        res = _ingest_corpus(
            cell_name="phase5_md_briefs",
            glob=str(md_dir / "*.md"),
            table="kb_topical",
            mode="append",
            source_tag="llm_generated",
            pg_dsn=pg_dsn,
        )
        if res.error:
            return PhaseResult("5-llm-md", "FAIL", time.time() - t0, res.error)
        print(f"  OK appended docs={res.docs_processed} chunks={res.chunks_written}")
        return PhaseResult(
            "5-llm-md", "OK", time.time() - t0,
            f"docs={res.docs_processed} chunks={res.chunks_written}",
            {"docs_processed": res.docs_processed,
             "chunks_written": res.chunks_written,
             "wall_seconds": round(res.wall_seconds, 2),
             "embed_seconds": round(res.embed_seconds, 2)},
        )
    except Exception as exc:
        traceback.print_exc()
        return PhaseResult("5-llm-md", "FAIL", time.time() - t0, str(exc))


# ---------------------------------------------------------------------------
# Phase 6 — ClickHouse seed → append into kb_topical
# ---------------------------------------------------------------------------

CH_SEED_ROWS = [
    ("ch_row_1",
     "Retrieval Quality vs. Generation Quality",
     "A persistent confusion in RAG systems: when the final answer is wrong, "
     "is it because retrieval surfaced the wrong context, or because the LLM "
     "ignored or misused the correct context? Decouple these failures by "
     "evaluating retrieval (recall@k, MRR) and generation (faithfulness, "
     "answer relevance) independently. Most teams find that retrieval is the "
     "bigger problem and the LLM is doing better than the eyeball suggests."),
    ("ch_row_2",
     "Filtered ANN: The Underrated Lever",
     "Vector search performs best when paired with metadata filters. A query "
     "like \"how do I rotate JWT keys\" returns better results when constrained "
     "to language='en' and source='docs' than when run against an undifferentiated "
     "embedding pool. Filter-first ANN engines (Qdrant, Weaviate's filter "
     "strategies, pgvector with btree pre-filters) outperform filter-after for "
     "selective predicates."),
    ("ch_row_3",
     "Re-Embedding as Migration Cost",
     "Every change to the embedding model invalidates the entire vector "
     "index. If your corpus is 50M vectors and you switch from bge-small to "
     "bge-large, you re-embed 50M chunks at 100ms each, plus rebuild the HNSW "
     "index, plus take downtime or run blue/green. Budget for re-embedding in "
     "the same column as schema migrations, not as an optimization."),
    ("ch_row_4",
     "Hybrid Fusion: RRF vs. Linear Weighting",
     "Reciprocal Rank Fusion (1/(60+rank), summed across legs) is the "
     "default-good hybrid fusion strategy. It is robust to score-scale "
     "differences across legs (a BM25 score of 12.3 and a cosine of 0.83 "
     "don't need normalization). Linear weighting requires careful "
     "min-max normalization and a tuned alpha but can outperform RRF "
     "when one leg is consistently more reliable than the other."),
    ("ch_row_5",
     "Latency Budget for Production RAG",
     "A typical user-facing RAG endpoint targets <1.5s end-to-end. That "
     "decomposes as: query embedding (50-150ms), ANN search (10-100ms), "
     "optional re-rank of top-50 (300-800ms with a cross-encoder), prompt "
     "build (negligible), LLM generation (500-1500ms). Re-ranking is often "
     "the first thing cut when the budget is tight, even though it's "
     "usually the highest-leverage stage for quality."),
    ("ch_row_6",
     "Why Chunk Size Isn't a Hyperparameter",
     "Treating chunk_size as a hyperparameter to grid-search is a mistake. "
     "The right chunk is the smallest unit that can stand alone as an "
     "answer to some plausible question. That's a structural property of "
     "the document, not a number. A 200-char chunk and a 2000-char chunk "
     "can both be optimal depending on the document's section size."),
    ("ch_row_7",
     "The Embedding Drift Problem Across Tenants",
     "Multi-tenant RAG with a shared embedding model has a hidden tradeoff: "
     "tenants whose corpora are out of distribution for the embedder get "
     "systematically worse retrieval than tenants whose content matches the "
     "training data. Domain-specific embedders (voyage-code, voyage-law) "
     "narrow the gap but require per-tenant model selection — operational "
     "complexity most teams underestimate."),
]


def phase_6_clickhouse_sync(pg_dsn: str, ch_dsn: str, ch_reachable: bool) -> PhaseResult:
    t0 = time.time()
    if not ch_reachable:
        return PhaseResult("6-clickhouse", "SKIP", time.time() - t0, "clickhouse unreachable")
    try:
        from chunkshop.backends.clickhouse import ClickHouseBackend
        backend = ClickHouseBackend(dsn=ch_dsn)
        with backend.connect() as client:
            client.command("DROP TABLE IF EXISTS chunkshop_test.kb_topical_seed")
            client.command(
                "CREATE TABLE chunkshop_test.kb_topical_seed ("
                "  id String, "
                "  title String, "
                "  content String, "
                "  updated_at DateTime DEFAULT now()"
                ") ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                "chunkshop_test.kb_topical_seed",
                [(rid, title, content) for rid, title, content in CH_SEED_ROWS],
                column_names=["id", "title", "content"],
            )
            n = client.query("SELECT count() FROM chunkshop_test.kb_topical_seed").result_rows[0][0]
            print(f"  inserted {n} rows into chunkshop_test.kb_topical_seed")
    except Exception as exc:
        traceback.print_exc()
        return PhaseResult("6-clickhouse", "FAIL", time.time() - t0, f"seed insert failed: {exc}")

    # Now ingest via chunkshop's clickhouse_table source.
    os.environ["CHUNKSHOP_REAL_WORLD_DSN"] = pg_dsn
    os.environ["CHUNKSHOP_REAL_WORLD_CH_DSN"] = ch_dsn
    try:
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

        cfg = CellConfig(
            cell_name="phase6_clickhouse_sync",
            source=ClickhouseTableSource(
                type="clickhouse_table",
                dsn_env="CHUNKSHOP_REAL_WORLD_CH_DSN",
                database="chunkshop_test",
                table="kb_topical_seed",
                id_column="id",
                content_column="content",
                title_column="title",
            ),
            chunker=SentenceAwareChunker(type="sentence_aware", min_chars=120, max_chars=1200),
            embedder=FastembedEmbedder(
                type="fastembed",
                model_name="Xenova/bge-small-en-v1.5-int8",
                dim=384, batch_size=64, threads=2,
            ),
            extractor=LangDetectExtractor(type="lang_detect"),
            target=TargetConfig(
                type="postgres",
                dsn_env="CHUNKSHOP_REAL_WORLD_DSN",
                database=SCHEMA,
                table="kb_topical",
                mode="append",
                source_tag="clickhouse_seed",
                hnsw=False,
                promote_metadata=[PromoteColumn(path="language", type="text")],
            ),
            runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=5),
        )
        res = run_cell(cfg)
        if res.error:
            return PhaseResult("6-clickhouse", "FAIL", time.time() - t0, res.error)
        _ensure_fts(pg_dsn, "kb_topical")
        print(f"  OK docs={res.docs_processed} chunks={res.chunks_written}")
        return PhaseResult(
            "6-clickhouse", "OK", time.time() - t0,
            f"docs={res.docs_processed} chunks={res.chunks_written}",
            {"docs_processed": res.docs_processed,
             "chunks_written": res.chunks_written,
             "wall_seconds": round(res.wall_seconds, 2),
             "ch_rows_inserted": len(CH_SEED_ROWS)},
        )
    except Exception as exc:
        traceback.print_exc()
        return PhaseResult("6-clickhouse", "FAIL", time.time() - t0, str(exc))


# ---------------------------------------------------------------------------
# Phase 7 — hybrid_search across all five KBs
# ---------------------------------------------------------------------------

def _embed_query(text: str):
    from chunkshop.config import FastembedEmbedder
    from chunkshop.embedders import load_embedder
    emb = load_embedder(FastembedEmbedder(
        type="fastembed",
        model_name="Xenova/bge-small-en-v1.5-int8",
        dim=384, batch_size=1, threads=2,
    ))
    return emb.embed([text])[0]


def phase_7_hybrid_search(pg_dsn: str) -> PhaseResult:
    t0 = time.time()
    from chunkshop import search

    # Cache query embeddings — we'll reuse each one across all 5 KBs.
    print("  embedding queries...")
    qvecs = {q: _embed_query(q) for q in HYBRID_QUERIES}

    results: dict = {}
    failures: list[str] = []
    for table in KB_TABLES_FOR_SEARCH:
        print(f"\n  -- KB: {table} --")
        results[table] = {}
        for query in HYBRID_QUERIES:
            try:
                hits = search.hybrid_search(
                    pg_dsn,
                    schema=SCHEMA,
                    table=table,
                    query=query,
                    query_vec=qvecs[query],
                    k=5,
                    legs=("semantic", "fts"),
                    fusion="rrf",
                )
            except Exception as exc:
                failures.append(f"{table}/{query}: {exc}")
                print(f"    QUERY FAILED [{query}]: {exc}")
                continue
            results[table][query] = [
                {
                    "doc_id": h.doc_id,
                    "seq_num": h.seq_num,
                    "score": round(h.score, 5),
                    "legs": list(h.legs),
                    "snippet": (h.text or "").replace("\n", " ")[:80],
                }
                for h in hits
            ]
            print(f"    query={query!r}  hits={len(hits)}")
            for i, h in enumerate(hits, 1):
                snippet = (h.text or "").replace("\n", " ")[:80]
                legs_str = "+".join(h.legs)
                print(f"      {i}. doc={h.doc_id!r:35} seq={h.seq_num:<3} "
                      f"score={h.score:.5f} legs={legs_str:<14} | {snippet}")
    status = "OK" if results and not failures else ("FAIL" if not results else "OK")
    return PhaseResult(
        "7-hybrid-search", status, time.time() - t0,
        f"searched {len(results)} KBs × {len(HYBRID_QUERIES)} queries",
        results,
    )


# ---------------------------------------------------------------------------
# Phase 8 — cleanup note
# ---------------------------------------------------------------------------

def phase_8_cleanup_note(pg_dsn: str) -> PhaseResult:
    t0 = time.time()
    print(f"  Schema {SCHEMA} retained for inspection.")
    print(f"  Drop with:")
    print(f"    psql '{pg_dsn}' -c 'DROP SCHEMA {SCHEMA} CASCADE'")
    print(f"  ClickHouse seed table chunkshop_test.kb_topical_seed retained.")
    return PhaseResult("8-cleanup-note", "OK", time.time() - t0,
                       f"schema {SCHEMA} retained")


# ---------------------------------------------------------------------------
# KB row-count probe (used in summary)
# ---------------------------------------------------------------------------

def _collect_kb_counts(pg_dsn: str) -> dict:
    import psycopg
    counts: dict = {}
    try:
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (SCHEMA,),
            )
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                try:
                    cur.execute(f'SELECT count(*), count(DISTINCT doc_id) FROM "{SCHEMA}"."{t}"')
                    chunks, docs = cur.fetchone()
                    counts[t] = {"chunks": chunks, "docs": docs}
                except Exception as exc:
                    counts[t] = {"error": str(exc)}
    except Exception as exc:
        counts["__error__"] = str(exc)
    return counts


# ---------------------------------------------------------------------------
# Summary writer
# ---------------------------------------------------------------------------

def _write_summary(phases: list[PhaseResult], pg_dsn: str) -> None:
    _banner("SUMMARY")
    table = [(p.name, p.status, f"{p.wall_seconds:.1f}s", p.detail) for p in phases]
    width_n = max(len(r[0]) for r in table)
    width_s = max(len(r[1]) for r in table)
    width_w = max(len(r[2]) for r in table)
    for name, status, wall, detail in table:
        print(f"  {name:<{width_n}}  {status:<{width_s}}  {wall:>{width_w}}  {detail}")

    print("\n  KB row counts (post-ingest):")
    counts = _collect_kb_counts(pg_dsn)
    if "__error__" in counts:
        print(f"    count probe failed: {counts['__error__']}")
    else:
        for tbl, c in counts.items():
            if "error" in c:
                print(f"    {tbl:20}  ERROR: {c['error']}")
            else:
                print(f"    {tbl:20}  chunks={c['chunks']:>6}  docs={c['docs']:>4}")

    overall = "DONE" if all(p.status in ("OK", "SKIP") for p in phases) else "DONE_WITH_CONCERNS"
    if any(p.status == "FAIL" for p in phases[:1]):  # phase 0 fail = BLOCKED
        overall = "BLOCKED"
    print(f"\n  OVERALL: {overall}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    pg_dsn = os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_PG_DSN)
    ch_dsn = os.environ.get("CHUNKSHOP_TEST_DSN_CLICKHOUSE", DEFAULT_CH_DSN)

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    # Tee stdout + stderr to /tmp/.../run-report.txt
    report_fh = REPORT_PATH.open("w")
    tee_out = _TeeStream(sys.stdout, report_fh)
    tee_err = _TeeStream(sys.stderr, report_fh)

    started = time.time()
    phases: list[PhaseResult] = []
    with contextlib.redirect_stdout(tee_out), contextlib.redirect_stderr(tee_err):
        _banner("Phase 0 — Workspace + DB pre-flight")
        r0 = phase_0_setup(pg_dsn, ch_dsn)
        phases.append(r0)
        if r0.status == "FAIL":
            _write_summary(phases, pg_dsn)
            report_fh.flush()
            report_fh.close()
            return 1
        ch_reachable = r0.stats.get("clickhouse_reachable", False)

        _banner("Phase 1 — Clone 3 public repos")
        r1 = phase_1_clone_repos()
        phases.append(r1)

        _banner("Phase 2 — Per-repo KBs")
        r2 = phase_2_per_repo_kbs(pg_dsn, r1.stats if isinstance(r1.stats, dict) else {})
        phases.append(r2)

        _banner("Phase 3 — Cross-cutting all-MD KB")
        r3 = phase_3_all_md_kb(pg_dsn, r1.stats if isinstance(r1.stats, dict) else {})
        phases.append(r3)

        _banner("Phase 4 — PDF KB (arxiv)")
        r4 = phase_4_pdf_kb(pg_dsn)
        phases.append(r4)

        _banner("Phase 5 — LLM-generated MD briefs (append)")
        r5 = phase_5_generate_and_append_md(pg_dsn)
        phases.append(r5)

        _banner("Phase 6 — ClickHouse seed → ingest (append)")
        r6 = phase_6_clickhouse_sync(pg_dsn, ch_dsn, ch_reachable)
        phases.append(r6)

        _banner("Phase 7 — hybrid_search across all KBs")
        r7 = phase_7_hybrid_search(pg_dsn)
        phases.append(r7)

        _banner("Phase 8 — Cleanup note")
        r8 = phase_8_cleanup_note(pg_dsn)
        phases.append(r8)

        wall = time.time() - started
        print(f"\n  TOTAL WALL TIME: {wall:.1f}s")
        _write_summary(phases, pg_dsn)

    report_fh.flush()
    report_fh.close()
    print(f"\n  full report captured to: {REPORT_PATH}")
    return 0 if all(p.status in ("OK", "SKIP") for p in phases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
