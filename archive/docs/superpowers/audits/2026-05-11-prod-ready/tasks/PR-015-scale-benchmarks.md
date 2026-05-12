# PR-015 — Publish at-scale benchmarks

**Priority:** P3
**Effort:** L (~3 days)
**Dependencies:** none
**GAP-IDs:** GAP-006

## Problem

chunkshop documents the small-scale matrix test (16 cells, 1 doc each) and the bakeoff samples (~20 docs each). No published numbers at 1M+ docs across the 4 backends — the actual operating regime for production users.

## Solution

Run chunkshop against a representative 1M+ doc corpus on each backend; capture and publish:

- Ingest throughput (docs/second, chunks/second)
- Embed wall time per cell
- Query latency (p50, p95, p99) on a hot table
- Memory peak per cell
- HNSW build time (PG) / vector_similarity build time (CH)

Output: `docs/benchmarks-at-scale.md` with corpus description, hardware specs, methodology, and tables per backend.

## Acceptance Criteria

- [ ] Benchmarks doc exists with numbers for all 4 backends.
- [ ] Methodology section: hardware, corpus, chunker, embedder, repeats.
- [ ] Caveat section: "your mileage may vary" disclaimer.
- [ ] Reproduction script in `docs/samples/bakeoff-at-scale/`.

## Risk if Skipped

Users guess the operational envelope. Some over-provision; some under-provision and hit OOM.
