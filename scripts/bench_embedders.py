"""Head-to-head retrieval benchmark for three int8 embedders on chunkshop's own sample corpus.

Usage:
    uv run python scripts/bench_embedders.py
    uv run python scripts/bench_embedders.py --dsn postgresql://... --keep-schema

Ingests `docs/samples/*-*.md` three times — once per embedder — into a dedicated
schema, runs a fixed set of gold queries, and scores recall@{1,3,5} + MRR. Writes
raw results to `skill-output/bench-embedders/results.json` and a human-readable
summary to `skill-output/bench-embedders/report.md`. Drops the schema on exit
unless `--keep-schema` is passed.

NOTE: 4 docs x ~12 queries is a low-statistical-power benchmark. Treat the numbers
as directional signal, not as a winner declaration.
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

# Ensure local chunkshop is importable even when run from repo root without install.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "python" / "src"))

from chunkshop.config import (  # noqa: E402
    CellConfig,
    FastembedEmbedder,
    FilesSource,
    HierarchyChunker,
    IdentityFramerConfig,
    NoneExtractor,
    RuntimeConfig,
    TargetConfig,
)
from chunkshop.embedders import load_embedder  # noqa: E402
from chunkshop.runner import run_cell  # noqa: E402


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
BENCH_SCHEMA = "chunkshop_bench_embedders"
CORPUS_GLOB = str(REPO_ROOT / "docs" / "samples" / "*-*.md")
OUTPUT_DIR = REPO_ROOT / "skill-output" / "bench-embedders"

# Three int8 embedders under test. Each writes to its own table within BENCH_SCHEMA.
EMBEDDERS = [
    {
        "model_name": "Xenova/bge-small-en-v1.5-int8",
        "dim": 384,
        "table": "bge_small_int8",
        "short": "bge-small-int8",
    },
    {
        "model_name": "Xenova/bge-base-en-v1.5-int8",
        "dim": 768,
        "table": "bge_base_int8",
        "short": "bge-base-int8",
    },
    {
        "model_name": "nomic-ai/nomic-embed-text-v1.5-Q",
        "dim": 768,
        "table": "nomic_q",
        "short": "nomic-q",
    },
]

# Gold queries: written BEFORE inspecting retrieval results. Each entry names the
# expected `doc_id` (markdown filename stem) and an optional section heading that
# a human would expect to match. We score on doc_id only (chunk-level would be
# too noisy on 4 docs). Mix of easy (direct keyword) and harder (paraphrased)
# questions, spanning all 4 docs in the corpus.
GOLD_QUERIES: list[dict[str, str]] = [
    # handbook-intro — easier to miss because it's the shortest doc
    {
        "query": "what does Northwind Co. actually build",
        "gold_doc_id": "handbook-intro",
        "gold_section": "What we do",
    },
    {
        "query": "how is the handbook organized",
        "gold_doc_id": "handbook-intro",
        "gold_section": "How this handbook is structured",
    },
    # handbook-engineering — code review, testing, deployment, on-call
    {
        "query": "who approves a pull request",
        "gold_doc_id": "handbook-engineering",
        "gold_section": "Code review",
    },
    {
        "query": "should I mock the database in tests",
        "gold_doc_id": "handbook-engineering",
        "gold_section": "Testing",
    },
    {
        "query": "how long does staging bake before production",
        "gold_doc_id": "handbook-engineering",
        "gold_section": "Deployment",
    },
    {
        "query": "what should I do if I see a production regression while on call",
        "gold_doc_id": "handbook-engineering",
        "gold_section": "On-call",
    },
    {
        "query": "when does on-call rotation change over",
        "gold_doc_id": "handbook-engineering",
        "gold_section": "On-call",
    },
    # handbook-security — secrets, least privilege, incident response
    {
        "query": "how do we rotate API keys",
        "gold_doc_id": "handbook-security",
        "gold_section": "Secrets management",
    },
    {
        "query": "where should credentials be stored",
        "gold_doc_id": "handbook-security",
        "gold_section": "Secrets management",
    },
    {
        "query": "are post-mortems blameless",
        "gold_doc_id": "handbook-security",
        "gold_section": "Incident response",
    },
    {
        "query": "principle of least privilege audit cadence",
        "gold_doc_id": "handbook-security",
        "gold_section": "Least privilege",
    },
    # release-notes — April 2026 changes
    {
        "query": "what changed in the April release",
        "gold_doc_id": "release-notes",
        "gold_section": "",
    },
    {
        "query": "audit log retention period",
        "gold_doc_id": "release-notes",
        "gold_section": "",
    },
    {
        "query": "SKU search ranking change",
        "gold_doc_id": "release-notes",
        "gold_section": "",
    },
]


def build_cell_cfg(model_name: str, dim: int, table: str, dsn_env: str) -> CellConfig:
    """Build a CellConfig that ingests docs/samples/*-*.md with hierarchy + the given embedder."""
    return CellConfig(
        cell_name=f"bench_{table}",
        source=FilesSource(type="files", glob=CORPUS_GLOB, id_from="stem", encoding="utf-8"),
        framer=IdentityFramerConfig(),
        chunker=HierarchyChunker(type="hierarchy", prefix_heading=True, min_section_chars=100),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name=model_name,
            dim=dim,
            threads=4,
            batch_size=64,
        ),
        extractor=NoneExtractor(),
        target=TargetConfig(
            dsn_env=dsn_env,
            schema=BENCH_SCHEMA,
            table=table,
            overwrite=True,
            hnsw=False,  # 4 docs is too small for HNSW to beat seq scan
            mode="overwrite",
        ),
        runtime=RuntimeConfig(
            omp_num_threads=4,
            heartbeat_every=5,
            log_path="/tmp/chunkshop-bench-embedders.log",
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
) -> list[tuple[str, int, str]]:
    """Return top-k (doc_id, seq_num, original_content[:80]) by cosine similarity."""
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec.tolist()) + "]"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT doc_id, seq_num, left(original_content, 80) "
                "FROM {}.{} ORDER BY embedding <=> %s::vector LIMIT %s"
            ).format(sql.Identifier(BENCH_SCHEMA), sql.Identifier(table)),
            (vec_str, k),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def score_query(top_k: list[tuple[str, int, str]], gold_doc_id: str) -> dict[str, float]:
    """Compute recall@1/3/5 and MRR against top-5. Returns ints for recall, float for MRR."""
    doc_ids = [r[0] for r in top_k]
    recall_at_1 = 1 if (len(doc_ids) >= 1 and gold_doc_id in doc_ids[:1]) else 0
    recall_at_3 = 1 if (gold_doc_id in doc_ids[:3]) else 0
    recall_at_5 = 1 if (gold_doc_id in doc_ids[:5]) else 0
    mrr = 0.0
    for rank, did in enumerate(doc_ids[:5], start=1):
        if did == gold_doc_id:
            mrr = 1.0 / rank
            break
    return {
        "recall_at_1": recall_at_1,
        "recall_at_3": recall_at_3,
        "recall_at_5": recall_at_5,
        "mrr": mrr,
    }


def drop_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(BENCH_SCHEMA))
        )


def format_aggregates_table(aggregates: list[dict[str, Any]]) -> str:
    lines = [
        "| Embedder | recall@1 | recall@3 | recall@5 | MRR |",
        "|----------|---------:|---------:|---------:|----:|",
    ]
    for a in aggregates:
        lines.append(
            f"| `{a['short']}` (dim {a['dim']}) "
            f"| {a['recall_at_1']:.3f} | {a['recall_at_3']:.3f} | "
            f"{a['recall_at_5']:.3f} | {a['mrr']:.3f} |"
        )
    return "\n".join(lines)


def format_per_query_table(
    gold: list[dict[str, str]], per_query: dict[str, list[dict[str, Any]]]
) -> str:
    header_embs = [e["short"] for e in EMBEDDERS]
    lines = [
        "| # | Query | Gold doc | " + " | ".join(f"{n} (r@1 / MRR)" for n in header_embs) + " |",
        "|---|-------|----------|" + "|".join(["---"] * len(header_embs)) + "|",
    ]
    for i, g in enumerate(gold, start=1):
        cells = [f"{i}", f"{g['query']}", f"`{g['gold_doc_id']}`"]
        for e in EMBEDDERS:
            rec = per_query[e["short"]][i - 1]
            cells.append(f"{rec['recall_at_1']} / {rec['mrr']:.2f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dsn",
        default=os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN),
        help=f"Postgres DSN (default: $CHUNKSHOP_TEST_DSN or {DEFAULT_DSN})",
    )
    ap.add_argument(
        "--keep-schema",
        action="store_true",
        help=f"Do not drop {BENCH_SCHEMA} on exit",
    )
    args = ap.parse_args()

    # Tell chunkshop's sink how to find the DB. It reads from env vars named by
    # `target.dsn_env`; we set a dedicated var so we do not trample a caller's.
    dsn_env = "CHUNKSHOP_BENCH_DSN"
    os.environ[dsn_env] = args.dsn

    # Early reachability check so we fail loudly instead of silently skipping.
    try:
        with psycopg.connect(args.dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        print(f"ERROR: DSN {args.dsn} unreachable: {exc}", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_meta = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dsn": args.dsn.split("@")[-1],  # host/db only, no creds
        "corpus_glob": CORPUS_GLOB,
        "chunker": "hierarchy(prefix_heading=true, min_section_chars=100)",
        "n_queries": len(GOLD_QUERIES),
        "n_docs": len(list(Path(REPO_ROOT / "docs" / "samples").glob("*-*.md"))),
    }

    # --- Ingest: one run per embedder -----------------------------------
    ingest_stats: dict[str, dict[str, Any]] = {}
    for emb in EMBEDDERS:
        print(f"\n=== Ingesting with {emb['short']} -> {BENCH_SCHEMA}.{emb['table']} ===")
        cfg = build_cell_cfg(emb["model_name"], emb["dim"], emb["table"], dsn_env)
        t0 = time.time()
        result = run_cell(cfg)
        wall = time.time() - t0
        if result.error:
            print(f"ERROR: ingest failed for {emb['short']}: {result.error}", file=sys.stderr)
            if not args.keep_schema:
                drop_schema(args.dsn)
            return 3
        n_chunks = count_chunks(args.dsn, emb["table"])
        if n_chunks == 0:
            print(f"ERROR: 0 chunks written for {emb['short']} — aborting", file=sys.stderr)
            if not args.keep_schema:
                drop_schema(args.dsn)
            return 4
        ingest_stats[emb["short"]] = {
            "chunks": n_chunks,
            "docs": result.docs_processed,
            "wall_seconds": round(wall, 2),
        }
        print(
            f"  -> {result.docs_processed} docs, {n_chunks} chunks, {wall:.1f}s wall"
        )

    # --- Query: embed each gold query with each model, score top-5 --------
    per_query: dict[str, list[dict[str, Any]]] = {e["short"]: [] for e in EMBEDDERS}
    aggregates: list[dict[str, Any]] = []

    for emb in EMBEDDERS:
        print(f"\n=== Querying with {emb['short']} ===")
        # Re-use chunkshop's loader so the query embedding is produced the same
        # way as the ingest embedding (batched fastembed, same normalization).
        qcfg = FastembedEmbedder(
            type="fastembed",
            model_name=emb["model_name"],
            dim=emb["dim"],
            threads=4,
            batch_size=64,
        )
        embedder = load_embedder(qcfg)
        query_texts = [g["query"] for g in GOLD_QUERIES]
        query_vecs = embedder.embed(query_texts)
        if query_vecs.shape[0] != len(GOLD_QUERIES):
            print(
                f"ERROR: {emb['short']} produced {query_vecs.shape[0]} query vectors, "
                f"expected {len(GOLD_QUERIES)}",
                file=sys.stderr,
            )
            if not args.keep_schema:
                drop_schema(args.dsn)
            return 5
        if not np.all(np.any(query_vecs != 0.0, axis=1)):
            print(
                f"ERROR: {emb['short']} produced at least one zero-length vector",
                file=sys.stderr,
            )
            if not args.keep_schema:
                drop_schema(args.dsn)
            return 6

        # Per-query scores
        r1_sum = r3_sum = r5_sum = 0
        mrr_sum = 0.0
        for i, g in enumerate(GOLD_QUERIES):
            top_k = query_top_k(args.dsn, emb["table"], query_vecs[i], k=5)
            score = score_query(top_k, g["gold_doc_id"])
            r1_sum += score["recall_at_1"]
            r3_sum += score["recall_at_3"]
            r5_sum += score["recall_at_5"]
            mrr_sum += score["mrr"]
            per_query[emb["short"]].append(
                {
                    "query": g["query"],
                    "gold_doc_id": g["gold_doc_id"],
                    "top_5": [{"doc_id": t[0], "seq_num": t[1], "preview": t[2]} for t in top_k],
                    **score,
                }
            )
        n = len(GOLD_QUERIES)
        aggregates.append(
            {
                "short": emb["short"],
                "model_name": emb["model_name"],
                "dim": emb["dim"],
                "recall_at_1": r1_sum / n,
                "recall_at_3": r3_sum / n,
                "recall_at_5": r5_sum / n,
                "mrr": mrr_sum / n,
            }
        )
        print(
            f"  r@1={r1_sum / n:.3f}  r@3={r3_sum / n:.3f}  "
            f"r@5={r5_sum / n:.3f}  MRR={mrr_sum / n:.3f}"
        )

    # --- Write results.json -----------------------------------------------
    results = {
        "run_meta": run_meta,
        "ingest_stats": ingest_stats,
        "aggregates": aggregates,
        "per_query": per_query,
        "gold_queries": GOLD_QUERIES,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(results, indent=2))

    # --- Write report.md --------------------------------------------------
    report_lines = [
        "# Embedder head-to-head on docs/samples",
        "",
        f"- Run date: {run_meta['started_at']}",
        f"- Corpus: `docs/samples/*-*.md` ({run_meta['n_docs']} docs)",
        f"- Chunker: {run_meta['chunker']}",
        f"- Queries: {run_meta['n_queries']} hand-written gold queries",
        f"- DB: `{run_meta['dsn']}` (schema `{BENCH_SCHEMA}`)",
        "",
        "## Corpus stats (chunks per table after ingest)",
        "",
        "| Embedder | chunks | wall_sec |",
        "|----------|-------:|---------:|",
    ]
    for emb in EMBEDDERS:
        s = ingest_stats[emb["short"]]
        report_lines.append(f"| `{emb['short']}` | {s['chunks']} | {s['wall_seconds']} |")
    report_lines.append("")
    report_lines.append("## Aggregates (mean across all queries)")
    report_lines.append("")
    report_lines.append(format_aggregates_table(aggregates))
    report_lines.append("")
    report_lines.append("## Per-query detail (top-5 retrieval, `recall@1 / MRR`)")
    report_lines.append("")
    report_lines.append(format_per_query_table(GOLD_QUERIES, per_query))
    report_lines.append("")
    report_lines.append("## Honesty note")
    report_lines.append("")
    report_lines.append(
        "4 docs x "
        + str(len(GOLD_QUERIES))
        + " queries is a low-statistical-power benchmark. A single query flipping "
        "changes aggregate recall by "
        f"{1.0 / len(GOLD_QUERIES):.3f}. Treat these as directional signal only; "
        "broader benchmarks like MTEB evaluate on tens of thousands of queries."
    )

    (OUTPUT_DIR / "report.md").write_text("\n".join(report_lines) + "\n")

    # --- Cleanup ----------------------------------------------------------
    if not args.keep_schema:
        drop_schema(args.dsn)
        print(f"\nDropped schema {BENCH_SCHEMA}")
    else:
        print(f"\nKept schema {BENCH_SCHEMA} (pass without --keep-schema to drop)")

    print("\n=== Aggregates ===")
    print(format_aggregates_table(aggregates))
    print(f"\nResults: {OUTPUT_DIR / 'results.json'}")
    print(f"Report:  {OUTPUT_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
