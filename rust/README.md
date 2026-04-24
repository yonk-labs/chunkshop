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

## Known drift: embedding values are NOT bit-exact vs Python

Python's default embedder is `Xenova/bge-base-en-v1.5-int8`: a Xenova-uploaded
int8-quantized ONNX model, registered in
`python/src/chunkshop/embedders/_registry.py`.

fastembed-rs does not ship an exact match for that variant. The closest
analog is `BGEBaseENV15Q`, which points at Qdrant's **fp32-optimized** ONNX
(`Qdrant/bge-base-en-v1.5-onnx-Q`). Bit-exact parity would require pointing
`ort` at the same Xenova ONNX file Python uses — that's a post-MVP hook.

In practice on the shipped 4-file sample corpus, the parity check
(`scripts/parity_check.py`) reports:

- Chunk `embedded_content`: **100% byte-for-byte identical** between Python and Rust.
- Top-5 retrieval: **identical order** for the fixed query.
- Chunk-level cosine distance between matched embeddings: **~0.01** (fp32 vs
  int8 drift; not bit-exact, but wire-format-compatible — the HNSW index and
  cosine ordering behave the same).

If you need bit-exactness, use the Python implementation until the Rust one
grows a user-defined-ONNX path.

Model-name mapping today (in `src/embedder.rs::resolve_model_name`):

| Python YAML `model_name`                     | fastembed-rs variant | Notes                          |
|----------------------------------------------|----------------------|--------------------------------|
| `Xenova/bge-base-en-v1.5-int8`               | `BGEBaseENV15Q`      | fp32-optimized, closest match  |
| `Xenova/bge-small-en-v1.5-int8`              | `BGESmallENV15Q`     | fp32-optimized, closest match  |
| `BAAI/bge-base-en-v1.5`                      | `BGEBaseENV15`       | direct                         |
| `BAAI/bge-small-en-v1.5`                     | `BGESmallENV15`      | direct                         |
| `BAAI/bge-large-en-v1.5`                     | `BGELargeENV15`      | direct                         |
| `sentence-transformers/all-MiniLM-L6-v2`     | `AllMiniLML6V2`      | direct                         |

Any other `model_name` errors at cell start.

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
| Bit-exact int8 parity                           | Wire up `fastembed::UserDefinedEmbeddingModel` with Xenova's `model_quantized.onnx` byte-for-byte from Python's fastembed cache. |
| `hierarchy` chunker                             | Port `python/src/chunkshop/chunkers/hierarchy.py` (~100 lines). |
| Extractors                                      | Each is an independent pure-Rust port modulo Python-only deps (spaCy can't cross). |
| Orchestrator                                    | Spawn N `chunkshop-rs ingest` subprocesses over N YAML configs. |

## License

MIT (workspace inherits from the repo root).
