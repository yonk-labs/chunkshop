# chunkshop-rs

Rust port of chunkshop. Same YAML config, same chunk table shape, same
ordering of chunks — vectors written by `chunkshop-rs ingest` are compatible
with vectors written by the Python reference (`../scripts/parity_check.py`),
and the same bakeoff YAML produces equivalent leaderboards in both languages
(`../scripts/parity_check_bakeoff.py`).

> ## What Rust covers today
>
> The chunkshop user journey is **bring corpus → run bakeoff → take
> recommended.yaml → run ingest → repeat for new corpus**. Rust now covers
> all of it except orchestrator-level parallel fan-out.
>
> | What | Python | Rust |
> |---|---|---|
> | `ingest` (one YAML → one cell) | ✅ | ✅ |
> | `Pipeline` (library / inline mode) | ✅ | ✅ |
> | `bakeoff` (matrix → leaderboard → recommended.yaml) | ✅ | ✅ |
> | `orchestrate` (N cells in parallel subprocesses) | ✅ | ❌ |
>
> Cross-language bakeoff parity is **verified** per-run by
> `scripts/parity_check_bakeoff.py`: same YAML, leaderboards within
> ±2.5pp MRR with consistent ordering on distinct-MRR pairs. The
> orchestrator port is the next major piece.

**Status:** v0.5.0 beta. Single-cell pipeline + bakeoff at parity with Python
for the chunk-table path (modulo two NLP-heavy extractors that need spaCy).
The same canonical
YAML — `docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml` — runs from both
languages and produces equivalent leaderboards. Wire-format proof of the
cross-language claim at both the cell layer (vectors interchangeable) AND
the bakeoff layer (leaderboards within ±2.5pp MRR, consistent ordering on
distinct-MRR pairs).

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
| source    | `files`, `json_corpus`, `pg_table`, `http`, `s3` (bucket + prefix + optional `endpoint_url` for minio / R2; via the `object_store` crate; standard AWS credential chain; `metadata` carries `{bucket, key, size, etag}`) — **5/5 sources ship in Rust** |
| framer    | `identity` (default 1-to-1 pass-through), `heading_boundary` (split markdown on a configurable heading regex with preamble + title-from-heading), `regex_boundary` (split on arbitrary regex; optional title-pattern capture), `jsonpath` (parse content as JSON, walk dotted path with `*` for list iteration; configurable body/title sub-paths) — all byte-identical to Python |
| chunker   | `sentence_aware`, `hierarchy`, `fixed_overlap`, `neighbor_expand`, `summary_embed`, `hierarchical_summary` (all byte-identical to Python), `semantic` (algorithm-parity; chunks NOT byte-identical due to MB-1's ~1e-3 ORT drift) — **all 6 Python chunkers ship in Rust**. Summarizer dispatch supports `passthrough` + `external` natively; `callable` mode recognizes `chunkshop.summarizers.passthrough` always, and `chunkshop.summarizers.lede` behind the `lede` cargo feature (`cargo build --features lede` pulls `lede` from crates.io). |
| embedder  | `fastembed` (maps model_name to fastembed-rs variant; see below) |
| extractor | `none` (default), `composite`, `rake_keywords` (hand-rolled RAKE + 150-word EN stopword list — algorithm-only parity), `lang_detect` (via `whatlang` crate, ISO 639-3 → 639-1 conversion — algorithm-only parity), `keybert_phrases` + `spacy_entities` (Python-only stubs that error at config-load) |
| target    | Postgres, MariaDB, SQLite, and ClickHouse chunk tables; modes `overwrite` / `append` / `create_if_missing`; `force_overwrite`; `source_tag` write-once where the backend supports upsert; `promote_metadata`; HNSW/index knobs where backend-native |

## What does NOT work yet

The two extractor stubs are deliberate Python-only. Orchestrator, the
embedder-registry breadth, and the Python/Postgres companion document table are
real parity gaps.

### Python/Postgres document table

`target.documents.enabled: true` is currently Python/Postgres-only. Rust
rejects enabled document stores at config load so a shared YAML cannot appear
to ingest successfully while silently omitting the companion document rows.

### Meta-runner gap

- **`chunkshop-rs bakeoff` subcommand — SHIPPED.** Run the same
  `bakeoff-ntsb-rust.yaml` from either language and verify with
  `scripts/parity_check_bakeoff.py` (leaderboards within ±2.5pp MRR;
  consistent ordering on distinct-MRR pairs).
- **`chunkshop-rs orchestrate` subcommand — not ported.** Bulk multi-cell
  fan-out (Python's CLI spawns N `ingest` subprocesses with thread caps,
  log multiplexing, per-cell failure isolation). Different surface than the
  bakeoff; tracked separately.

### Embedder registry breadth

- The Rust dispatch supports `Xenova/bge-{small,base}-en-v1.5-int8`
  (bit-near-exact via the same ONNX file Python loads), the stock
  fastembed-rs variants (`BAAI/bge-{small,base,large}-en-v1.5`,
  `sentence-transformers/all-MiniLM-L6-v2[-int8]`), and the nomic v1.5
  family (`nomic-ai/nomic-embed-text-v1.5[-Q]`). Adding a model that
  fastembed-rs already knows = one entry in
  `src/embedder.rs::resolve_model_name`. Adding a model fastembed-rs
  doesn't know yet = one entry in `user_defined_source` (for CLS-pooled
  variants) plus a mean-pooling branch if needed. The brief queue tracks
  a YAML-driven HF-pointer feature that turns "add a model" into a YAML
  edit instead of a Rust-source edit. See [`../docs/embedders.md`](../docs/embedders.md).

### Deliberate Python-only extractors

- Extractors `keybert_phrases` and `spacy_entities` — Python-only. Build-time
  error directs users back to Python or to a custom Rust binary that
  registers their own NER / embedding-keyphrase pipeline. The other four
  extractor variants (`none`, `composite`, `rake_keywords`, `lang_detect`)
  ship.

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
| **`chunkshop-rs bakeoff`** subcommand           | **In flight.** Mirror `python/src/chunkshop/bakeoff/` (config + keys + gold + runner + score + output) so the same `bakeoff-ntsb.yaml` runs from either language and produces the same leaderboard ordering. The single biggest gap to the cross-language pitch — without it, the comparison story is Python-only. |
| `chunkshop-rs orchestrate` subcommand           | Spawn N `chunkshop-rs ingest` subprocesses over N YAML configs with thread caps + per-cell failure isolation. Different surface from bakeoff; tracked separately. |
| Python-only extractors (`keybert_phrases`, `spacy_entities`) | Each needs a Rust-native NER / embedding-keyphrase implementation modulo Python-only deps (spaCy can't cross). |

## License

MIT (workspace inherits from the repo root).
