"""Full-factorial bakeoff: 3 embedders x 5 chunking strategies = 15 combos.

Usage:
    uv run python scripts/bench_matrix.py
    uv run python scripts/bench_matrix.py --dsn postgresql://... --keep-schema

Ingests `docs/samples/*-*.md` with every (chunker, embedder) combo into its own
table under a dedicated schema, runs the same gold-query set used by
`bench_embedders.py`, and scores recall@{1,3,5} + MRR per combo. Writes
results to `skill-output/bench-matrix/`.

The 5 chunking strategies, matching chunkshop's factorial convention:
  A = sentence_aware                                  (prose splitter)
  B = hierarchy                                       (heading-aware, default)
  C = fixed_overlap(window=300 words, step=150)       (dumb-but-predictable baseline)
  D = neighbor_expand(window=1) over sentence_aware   (context-augmented prose)
  E = neighbor_expand(window=1) over hierarchy        (context-augmented headings)

NOTE: 4 docs x 14 queries is low statistical power. A single query flipping
changes aggregate recall by ~0.07. Treat the numbers as directional, not
as a tournament winner declaration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python" / "src"))

from chunkshop.config import (  # noqa: E402
    CellConfig,
    FastembedEmbedder,
    FilesSource,
    FixedOverlapChunker,
    HierarchyChunker,
    IdentityFramerConfig,
    NeighborExpandChunker,
    NoneExtractor,
    RuntimeConfig,
    SentenceAwareChunker,
    TargetConfig,
)
from chunkshop.embedders import load_embedder  # noqa: E402
from chunkshop.runner import run_cell  # noqa: E402


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
BENCH_SCHEMA = "chunkshop_bench_matrix"
CORPUS_GLOB = str(REPO_ROOT / "docs" / "samples" / "*-*.md")
OUTPUT_DIR = REPO_ROOT / "skill-output" / "bench-matrix"


EMBEDDERS: list[dict[str, Any]] = [
    {
        "model_name": "Xenova/bge-small-en-v1.5-int8",
        "dim": 384,
        "short": "bge-small",
    },
    {
        "model_name": "Xenova/bge-base-en-v1.5-int8",
        "dim": 768,
        "short": "bge-base",
    },
    {
        "model_name": "nomic-ai/nomic-embed-text-v1.5-Q",
        "dim": 768,
        "short": "nomic-q",
    },
]

# Five chunking strategies. Each is referenced by single-letter key + a short
# label for reporting. `build` is a zero-arg callable returning a fresh
# chunker-config pydantic model (the config objects are not shared across
# cells because chunkshop's pydantic models are frozen).
STRATEGIES: list[dict[str, Any]] = [
    {
        "key": "A",
        "short": "sentence_aware",
        "build": lambda: SentenceAwareChunker(type="sentence_aware", doc_type="prose"),
    },
    {
        "key": "B",
        "short": "hierarchy",
        "build": lambda: HierarchyChunker(type="hierarchy", prefix_heading=True, min_section_chars=100),
    },
    {
        "key": "C",
        "short": "fixed_overlap",
        "build": lambda: FixedOverlapChunker(type="fixed_overlap", window_words=300, step_words=150),
    },
    {
        "key": "D",
        "short": "neighbor+sentence",
        "build": lambda: NeighborExpandChunker(
            type="neighbor_expand",
            base=SentenceAwareChunker(type="sentence_aware", doc_type="prose"),
            window=1,
        ),
    },
    {
        "key": "E",
        "short": "neighbor+hierarchy",
        "build": lambda: NeighborExpandChunker(
            type="neighbor_expand",
            base=HierarchyChunker(type="hierarchy", prefix_heading=True, min_section_chars=100),
            window=1,
        ),
    },
]


# Gold queries — same set used by bench_embedders.py (copy kept in-repo instead
# of importing so this script stays self-contained as a CI artifact).
GOLD_QUERIES: list[dict[str, str]] = [
    {"query": "what does Northwind Co. actually build", "gold_doc_id": "handbook-intro"},
    {"query": "how is the handbook organized", "gold_doc_id": "handbook-intro"},
    {"query": "who approves a pull request", "gold_doc_id": "handbook-engineering"},
    {"query": "should I mock the database in tests", "gold_doc_id": "handbook-engineering"},
    {"query": "how long does staging bake before production", "gold_doc_id": "handbook-engineering"},
    {"query": "what should I do if I see a production regression while on call", "gold_doc_id": "handbook-engineering"},
    {"query": "when does on-call rotation change over", "gold_doc_id": "handbook-engineering"},
    {"query": "how do we rotate API keys", "gold_doc_id": "handbook-security"},
    {"query": "where should credentials be stored", "gold_doc_id": "handbook-security"},
    {"query": "are post-mortems blameless", "gold_doc_id": "handbook-security"},
    {"query": "principle of least privilege audit cadence", "gold_doc_id": "handbook-security"},
    {"query": "what changed in the April release", "gold_doc_id": "release-notes"},
    {"query": "audit log retention period", "gold_doc_id": "release-notes"},
    {"query": "SKU search ranking change", "gold_doc_id": "release-notes"},
]


def _table_name(strategy_key: str, embedder_short: str) -> str:
    """lowercase, underscore-separated, pgvector-ident-safe."""
    return f"{strategy_key.lower()}_{embedder_short.replace('-', '_')}"


def build_cell_cfg(strategy: dict, embedder: dict, dsn_env: str) -> CellConfig:
    return CellConfig(
        cell_name=f"bench_{strategy['key']}_{embedder['short']}",
        source=FilesSource(type="files", glob=CORPUS_GLOB, id_from="stem", encoding="utf-8"),
        framer=IdentityFramerConfig(),
        chunker=strategy["build"](),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name=embedder["model_name"],
            dim=embedder["dim"],
            threads=4,
            batch_size=64,
        ),
        extractor=NoneExtractor(),
        target=TargetConfig(
            dsn_env=dsn_env,
            schema=BENCH_SCHEMA,
            table=_table_name(strategy["key"], embedder["short"]),
            mode="overwrite",
            hnsw=False,
        ),
        runtime=RuntimeConfig(
            omp_num_threads=4,
            heartbeat_every=50,
            log_path="/tmp/chunkshop-bench-matrix.log",
        ),
    )


def count_chunks(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                sql.Identifier(BENCH_SCHEMA), sql.Identifier(table)
            )
        )
        return cur.fetchone()[0]


def query_top_k(
    dsn: str, table: str, query_vec: np.ndarray, k: int = 5
) -> list[tuple[str, int]]:
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec.tolist()) + "]"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT doc_id, seq_num FROM {}.{} "
                "ORDER BY embedding <=> %s::vector LIMIT %s"
            ).format(sql.Identifier(BENCH_SCHEMA), sql.Identifier(table)),
            (vec_str, k),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def score_query(top_k: list[tuple[str, int]], gold_doc_id: str) -> dict[str, float]:
    doc_ids = [r[0] for r in top_k]
    r1 = 1 if (doc_ids and gold_doc_id == doc_ids[0]) else 0
    r3 = 1 if (gold_doc_id in doc_ids[:3]) else 0
    r5 = 1 if (gold_doc_id in doc_ids[:5]) else 0
    mrr = 0.0
    for rank, did in enumerate(doc_ids[:5], start=1):
        if did == gold_doc_id:
            mrr = 1.0 / rank
            break
    return {"recall_at_1": r1, "recall_at_3": r3, "recall_at_5": r5, "mrr": mrr}


def drop_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(BENCH_SCHEMA))
        )


def format_grid(
    rows: list[str],
    cols: list[str],
    cells: list[list[str]],
    title: str,
) -> str:
    """Render a 2D grid with row labels + col header."""
    header = "| " + title + " | " + " | ".join(cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    lines = [header, sep]
    for r, row_vals in zip(rows, cells):
        lines.append("| " + r + " | " + " | ".join(row_vals) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dsn",
        default=os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN),
        help=f"Postgres DSN (default: $CHUNKSHOP_TEST_DSN or {DEFAULT_DSN})",
    )
    ap.add_argument("--keep-schema", action="store_true")
    args = ap.parse_args()

    dsn_env = "CHUNKSHOP_BENCH_DSN"
    os.environ[dsn_env] = args.dsn

    try:
        with psycopg.connect(args.dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        print(f"ERROR: DSN {args.dsn} unreachable: {exc}", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_started = time.strftime("%Y-%m-%d %H:%M:%S")

    # ----------------------------- INGEST -----------------------------------
    ingest_stats: dict[str, dict[str, Any]] = {}  # keyed by "{strat_key}_{emb_short}"
    total = len(STRATEGIES) * len(EMBEDDERS)
    done = 0
    for strat in STRATEGIES:
        for emb in EMBEDDERS:
            done += 1
            combo = f"{strat['key']}_{emb['short']}"
            print(f"\n=== [{done}/{total}] Ingest {strat['short']} x {emb['short']} -> {_table_name(strat['key'], emb['short'])} ===")
            cfg = build_cell_cfg(strat, emb, dsn_env)
            t0 = time.time()
            res = run_cell(cfg)
            wall = time.time() - t0
            if res.error:
                print(f"ERROR: ingest failed for {combo}: {res.error}", file=sys.stderr)
                if not args.keep_schema:
                    drop_schema(args.dsn)
                return 3
            n_chunks = count_chunks(args.dsn, _table_name(strat["key"], emb["short"]))
            if n_chunks == 0:
                print(f"ERROR: 0 chunks for {combo}", file=sys.stderr)
                if not args.keep_schema:
                    drop_schema(args.dsn)
                return 4
            ingest_stats[combo] = {
                "chunks": n_chunks,
                "docs": res.docs_processed,
                "wall_seconds": round(wall, 2),
            }
            print(f"  -> {n_chunks} chunks, {wall:.1f}s")

    # ----------------------------- QUERY ------------------------------------
    # Cache one TextEmbedding per embedder (not one per combo).
    query_vecs_by_emb: dict[str, np.ndarray] = {}
    for emb in EMBEDDERS:
        print(f"\n=== Embedding {len(GOLD_QUERIES)} queries with {emb['short']} ===")
        qcfg = FastembedEmbedder(
            type="fastembed",
            model_name=emb["model_name"],
            dim=emb["dim"],
            threads=4,
            batch_size=64,
        )
        embedder = load_embedder(qcfg)
        vecs = embedder.embed([g["query"] for g in GOLD_QUERIES])
        if vecs.shape[0] != len(GOLD_QUERIES):
            print(f"ERROR: {emb['short']} produced wrong vector count", file=sys.stderr)
            if not args.keep_schema:
                drop_schema(args.dsn)
            return 5
        query_vecs_by_emb[emb["short"]] = vecs

    # Score each combo
    per_combo_scores: dict[str, dict[str, Any]] = {}  # combo -> aggregates + per_query
    for strat in STRATEGIES:
        for emb in EMBEDDERS:
            combo = f"{strat['key']}_{emb['short']}"
            vecs = query_vecs_by_emb[emb["short"]]
            table = _table_name(strat["key"], emb["short"])
            r1 = r3 = r5 = 0
            mrr_sum = 0.0
            per_query: list[dict[str, Any]] = []
            for i, g in enumerate(GOLD_QUERIES):
                top = query_top_k(args.dsn, table, vecs[i], k=5)
                s = score_query(top, g["gold_doc_id"])
                r1 += s["recall_at_1"]
                r3 += s["recall_at_3"]
                r5 += s["recall_at_5"]
                mrr_sum += s["mrr"]
                per_query.append({
                    "query": g["query"],
                    "gold_doc_id": g["gold_doc_id"],
                    "top_5": [{"doc_id": t[0], "seq_num": t[1]} for t in top],
                    **s,
                })
            n = len(GOLD_QUERIES)
            per_combo_scores[combo] = {
                "strategy": strat["short"],
                "strategy_key": strat["key"],
                "embedder": emb["short"],
                "recall_at_1": r1 / n,
                "recall_at_3": r3 / n,
                "recall_at_5": r5 / n,
                "mrr": mrr_sum / n,
                "per_query": per_query,
            }

    # ----------------------------- OUTPUT -----------------------------------
    results = {
        "run_meta": {
            "started_at": run_started,
            "dsn": args.dsn.split("@")[-1],
            "corpus_glob": CORPUS_GLOB,
            "n_queries": len(GOLD_QUERIES),
            "n_docs": len(list(Path(REPO_ROOT / "docs" / "samples").glob("*-*.md"))),
            "strategies": [{"key": s["key"], "short": s["short"]} for s in STRATEGIES],
            "embedders": [{"short": e["short"], "model": e["model_name"], "dim": e["dim"]} for e in EMBEDDERS],
        },
        "ingest_stats": ingest_stats,
        "combos": per_combo_scores,
        "gold_queries": GOLD_QUERIES,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(results, indent=2))

    # Build 2D grids for the report
    row_labels = [f"{s['key']}: `{s['short']}`" for s in STRATEGIES]
    col_labels = [f"`{e['short']}`" for e in EMBEDDERS]

    def cell_metric(metric: str) -> list[list[str]]:
        grid = []
        for strat in STRATEGIES:
            row = []
            for emb in EMBEDDERS:
                combo = f"{strat['key']}_{emb['short']}"
                v = per_combo_scores[combo][metric]
                row.append(f"{v:.3f}")
            grid.append(row)
        return grid

    # Best combo by MRR
    best_combo = max(per_combo_scores.items(), key=lambda kv: kv[1]["mrr"])

    report = [
        "# Full-factorial bakeoff: 5 chunkers x 3 embedders",
        "",
        f"- Run: {run_started}",
        f"- Corpus: `docs/samples/*-*.md` ({results['run_meta']['n_docs']} docs)",
        f"- Queries: {results['run_meta']['n_queries']} hand-written gold queries",
        f"- Total combos: {len(per_combo_scores)}",
        "",
        "## recall@1 grid",
        "",
        format_grid(row_labels, col_labels, cell_metric("recall_at_1"), "strategy \\ embedder"),
        "",
        "## recall@3 grid",
        "",
        format_grid(row_labels, col_labels, cell_metric("recall_at_3"), "strategy \\ embedder"),
        "",
        "## MRR grid",
        "",
        format_grid(row_labels, col_labels, cell_metric("mrr"), "strategy \\ embedder"),
        "",
        "## Chunk counts per combo",
        "",
        format_grid(
            row_labels,
            col_labels,
            [
                [str(ingest_stats[f"{s['key']}_{e['short']}"]["chunks"]) for e in EMBEDDERS]
                for s in STRATEGIES
            ],
            "strategy \\ embedder",
        ),
        "",
        "## Winner",
        "",
        f"**{best_combo[1]['strategy']} + {best_combo[1]['embedder']}** "
        f"(MRR={best_combo[1]['mrr']:.3f}, "
        f"r@1={best_combo[1]['recall_at_1']:.3f}).",
        "",
        "## Honesty note",
        "",
        f"4 docs x {len(GOLD_QUERIES)} queries is a low-statistical-power benchmark. "
        f"One query flipping moves r@1 by {1.0 / len(GOLD_QUERIES):.3f}. Combos within "
        "~0.07 of the leader are indistinguishable on this corpus — re-run with a "
        "bigger corpus / more queries before treating this as a tournament result.",
    ]
    (OUTPUT_DIR / "report.md").write_text("\n".join(report) + "\n")

    # ---------------------------- CLEANUP -----------------------------------
    if not args.keep_schema:
        drop_schema(args.dsn)
        print(f"\nDropped schema {BENCH_SCHEMA}")
    else:
        print(f"\nKept schema {BENCH_SCHEMA}")

    print("\n=== MRR grid ===")
    print(format_grid(row_labels, col_labels, cell_metric("mrr"), "strategy \\ embedder"))
    print(f"\nWinner: {best_combo[1]['strategy']} + {best_combo[1]['embedder']} (MRR={best_combo[1]['mrr']:.3f})")
    print(f"\nResults: {OUTPUT_DIR / 'results.json'}")
    print(f"Report:  {OUTPUT_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
