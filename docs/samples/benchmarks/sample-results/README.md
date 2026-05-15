# Sample benchmark results

Frozen result JSON from one run of each bench script in
[`../`](..). Captured on a 24-core / 122 GiB box, all backends on
`localhost`, chunkshop v0.4.0 + post-audit fixes (HEAD `0ec8257` at
capture time). Reference snapshot so readers of
[`docs/benchmarks.md`](../../../benchmarks.md) can inspect the raw
data without rerunning.

| File | Bench | What it captures |
|---|---|---|
| `bench-hnsw-ntsb.json` | `bench_hnsw.py` on NTSB (75 chunks) | HNSW *slower* than brute-force at this scale |
| `bench-hnsw-scotus.json` | `bench_hnsw.py` on SCOTUS (3865 chunks) | HNSW 4.2× faster, same MRR |
| `bench-concurrent.json` | `bench_concurrent.py` | Orchestrator wall + per-cell wall at c=1/2/4/8 |
| `bench-scale.json` | `bench_scale.py` | 4-backend ingest+query at 8079 chunks |

These files are checked in for reference only. **Do not edit them by
hand.** To refresh, re-run the bench scripts; live results land under
`skill-output/bench-*/` (gitignored). Copy from there to here if you
want the committed snapshot updated.
