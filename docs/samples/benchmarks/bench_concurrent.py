"""Benchmark: concurrent-ingest throughput via `chunkshop orchestrate`.

Spawns the same cell N times against N distinct PG schemas, runs the
orchestrator at concurrency C, measures wall time. Compares C=1 (sequential)
to C={2, 4, 8} to characterize the orchestrator's fan-out behavior.

Each cell embeds the NTSB corpus (20 docs, ~75 chunks, sentence_aware +
bge-small-int8). Cell-level wall time is ~5s (mostly ORT session load +
embedding). With concurrency=N, theoretical speedup is N× minus
ORT-init contention.

Usage:
  export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
  cd python && .venv/bin/python ../skill-output/bench-concurrent/bench_concurrent.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "python" / "src"))

from chunkshop.orchestrator import orchestrate

OUTPUT_DIR = ROOT / "skill-output/bench-concurrent"
CELLS_DIR = OUTPUT_DIR / "cells"

N_CELLS = 8
CORPUS_GLOB = str(ROOT / "docs/samples/bakeoff-ntsb/corpus/*.md")

CELL_TEMPLATE = """cell_name: bench_concurrent_{i}

source:
  type: files
  glob: "{glob}"
  id_from: stem

framer:
  type: identity

chunker:
  type: sentence_aware

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 8
  threads: 2

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: bench_concurrent_{i}
  table: chunks
  mode: overwrite
  hnsw: false
  source_tag: bench_concurrent
"""


def _prepare_cells() -> list[Path]:
    if CELLS_DIR.exists():
        shutil.rmtree(CELLS_DIR)
    CELLS_DIR.mkdir(parents=True)
    paths: list[Path] = []
    for i in range(N_CELLS):
        p = CELLS_DIR / f"cell-{i:02d}.yaml"
        p.write_text(CELL_TEMPLATE.format(i=i, glob=CORPUS_GLOB))
        paths.append(p)
    return paths


def _drop_schemas() -> None:
    dsn = os.environ["CHUNKSHOP_TEST_DSN"]
    with psycopg.connect(dsn) as conn:
        for i in range(N_CELLS):
            conn.execute(f'DROP SCHEMA IF EXISTS "bench_concurrent_{i}" CASCADE')
        conn.commit()


def main() -> int:
    if "CHUNKSHOP_TEST_DSN" not in os.environ:
        print("CHUNKSHOP_TEST_DSN not set")
        return 1

    cell_paths = _prepare_cells()
    print(f"[bench-concurrent] prepared {len(cell_paths)} cell YAMLs in {CELLS_DIR}")

    results: dict[str, dict] = {}
    for concurrency in (1, 2, 4, 8):
        print(f"\n[bench-concurrent] === concurrency={concurrency} ===")
        _drop_schemas()  # fresh start each pass
        t0 = time.perf_counter()
        res = orchestrate(
            configs=cell_paths,
            concurrency=concurrency,
            overall_timeout_seconds=600,
        )
        wall = time.perf_counter() - t0
        per_cell_walls = [c["wall_seconds"] for c in res.cells]
        results[f"concurrency_{concurrency}"] = {
            "total_wall_seconds": round(wall, 2),
            "succeeded": res.succeeded,
            "failed": res.failed,
            "per_cell_walls": per_cell_walls,
            "mean_per_cell_wall": round(sum(per_cell_walls) / len(per_cell_walls), 2),
            "max_per_cell_wall": round(max(per_cell_walls), 2),
            "throughput_cells_per_sec": round(N_CELLS / wall, 3),
        }
        print(f"  total wall: {wall:.2f}s")
        print(f"  succeeded: {res.succeeded}/{len(cell_paths)}")
        print(f"  mean per-cell wall: {results[f'concurrency_{concurrency}']['mean_per_cell_wall']}s")
        print(f"  throughput: {results[f'concurrency_{concurrency}']['throughput_cells_per_sec']} cells/s")

    # Comparison: speedup vs concurrency=1
    seq_wall = results["concurrency_1"]["total_wall_seconds"]
    print("\n[bench-concurrent] === speedup vs concurrency=1 ===")
    for k, v in results.items():
        c = int(k.split("_")[1])
        speedup = seq_wall / v["total_wall_seconds"]
        efficiency = speedup / c * 100  # % of ideal scaling
        v["speedup"] = round(speedup, 2)
        v["scaling_efficiency_pct"] = round(efficiency, 1)
        print(f"  c={c}: {v['total_wall_seconds']}s  speedup={speedup:.2f}×  scaling efficiency={efficiency:.0f}%")

    out_json = OUTPUT_DIR / "results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\n[bench-concurrent] wrote {out_json}")

    _drop_schemas()
    print("[bench-concurrent] cleaned up bench schemas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
