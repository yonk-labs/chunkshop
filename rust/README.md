# chunkshop-rs

Minimal Rust port of chunkshop. Same YAML config, same pgvector target table,
same ordering of chunks — vectors written by `chunkshop-rs` are compatible with
vectors written by the Python reference (see `../scripts/parity_check.py`).

**Status:** v0.1.0 MVP. One source, one chunker, one embedder, one sink. This
is a wire-format proof of the cross-language claim; it is not feature parity
with Python.

## Build

```bash
cd rust
cargo build --release
```

Release build takes ~25s wall on a modern laptop (plus ~5 min of crate
downloads on a cold `~/.cargo`). The ONNX Runtime binary is downloaded by
`ort-sys` during `cargo build` — if that fails with HTTP 504, retry; the CDN
(`cdn.pyke.io`) occasionally hiccups.

## Run

```bash
# Point at a pgvector-enabled Postgres
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# Run the shipped sample config (from repo root)
./target/release/chunkshop-rs ingest \
    --config ../docs/samples/sample.yaml
```

The first run downloads the embedder model to fastembed's cache (~500 MB for
`bge-base-en-v1.5`). Subsequent runs are local.

## What works

| Stage     | Supported                                                      |
|-----------|----------------------------------------------------------------|
| source    | `files` (glob + `id_from: path \| stem \| sha1`)               |
| chunker   | `sentence_aware` (doc_type: prose or code; max_chars/min_chars) |
| embedder  | `fastembed` (maps model_name to fastembed-rs variant; see below) |
| target    | pgvector table; modes `overwrite` + `create_if_missing`; HNSW index optional |

## What does NOT work (MVP cutoff)

Everything else Python ships is **deliberately out of scope**:

- Other chunkers: `hierarchy`, `fixed_overlap`, `neighbor_expand`, `semantic`,
  `summary_embed`, `hierarchical_summary` — not ported.
- Framers (`heading_boundary`, `regex_boundary`, `jsonpath`) — parsed but ignored.
- Extractors (`rake_keywords`, `keybert_phrases`, `spacy_entities`, `lang_detect`,
  `composite`) — parsed but ignored; no tags or extractor-produced metadata.
- Sources: `json_corpus`, `pg_table`, `http`, `s3` — not ported.
- Target `mode: append` — returns a runtime error pointing at the Python impl.
- Promoted columns (`promote_metadata`) — parsed but not written.
- Orchestrator / bakeoff subcommands — not ported.

YAML configs from the Python side are **accepted** (unknown fields on
`runtime`/`framer`/`extractor` are ignored) — but obviously the ignored stages
won't run.

## Embedding parity vs Python

For the two registered Xenova int8 BGE variants, `chunkshop-rs` loads
**the same ONNX file Python loads** (`Xenova/bge-base-en-v1.5/onnx/model_quantized.onnx`
and the small-model equivalent) via [`hf-hub`](https://crates.io/crates/hf-hub),
tokenizes through the `tokenizers` crate with the same padding/truncation
config fastembed-py uses, runs ORT with `intra_threads=1`, CLS-pools, and
L2-normalizes (with f64 sum-of-squares to mirror numpy). On the shipped
4-file / 14-chunk sample corpus the cross-language parity check reports:

- **Top-k retrieval order: identical** (Python and Rust pick the same
  chunks in the same order for a fixed query) — the user-visible RAG claim.
- Chunk `embedded_content`: **100% byte-for-byte identical**.
- Cosine distance between matched embeddings: **mean ~1-2e-3, max ~5-15e-3**
  per chunk (was ~1e-2 mean before this work — ~5x improvement).

Strict bitwise equality is **not** achievable: Python's `onnxruntime` wheel
and Rust's [`ort`](https://crates.io/crates/ort) crate are independent ORT
C++ binary builds. They diverge by ULPs (and occasionally more on quantized
matmul paths) regardless of thread count. If your workflow needs bitwise
reproducibility (e.g. cross-implementation vector hashing), use one
implementation throughout.

For all other model names the embedder falls back to fastembed-rs's stock
variants. Those *do not* claim parity with Python — they use Qdrant's
fp32-optimized ONNX, a different file from Python's BAAI fp32 ONNX, and
typically drift ~1e-3 per element.

Model-name mapping today (in `src/embedder.rs`):

| Python YAML `model_name`                     | Rust path                        | Parity vs Python |
|----------------------------------------------|----------------------------------|------------------|
| `Xenova/bge-base-en-v1.5-int8`               | hand-rolled ORT + Xenova ONNX    | retrieval-identical, cos drift ≤ 1.5e-2 |
| `Xenova/bge-small-en-v1.5-int8`              | hand-rolled ORT + Xenova ONNX    | retrieval-identical, cos drift ≤ 1.5e-2 |
| `BAAI/bge-base-en-v1.5`                      | fastembed-rs `BGEBaseENV15`      | wire-format only |
| `BAAI/bge-small-en-v1.5`                     | fastembed-rs `BGESmallENV15`     | wire-format only |
| `BAAI/bge-large-en-v1.5`                     | fastembed-rs `BGELargeENV15`     | wire-format only |
| `sentence-transformers/all-MiniLM-L6-v2`     | fastembed-rs `AllMiniLML6V2`     | wire-format only |

Any other `model_name` errors at cell start.

### Parity verification

- `rust/chunkshop/tests/embedding_parity.rs` — embeds 5 fixed inputs and
  asserts (a) median per-vector cosine distance ≤ 1e-7, (b) max abs
  element-wise diff ≤ 1e-2, (c) max per-vector cosine distance ≤ 5e-3
  against committed Python reference vectors. Skips cleanly without
  network. Re-generate the reference with
  `uv run --project python python scripts/produce_rust_parity_reference.py`.
- `scripts/parity_check.py` — end-to-end ingest comparison. Boots Python
  and Rust against the same corpus into two tables, compares top-k
  retrieval and per-chunk cosine. Manual; needs both toolchains plus
  Postgres.

## Cross-language parity check

`scripts/parity_check.py` (at the repo root) is a manual check — not a pytest
— because it needs both toolchains installed. It runs the Python ingest and
the Rust ingest into two tables, then compares top-k retrieval for a fixed
query:

```bash
# With both `uv` (Python) and cargo (Rust) available:
cd rust && cargo build --release && cd ..
export CHUNKSHOP_DSN="postgresql://..."
cd python && uv run python ../scripts/parity_check.py --corpus "docs/samples/*-*.md"
```

Writes `skill-output/rust-parity/report.md`.

## Integration test

```bash
cd rust
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
cargo test --test parity
```

Skips if `CHUNKSHOP_TEST_DSN` is unset. The test creates schema
`chunkshop_rust_parity`, ingests `tests/parity-fixtures/handbook-intro.md`,
and asserts row count > 0, non-empty `embedded_content`, and
`vector(768)` column dim. Leaves the schema behind for inspection; rerun
re-creates it under `mode: overwrite`.

## Implementation roadmap (not shipped)

| Want                                            | Lift |
|-------------------------------------------------|------|
| `hierarchy` chunker                             | Port `python/src/chunkshop/chunkers/hierarchy.py` (~100 lines). |
| Extractors                                      | Each is an independent pure-Rust port modulo Python-only deps (spaCy can't cross). |
| Orchestrator                                    | Spawn N `chunkshop-rs ingest` subprocesses over N YAML configs. |

## License

MIT (workspace inherits from the repo root).
