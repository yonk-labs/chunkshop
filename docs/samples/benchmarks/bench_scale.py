"""Benchmark: ingest + query throughput at ~10k chunks across all 4 backends.

Ingests the SCOTUS corpus with fixed_overlap(window=50, step=25) on each
backend, then runs the 12 SCOTUS gold queries. Captures:
  - ingest wall time (separately: pipeline-overhead vs embedder)
  - chunk count
  - query latency (per-query + mean + p95)
  - MRR (sanity check that vectors round-trip)

Skips backends whose DSN env var is unset.

Usage:
  export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
  export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
  export CHUNKSHOP_TEST_DSN_CH="clickhouse://default:chpw@localhost:8124/chunkshop_test"
  export SQLITE_SCALE_PATH=/tmp/bench-scale.db
  cd python && .venv/bin/python ../skill-output/bench-scale/bench_scale.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "python" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import vector_text  # noqa: E402
from chunkshop.config import load_config, FastembedEmbedder  # noqa: E402
from chunkshop.embedders import load_embedder  # noqa: E402
from chunkshop.runner import run_cell  # noqa: E402

OUTPUT_DIR = ROOT / "skill-output/bench-scale"
GOLD_FILE = ROOT / "docs/samples/bakeoff-scotus/gold-scotus.yaml"
SCOTUS_JSON = "/home/yonk/yonk-tools/pg-raggraph/benchmarks/age-bakeoff/src/age_bakeoff/extraction/data/scotus.json"


def _cell_yaml(backend: str) -> str:
    source_block = (
        f"type: json_corpus\n"
        f"  path: {SCOTUS_JSON}\n"
        f"  documents_key: documents\n"
        f"  id_field: id\n"
        f"  content_field: content\n"
        f"  title_field: title"
    )
    target = {
        "postgres": (
            "type: postgres\n"
            "  dsn_env: CHUNKSHOP_TEST_DSN\n"
            "  database: bench_scale_pg\n"
            "  table: chunks\n"
            "  mode: overwrite\n"
            "  hnsw: true\n"
            "  source_tag: bench_scale"
        ),
        "mariadb": (
            "type: mariadb\n"
            "  dsn_env: CHUNKSHOP_TEST_DSN_MARIADB\n"
            "  database: bench_scale_md\n"
            "  table: chunks\n"
            "  mode: overwrite\n"
            "  hnsw: true\n"
            "  source_tag: bench_scale"
        ),
        "sqlite": (
            "type: sqlite\n"
            "  dsn_env: SQLITE_SCALE_PATH\n"
            "  database: ignored\n"
            "  table: chunks\n"
            "  mode: overwrite\n"
            "  source_tag: bench_scale"
        ),
        "clickhouse": (
            "type: clickhouse\n"
            "  dsn_env: CHUNKSHOP_TEST_DSN_CH\n"
            "  database: bench_scale_ch\n"
            "  table: chunks\n"
            "  mode: overwrite\n"
            "  source_tag: bench_scale"
        ),
    }[backend]
    return f"""cell_name: bench_scale_{backend}

source:
  {source_block}

framer:
  type: identity

chunker:
  type: fixed_overlap
  window_words: 50
  step_words: 25

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 16
  threads: 2

target:
  {target}
"""


def _backend_dsn_env(backend: str) -> str:
    return {
        "postgres": "CHUNKSHOP_TEST_DSN",
        "mariadb": "CHUNKSHOP_TEST_DSN_MARIADB",
        "sqlite": "SQLITE_SCALE_PATH",
        "clickhouse": "CHUNKSHOP_TEST_DSN_CH",
    }[backend]


def _query_pg(dsn: str, schema: str, qvec: list[float], k: int) -> tuple[list[tuple[str, int]], float]:
    import psycopg
    qvec_str = vector_text(qvec)
    t0 = time.perf_counter()
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            f'SELECT doc_id, seq_num FROM "{schema}"."chunks" '
            f'ORDER BY embedding <=> %s::vector LIMIT %s',
            (qvec_str, k),
        ).fetchall()
    return [(r[0], r[1]) for r in rows], (time.perf_counter() - t0) * 1000.0


def _query_mariadb(dsn: str, schema: str, qvec: list[float], k: int) -> tuple[list[tuple[str, int]], float]:
    """Hybrid query: euclidean in ORDER BY (index-accelerated), cosine in SELECT
    (reported distance matches the other 3 backends). Identical ranking to
    cosine for L2-normalized vectors. See sinks/mariadb.py for rationale.
    """
    import pymysql
    from chunkshop.backends.mariadb import _parse_mysql_dsn
    qvec_str = vector_text(qvec)
    kwargs = _parse_mysql_dsn(dsn)
    t0 = time.perf_counter()
    conn = pymysql.connect(**kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT doc_id, seq_num FROM `{schema}`.`chunks` "
                f"ORDER BY VEC_DISTANCE_EUCLIDEAN(embedding, VEC_FromText(%s)) LIMIT %s",
                (qvec_str, k),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [(r[0], r[1]) for r in rows], (time.perf_counter() - t0) * 1000.0


def _query_sqlite(path: str, qvec: list[float], k: int) -> tuple[list[tuple[str, int]], float]:
    import sqlite3, sqlite_vec
    qvec_str = vector_text(qvec)
    t0 = time.perf_counter()
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    rows = conn.execute(
        f"SELECT c.doc_id, c.seq_num FROM chunks c JOIN chunks_vec v ON c.id = v.id "
        f"WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
        (qvec_str, k),
    ).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows], (time.perf_counter() - t0) * 1000.0


def _query_clickhouse(dsn: str, db: str, qvec: list[float], k: int) -> tuple[list[tuple[str, int]], float]:
    from chunkshop.backends.clickhouse import ClickHouseBackend
    backend = ClickHouseBackend(_backend_dsn_env("clickhouse"))
    # CH parameter substitution mangles long float arrays — inline as a literal.
    # Floats are safe (rendered from a Vec<f32> via Python's repr); no user string.
    qvec_lit = "[" + ", ".join(f"{v:.7f}" for v in qvec) + "]"
    t0 = time.perf_counter()
    with backend.connect() as client:
        result = client.query(
            f"SELECT doc_id, seq_num FROM `{db}`.chunks "
            f"ORDER BY cosineDistance(embedding, {qvec_lit}) LIMIT {k}"
        )
        rows = [(r[0], r[1]) for r in result.result_rows]
    return rows, (time.perf_counter() - t0) * 1000.0


def _drop(backend: str) -> None:
    if backend == "postgres":
        import psycopg
        with psycopg.connect(os.environ["CHUNKSHOP_TEST_DSN"]) as conn:
            conn.execute('DROP SCHEMA IF EXISTS bench_scale_pg CASCADE'); conn.commit()
    elif backend == "mariadb":
        import pymysql
        from chunkshop.backends.mariadb import _parse_mysql_dsn
        c = pymysql.connect(**_parse_mysql_dsn(os.environ["CHUNKSHOP_TEST_DSN_MARIADB"]))
        try:
            with c.cursor() as cur:
                cur.execute("DROP DATABASE IF EXISTS `bench_scale_md`")
            c.commit()
        finally:
            c.close()
    elif backend == "sqlite":
        p = Path(os.environ["SQLITE_SCALE_PATH"])
        if p.exists():
            p.unlink()
    elif backend == "clickhouse":
        from chunkshop.backends.clickhouse import ClickHouseBackend
        with ClickHouseBackend("CHUNKSHOP_TEST_DSN_CH").connect() as client:
            client.command("DROP DATABASE IF EXISTS `bench_scale_ch` SYNC")


def run_backend(backend: str, gold: list[dict], embedder) -> dict:
    dsn_env = _backend_dsn_env(backend)
    if dsn_env not in os.environ:
        print(f"[bench-scale] SKIP {backend}: {dsn_env} not set")
        return {"skipped": True, "reason": f"{dsn_env} unset"}

    yaml_path = OUTPUT_DIR / f"cell-{backend}.yaml"
    yaml_path.write_text(_cell_yaml(backend))
    cfg = load_config(yaml_path)

    print(f"\n[bench-scale] === {backend} ingest ===")
    _drop(backend)  # fresh start
    t0 = time.perf_counter()
    cell_res = run_cell(cfg)
    ingest_wall = time.perf_counter() - t0
    print(f"  docs={cell_res.docs_processed} chunks={cell_res.chunks_written} "
          f"wall={ingest_wall:.2f}s embed={cell_res.embed_seconds:.2f}s")

    # Run queries
    print(f"[bench-scale] === {backend} query ===")
    latencies: list[float] = []
    rrs: list[float] = []
    for entry in gold:
        [qvec] = embedder.embed([entry["query"]])
        if backend == "postgres":
            top_rows, ms = _query_pg(os.environ["CHUNKSHOP_TEST_DSN"], "bench_scale_pg", qvec, 5)
        elif backend == "mariadb":
            top_rows, ms = _query_mariadb(os.environ["CHUNKSHOP_TEST_DSN_MARIADB"], "bench_scale_md", qvec, 5)
        elif backend == "sqlite":
            top_rows, ms = _query_sqlite(os.environ["SQLITE_SCALE_PATH"], qvec, 5)
        elif backend == "clickhouse":
            top_rows, ms = _query_clickhouse(os.environ["CHUNKSHOP_TEST_DSN_CH"], "bench_scale_ch", qvec, 5)
        latencies.append(ms)

        seen: set[str] = set()
        ordered: list[str] = []
        for did, _ in top_rows:
            if did not in seen:
                ordered.append(did)
                seen.add(did)
        rank = next((i + 1 for i, d in enumerate(ordered) if d == entry["gold_doc_id"]), None)
        rrs.append(1.0 / rank if rank else 0.0)

    mrr = sum(rrs) / len(rrs) if rrs else 0.0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)

    out = {
        "backend": backend,
        "ingest": {
            "docs": cell_res.docs_processed,
            "chunks": cell_res.chunks_written,
            "wall_seconds": round(ingest_wall, 2),
            "embed_seconds": round(cell_res.embed_seconds, 2),
            "non_embed_seconds": round(ingest_wall - cell_res.embed_seconds, 2),
        },
        "query_ms_mean": round(statistics.mean(latencies), 2),
        "query_ms_min": round(min(latencies), 2),
        "query_ms_p95": round(p95, 2),
        "query_ms_max": round(max(latencies), 2),
        "mrr": round(mrr, 4),
    }
    print(f"  MRR={out['mrr']}  query mean={out['query_ms_mean']}ms p95={out['query_ms_p95']}ms max={out['query_ms_max']}ms")
    return out


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Make sure SQLITE_SCALE_PATH is set (use a tempfile by default).
    os.environ.setdefault("SQLITE_SCALE_PATH", "/tmp/bench-scale.db")

    gold = yaml.safe_load(GOLD_FILE.read_text())
    print(f"[bench-scale] loaded {len(gold)} gold queries")

    embedder = load_embedder(FastembedEmbedder(
        type="fastembed",
        model_name="Xenova/bge-small-en-v1.5-int8",
        dim=384,
        batch_size=16,
        threads=2,
    ))

    out: dict[str, dict] = {}
    for backend in ("postgres", "mariadb", "sqlite", "clickhouse"):
        out[backend] = run_backend(backend, gold, embedder)

    print("\n[bench-scale] === summary ===")
    print(f"{'backend':<12} {'chunks':>7} {'ingest_s':>10} {'embed_s':>9} {'qry_mean_ms':>12} {'qry_p95_ms':>11} {'mrr':>6}")
    for backend, res in out.items():
        if res.get("skipped"):
            print(f"{backend:<12} SKIPPED ({res['reason']})")
            continue
        ing = res["ingest"]
        print(f"{backend:<12} {ing['chunks']:>7} {ing['wall_seconds']:>10} {ing['embed_seconds']:>9} "
              f"{res['query_ms_mean']:>12} {res['query_ms_p95']:>11} {res['mrr']:>6}")

    out_json = OUTPUT_DIR / "results.json"
    out_json.write_text(json.dumps(out, indent=2))
    print(f"\n[bench-scale] wrote {out_json}")

    # Cleanup
    for backend in ("postgres", "mariadb", "sqlite", "clickhouse"):
        if not out.get(backend, {}).get("skipped"):
            try:
                _drop(backend)
            except Exception as e:
                print(f"[bench-scale] cleanup warning for {backend}: {e}")
    print("[bench-scale] cleanup done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
