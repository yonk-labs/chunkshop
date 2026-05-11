"""Benchmark: HNSW recall + query latency vs brute-force on Postgres.

Ingests the NTSB corpus twice — once with `hnsw: true` (pgvector HNSW index)
and once with `hnsw: false` (sequential scan with cosine distance). Runs the
12 NTSB gold queries against each table, measuring MRR + per-query wall
time.

Configuration: sentence_aware chunker + Xenova/bge-small-en-v1.5-int8 — the
best-scoring (chunker, embedder) combo on NTSB from the existing mega-table
bakeoff (MRR=0.903 brute-force across all 4 backends).

Usage:
  export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
  cd python && .venv/bin/python ../skill-output/bench-hnsw/bench_hnsw.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import psycopg
import yaml

# Make the chunkshop package importable from its src/ layout.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "python" / "src"))

from chunkshop.config import load_config
from chunkshop.embedders import load_embedder
from chunkshop.runner import run_cell

# Configurable via env: BENCH_CORPUS=ntsb|scotus
CORPUS_NAME = os.environ.get("BENCH_CORPUS", "ntsb")
if CORPUS_NAME == "scotus":
    GOLD_FILE = ROOT / "docs/samples/bakeoff-scotus/gold-scotus.yaml"
    SOURCE_BLOCK = (
        "type: json_corpus\n"
        "  path: /home/yonk/yonk-tools/pg-raggraph/benchmarks/age-bakeoff/src/age_bakeoff/extraction/data/scotus.json\n"
        "  documents_key: documents\n"
        "  id_field: id\n"
        "  content_field: content\n"
        "  title_field: title"
    )
elif CORPUS_NAME == "ntsb":
    GOLD_FILE = ROOT / "docs/samples/bakeoff-ntsb/gold-ntsb.yaml"
    SOURCE_BLOCK = (
        "type: files\n"
        f"  glob: \"{ROOT / 'docs/samples/bakeoff-ntsb/corpus/*.md'}\"\n"
        "  id_from: stem"
    )
else:
    raise SystemExit(f"unknown BENCH_CORPUS={CORPUS_NAME!r}; expected 'ntsb' or 'scotus'")

OUTPUT_DIR = ROOT / "skill-output/bench-hnsw"

# Chunker varies by corpus. NTSB is small (20 docs) — use sentence_aware,
# the best-MRR chunker from the existing mega-table bakeoff. SCOTUS is larger
# (772 docs); use fixed_overlap with small windows to produce ~4k chunks
# where HNSW starts winning materially over brute-force.
if CORPUS_NAME == "ntsb":
    CHUNKER_BLOCK = "type: sentence_aware"
elif CORPUS_NAME == "scotus":
    CHUNKER_BLOCK = (
        "type: fixed_overlap\n"
        "  window_words: 100\n"
        "  step_words: 50"
    )

CELL_TEMPLATE = """cell_name: {cell_name}

source:
  {source_block}

framer:
  type: identity

chunker:
  {chunker_block}

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 8
  threads: 2

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: bench_hnsw_{corpus}_{schema_suffix}
  table: chunks
  mode: overwrite
  hnsw: {hnsw}
  source_tag: bench_hnsw
"""


def _build_cell_yaml(cell_name: str, hnsw: bool) -> Path:
    suffix = "on" if hnsw else "off"
    path = OUTPUT_DIR / f"cell-{CORPUS_NAME}-{suffix}.yaml"
    path.write_text(CELL_TEMPLATE.format(
        cell_name=cell_name,
        source_block=SOURCE_BLOCK,
        chunker_block=CHUNKER_BLOCK,
        corpus=CORPUS_NAME,
        schema_suffix=suffix,
        hnsw=str(hnsw).lower(),
    ))
    return path


def _ingest(cell_yaml: Path) -> dict:
    cfg = load_config(cell_yaml)
    t0 = time.perf_counter()
    result = run_cell(cfg)
    wall = time.perf_counter() - t0
    return {
        "cell": result.cell_name,
        "docs_processed": result.docs_processed,
        "chunks_written": result.chunks_written,
        "wall_seconds": round(wall, 3),
        "embed_seconds": round(result.embed_seconds, 3),
    }


def _load_gold():
    return yaml.safe_load(GOLD_FILE.read_text())


def _run_queries(schema: str, embedder, gold: list[dict]) -> tuple[float, list[float], list[dict]]:
    """Run gold queries; return (MRR, per-query latencies ms, per-query records)."""
    dsn = os.environ["CHUNKSHOP_TEST_DSN"]
    per_query_records: list[dict] = []
    latencies: list[float] = []
    reciprocal_ranks: list[float] = []

    with psycopg.connect(dsn) as conn:
        for entry in gold:
            query = entry["query"]
            gold_doc = entry["gold_doc_id"]
            [qvec] = embedder.embed([query])
            qvec_str = "[" + ",".join(f"{v:.7f}" for v in qvec) + "]"

            t0 = time.perf_counter()
            rows = conn.execute(
                f'SELECT doc_id, seq_num, embedding <=> %s::vector AS distance '
                f'FROM "{schema}"."chunks" ORDER BY distance LIMIT 5',
                (qvec_str,),
            ).fetchall()
            latencies.append((time.perf_counter() - t0) * 1000.0)  # ms

            doc_ids_in_order: list[str] = []
            seen: set[str] = set()
            for r in rows:
                did = r[0]
                if did not in seen:
                    doc_ids_in_order.append(did)
                    seen.add(did)

            rank = None
            for i, did in enumerate(doc_ids_in_order, start=1):
                if did == gold_doc:
                    rank = i
                    break
            rr = (1.0 / rank) if rank else 0.0
            reciprocal_ranks.append(rr)
            per_query_records.append({
                "query": query[:80] + ("..." if len(query) > 80 else ""),
                "gold_doc_id": gold_doc,
                "top1": doc_ids_in_order[0] if doc_ids_in_order else None,
                "rank": rank,
                "rr": round(rr, 3),
                "latency_ms": round(latencies[-1], 2),
            })

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
    return mrr, latencies, per_query_records


def _drop_schema(schema: str) -> None:
    dsn = os.environ["CHUNKSHOP_TEST_DSN"]
    with psycopg.connect(dsn) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


def main() -> int:
    if "CHUNKSHOP_TEST_DSN" not in os.environ:
        print("CHUNKSHOP_TEST_DSN not set — aborting.")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    gold = _load_gold()
    print(f"[bench-hnsw] loaded {len(gold)} gold queries")

    results: dict[str, dict] = {}

    for hnsw_flag in (False, True):
        suffix = "on" if hnsw_flag else "off"
        schema = f"bench_hnsw_{CORPUS_NAME}_{suffix}"
        cell_yaml = _build_cell_yaml(f"bench_hnsw_{CORPUS_NAME}_{suffix}", hnsw_flag)
        print(f"\n[bench-hnsw] === HNSW={hnsw_flag} ===")
        print(f"[bench-hnsw] ingesting via {cell_yaml.name}")
        ingest_res = _ingest(cell_yaml)
        print(f"  ingest: {ingest_res}")

        # Build a stand-alone embedder for queries (uses the same model the
        # cell embedded with).
        print("[bench-hnsw] loading query-side embedder")
        from chunkshop.config import FastembedEmbedder
        embedder = load_embedder(FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-small-en-v1.5-int8",
            dim=384,
            batch_size=8,
            threads=2,
        ))

        mrr, latencies, per_query = _run_queries(schema, embedder, gold)

        results[f"hnsw_{suffix}"] = {
            "ingest": ingest_res,
            "mrr": round(mrr, 4),
            "query_latency_ms_mean": round(statistics.mean(latencies), 2),
            "query_latency_ms_min": round(min(latencies), 2),
            "query_latency_ms_max": round(max(latencies), 2),
            "query_latency_ms_p50": round(statistics.median(latencies), 2),
            "query_latency_ms_p95": round(
                statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies),
                2,
            ),
            "per_query": per_query,
        }
        print(f"  MRR: {results[f'hnsw_{suffix}']['mrr']}")
        print(f"  query latency: mean={results[f'hnsw_{suffix}']['query_latency_ms_mean']}ms "
              f"min={results[f'hnsw_{suffix}']['query_latency_ms_min']}ms "
              f"max={results[f'hnsw_{suffix}']['query_latency_ms_max']}ms")

    # Comparison
    on = results["hnsw_on"]
    off = results["hnsw_off"]
    print("\n[bench-hnsw] === comparison ===")
    print(f"  MRR delta (on - off): {on['mrr'] - off['mrr']:+.4f}")
    print(f"  mean query latency speedup: {off['query_latency_ms_mean'] / on['query_latency_ms_mean']:.2f}× "
          f"({off['query_latency_ms_mean']}ms → {on['query_latency_ms_mean']}ms)")
    print(f"  ingest wall delta: {on['ingest']['wall_seconds'] - off['ingest']['wall_seconds']:+.2f}s "
          f"(HNSW build cost)")

    out_json = OUTPUT_DIR / f"results-{CORPUS_NAME}.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\n[bench-hnsw] wrote {out_json}")

    # Cleanup
    _drop_schema(f"bench_hnsw_{CORPUS_NAME}_on")
    _drop_schema(f"bench_hnsw_{CORPUS_NAME}_off")
    print("[bench-hnsw] dropped both bench schemas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
