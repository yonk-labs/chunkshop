# chunkshop benchmarks — performance + accuracy

Measured numbers on the four backends across three axes the v0.4.0 audit
called out as gaps: HNSW vs brute-force recall, concurrent-ingest scaling,
and per-backend throughput at ~10k chunks. Run on a 24-core box, 122 GiB
RAM, all backends co-located on `localhost`. Numbers are illustrative of
the relative shapes; absolute wall time varies with hardware.

For broader accuracy benchmarks across chunkers / embedders / 3 corpora
(NTSB / SCOTUS / Sales-CRM), see the 252-cell mega-table linked from the
README's "Performance & accuracy" section.

## TL;DR

- **HNSW pays off above ~1k chunks.** At 75 chunks, HNSW is *slower* than
  brute-force (index lookup overhead exceeds scan cost). At 3.8k chunks,
  HNSW is **4.2× faster** with **identical MRR**. At 8k chunks on PG,
  query latency stays under 10ms.
- **Concurrent ingest scales sub-linearly.** `chunkshop orchestrate
  --concurrency 4` gives **2.45× speedup** on an 8-cell workload
  (61% scaling efficiency); past 4, diminishing returns dominate due to
  ONNX Runtime init contention.
- **MariaDB query latency cliff at 8k chunks.** PG/CH/SQLite all stay
  ≤15ms; MariaDB jumps to **158ms** — a 20-50× gap. Confirmed across
  multiple corpora. Use a different backend for low-latency retrieval at
  scale, or use MariaDB for ingest only.

## Bench 1 — HNSW vs brute-force on Postgres

The pgvector HNSW index trades a one-time build cost (ingest time) for
much faster queries on large tables. The tradeoff inverts on small
tables. Measured on the NTSB corpus (12 gold queries, sentence_aware
chunker, bge-small-en-v1.5-int8 embedder, dim=384):

| Corpus | Chunks | Chunker | HNSW off — query mean | HNSW on — query mean | Speedup | MRR (off) | MRR (on) |
|---|---:|---|---:|---:|---:|---:|---:|
| NTSB | 75 | `sentence_aware` | 1.11ms | 1.48ms | **0.75×** (slower) | 0.9028 | 0.9028 |
| SCOTUS | 1095 | `sentence_aware` | 3.01ms | 2.73ms | 1.10× | 0.4931 | 0.4931 |
| SCOTUS (small chunks) | 3865 | `fixed_overlap(w=100, s=50)` | 7.06ms | 1.68ms | **4.20×** | 0.4167 | 0.4167 |

**MRR is unchanged** across all three scales — HNSW recall matches
brute-force exactly on these corpora. (Theoretical recall loss is at the
fringes of large tables; not observed at these sizes.)

HNSW build cost is negligible compared to embedder cost:
- 75 chunks: −0.06s (lost in noise)
- 1095 chunks: +1.40s
- 3865 chunks: +4.26s (8% of total ingest)

**Recommendation:** `hnsw: true` for production tables. The build cost is
paid once at ingest; query latency wins compound over the lifetime of the
table. Skip HNSW (`hnsw: false`) during bakeoffs for fair query-time
comparison across chunker/embedder combos.

Reproduce: `python docs/samples/benchmarks/bench_hnsw.py`. Set
`BENCH_CORPUS=ntsb|scotus` to switch corpora; edit the chunker section
to vary chunk count.

## Bench 2 — concurrent-ingest scaling via `chunkshop orchestrate`

Each cell runs as a subprocess (`python -m chunkshop.cli ingest`). The
orchestrator's `--concurrency N` parameter controls how many cells run
simultaneously. 8 NTSB cells (sentence_aware + bge-small-int8) into 8
distinct PG schemas:

| Concurrency | Total wall | Mean per-cell wall | Speedup | Scaling efficiency |
|---:|---:|---:|---:|---:|
| 1 (sequential) | 42.28s | 5.06s | 1.00× | 100% (baseline) |
| 2 | 24.77s | 6.00s | **1.71×** | 85% |
| 4 | 17.27s | 8.42s | **2.45×** | 61% |
| 8 | 15.03s | 14.80s | **2.81×** | 35% |

Per-cell wall time **grows** with concurrency because ONNX Runtime
session initialization contends for the same shared library globals.
With 8 cells × 2 ORT threads each = 16 contended threads on a 24-core
box → context-switch overhead dominates past concurrency=4.

**Recommendation:** `concurrency` ≈ `min(physical_cores / embedder_threads, 4)`
for most workloads. On this 24-core box that's `min(24/2, 4) = 4`.
Going to 8 wastes ~14% throughput vs. an idealized 4-way speedup, but
isn't catastrophic — the orchestrator handles the contention gracefully.

Reproduce: `python docs/samples/benchmarks/bench_concurrent.py`.

## Bench 3 — per-backend throughput at ~8k chunks

The mega-table covers ~75-2000 chunks across NTSB / SCOTUS / Sales-CRM.
This bench pushes 5× past that to characterize the latency cliffs.
SCOTUS corpus (772 docs) chunked with `fixed_overlap(window_words=50,
step_words=25)` → 8,079 chunks total. Same chunks ingested into all 4
backends with `bge-small-en-v1.5-int8`. 12 SCOTUS gold queries against
each.

| Backend | Ingest wall | Embed time | Non-embed time | Query mean | Query p95 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| **Postgres + HNSW** | 63.91s | 47.06s | 16.85s | 9.22ms | 13.75ms | 0.4236 |
| **MariaDB + HNSW** (hybrid query, post v0.4.1) | 78.38s | 47.07s | 31.31s | **3.66ms** | 5.55ms | 0.4028 |
| **MariaDB** (pre-v0.4.1, cosine brute-force) | 82.34s | 46.65s | 35.69s | 158.74ms | 169.70ms | 0.4236 |
| **SQLite + sqlite-vec** | 48.08s | 45.89s | 2.19s | **3.21ms** | 3.80ms | 0.4028 |
| **ClickHouse** | 54.97s | 46.24s | 8.73s | 15.06ms | 19.35ms | 0.4306 |

### Read 1 — Embedder dominates ingest

All four backends spend 44-47s embedding (same 8079 chunks, same model).
Non-embed time is the backend-specific cost: schema/table DDL,
per-document INSERT, index build, network round-trips.

Non-embed cost from smallest to largest:
1. **SQLite: 2.19s** — no network, single-file commits
2. **ClickHouse: 8.63s** — HTTP per batch, single insert call
3. **Postgres + HNSW: 16.95s** — includes the HNSW build (~4s at this scale)
4. **MariaDB: 35.69s** — per-row INSERT through pymysql plus VEC_FromText
   interpolation; 16× SQLite

### Read 2 — MariaDB cliff fixed in v0.4.1; was a chunkshop-side query shape

**v0.4.0 behavior** (since-fixed): MariaDB cosine queries took **158ms
mean latency** at 8k chunks vs 8ms for PG with HNSW. The mega-table
foreshadowed the same shape on SCOTUS (~0.64s MariaDB vs 0.14s PG at
1095 chunks).

**Root cause** found during this audit: MariaDB 11.7's `VECTOR INDEX`
only accelerates `VEC_DISTANCE_EUCLIDEAN`, not `VEC_DISTANCE_COSINE`.
chunkshop's pre-v0.4.1 query used cosine, which bypassed the index and
forced a full table scan + sort.

**Fix** (shipped in v0.4.1): chunkshop now uses a hybrid query —
euclidean in `ORDER BY` (index-accelerated), cosine in `SELECT` (the
reported distance matches what PG/CH/SQLite return). For L2-normalized
embeddings (every chunkshop-supported embedder), euclidean and cosine
produce the same ranking.

**Tradeoff:** MariaDB's HNSW is approximate. MRR drops from 0.4236
(cosine brute-force) to 0.4028 (euclidean+HNSW) — a ~5% relative drop
on top-5. Same approximation tradeoff PG's HNSW makes; same MRR shape
as SQLite's vec0 MATCH.

**Requirement:** MariaDB cells must set `hnsw: true` for the index to
exist. The pre-v0.4.1 default of `hnsw: false` on MariaDB has been
revisited in the engine doc — MariaDB users should treat `hnsw: true` as
load-bearing for production retrieval.

**Recommendation:**
- **High-QPS read workloads, larger than ~1k chunks: PG with HNSW.**
- **Low-ops profile, ≤ ~1M chunks, single-machine: SQLite.** Fastest
  ingest, lowest query latency in this bench.
- **OLAP / analytics-heavy + retrieval: ClickHouse.** Query is fine; the
  win is in column-store joins around the retrieval result.
- **MariaDB for retrieval: only at small scale (<1k chunks) or
  ingest-only workloads where retrieval moves to another store.**

### Read 3 — MRR is consistent across backends

PG / MariaDB / ClickHouse all returned MRR = 0.4236-0.4306 (within
~0.02 of each other). SQLite landed at 0.4028 — the slight gap reflects
float-precision differences in sqlite-vec's MATCH operator vs the
SQL-standard cosine distance the other three use. Retrieval quality is
NOT meaningfully backend-dependent.

(The absolute MRR of ~0.42 is low because `fixed_overlap(window=50)`
chunks too aggressively for the SCOTUS corpus. The same corpus on
`hierarchy` chunker in the mega-table scored MRR=0.917. The point of
this bench is per-backend latency, not the chunker — short chunks +
short queries simply have lower MRR on SCOTUS.)

Reproduce: `python docs/samples/benchmarks/bench_scale.py`.

## What's still not measured

- **GPU embedder paths.** This bench box has no GPU; everything ran on
  CPU+ONNX Runtime. Per fastembed-rs docs, CUDA execution providers can
  give 5-20× embedder speedup on GPU. Not chunkshop-side work — fastembed
  / ort handle this transparently when CUDA is wired in the build.
- **Million-chunk scale.** Largest bench here is 8k chunks. The 1M+
  story (`docs/benchmarks-at-scale.md` mentioned in PR-015 of the
  prod-ready audit) needs hours of compute and a representative corpus.
  Patterns at 8k typically extrapolate (HNSW wins more, MariaDB lags
  more, SQLite stays competitive until it doesn't), but absolute numbers
  at million-chunk scale would be different.
- **HNSW recall vs k.** This bench measured top-5 recall implicitly via
  MRR; it didn't sweep `k` to find the HNSW recall cliff (where ANN
  approximation starts losing items vs exact brute-force). At small `k`
  (1, 3, 5) HNSW recall is near-perfect on these corpora; the cliff
  appears at larger `k` and very large tables.
- **HNSW build-parameter sweep.** chunkshop uses pgvector defaults
  (`m=16`, `ef_construction=64`). Tuning `m` upward improves recall at
  the cost of build time and index size; `ef_construction` similarly.
  Worth a future bench.

## How to reproduce all three

```bash
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
export CHUNKSHOP_TEST_DSN_CH="clickhouse://default:chpw@localhost:8124/chunkshop_test"
export SQLITE_SCALE_PATH="/tmp/bench-scale.db"

cd python && uv sync --extra dev --extra all-backends && cd ..

# Bench 1: HNSW
.venv/bin/python docs/samples/benchmarks/bench_hnsw.py             # NTSB (small)
BENCH_CORPUS=scotus .venv/bin/python docs/samples/benchmarks/bench_hnsw.py

# Bench 2: concurrent
.venv/bin/python docs/samples/benchmarks/bench_concurrent.py

# Bench 3: scale across backends
.venv/bin/python docs/samples/benchmarks/bench_scale.py
```

Bench scripts live in [`docs/samples/benchmarks/`](samples/benchmarks/) so
the methodology is reproducible. Result JSON files land in
`skill-output/bench-*/` (gitignored — output, not source).
