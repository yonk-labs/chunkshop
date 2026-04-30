# BYO embedder at 1024 dim

Companion sample to [`docs/samples/embedder-byo/`](../embedder-byo/), using
the same YAML-only BYO mechanism but a larger model: BGE-large int8 (1024
dim, ~340 MB).

This sample answers: *does the BYO path scale to bigger models?* (Yes.)

## Run it

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop                 # repo root
cd python && uv sync --extra dev && cd ..
(cd rust && cargo build --release)

# Python:
psql "$CHUNKSHOP_TEST_DSN" -c "DROP SCHEMA IF EXISTS chunkshop_byo_large CASCADE"
chunkshop ingest --config docs/samples/embedder-byo-large/byo-large.yaml

# Rust:
psql "$CHUNKSHOP_TEST_DSN" -c "DROP SCHEMA IF EXISTS chunkshop_byo_large CASCADE"
./rust/target/release/chunkshop-rs ingest --config docs/samples/embedder-byo-large/byo-large.yaml

# Verify dimensions:
psql "$CHUNKSHOP_TEST_DSN" -c "
  SELECT COUNT(*), vector_dims(embedding)
  FROM chunkshop_byo_large.chunks
  GROUP BY vector_dims(embedding)
"
```

Expected: 5 chunks @ vector_dims=1024.

## Tradeoffs at this size

| | bge-base int8 (default) | bge-large int8 (this sample) |
|---|---|---|
| dim | 768 | 1024 |
| ONNX size | ~110 MB | ~340 MB |
| Ingest wall (5 chunks, laptop) | ~0.5s | ~6s (Python) / ~4s (Rust) |
| MTEB delta | reference | ~+1pp on most benchmarks |

The cost/benefit only makes sense if you can show the +1pp matters on
*your* gold queries. Use `chunkshop bakeoff` to put both models in a
matrix and let your data choose.

See [`docs/embedder-catalogue.md`](../../embedder-catalogue.md) for the
full list of tested models, dim/precision/pooling for each, and known-
broken cases.
