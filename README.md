# chunkshop

[![CI](https://github.com/yonk-labs/chunkshop/actions/workflows/ci.yml/badge.svg)](https://github.com/yonk-labs/chunkshop/actions/workflows/ci.yml)
[![PyPI status](https://img.shields.io/badge/status-alpha-orange)](python/pyproject.toml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)](python/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python v0.2.0](https://img.shields.io/badge/python--impl-v0.2.0-blueviolet)](python/)
[![Rust v0.1.0 MVP](https://img.shields.io/badge/rust--impl-v0.1.0_MVP-blueviolet)](rust/)
[![Go: planned](https://img.shields.io/badge/go--impl-planned-lightgrey)](go/)

A small, standalone, embeddable ingestion tool. Pulls text from a source, chunks it, embeds it,
optionally tags it, and lands the result in a pgvector table. Designed to be consumed as a
library or driven from the command line.

One YAML config = one end-to-end ingest ("cell"). Multiple YAMLs run in parallel via
`chunkshop orchestrate`. Same schema across Python, Rust, and Go — vectors are interchangeable.

## Pipeline

```mermaid
flowchart LR
    S[Source<br/>files · json_corpus<br/>pg_table · http · s3] --> F[Framer<br/>identity · heading_boundary<br/>regex_boundary · jsonpath]
    F --> C[Chunker<br/>sentence_aware · fixed_overlap<br/>hierarchy · neighbor_expand<br/>semantic · summary_embed<br/>hierarchical_summary]
    C --> E[Embedder<br/>fastembed<br/>ONNX · int8 or fp32]
    E --> X[Extractor<br/>none · rake_keywords · keybert_phrases<br/>spacy_entities · lang_detect · composite]
    X --> T[(pgvector table<br/>HNSW index)]
```

Each arrow is a boundary — swap any box without touching the others. See
[`docs/architecture.md`](docs/architecture.md) for the per-module breakdown.

## 60-second quickstart

```bash
# 1. Install (Python MVP)
cd chunkshop/python
uv sync --extra dev

# 2. Point an env var at your Postgres (pgvector extension required)
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# 3. Run against the shipped sample corpus (4 markdown files in docs/samples/).
#    First run downloads the ONNX model (~85 MB for int8 bge-base).
cd ..   # repo root
chunkshop ingest --config docs/samples/sample.yaml
```

New to chunkshop? The [**end-to-end tutorial**](docs/tutorial.md) takes you from zero
(no Postgres) to a running semantic query in about 15 minutes, including the `docker run`
line for pgvector and a copy-paste query script.

## Status

| Impl             | Path       | State                                                |
|------------------|------------|------------------------------------------------------|
| Python reference | `python/`  | v0.2.0, runs end-to-end. int8 default.               |
| Rust             | `rust/`    | v0.1.0 MVP. One source, chunker, embedder, sink. Parity-checked. |
| Go               | `go/`      | Planned. `onnxruntime_go` + HF tokenizer bindings.   |

## Defaults

The example config ships with `chunker.type: hierarchy` and `embedder.model_name:
Xenova/bge-base-en-v1.5-int8`.

**Chunker choice is benchmark-backed.** chunkshop's factorial on a 772-doc legal QA
corpus (30 gold questions, `gpt-4.1-mini` answer + judge) found:

- **Hierarchy chunker wins across every embedder column** — prepending the section
  heading to each embedded chunk adds free framing context.
- **int8 >= fp32 in aggregate** (160 vs 152 fully_correct across 12 cells) with 2×
  faster ingest.
- Zero hallucinations across 720 answers — prompt discipline, not model choice.

**Embedder default is MTEB-backed.** `bge-base` beats `bge-small` by ~3–5 points
on public retrieval benchmarks (MTEB). Our 772-doc factorial had `bge-small-int8`
tied with the best fp32 cell *on that specific corpus*; broader benchmarks favor
the larger model, so we default there. `bge-base-int8` is still int8-quantized,
~85 MB, and CPU-fast — the upgrade over `bge-small-int8` is essentially free.

Swap to `Xenova/bge-small-en-v1.5-int8` for a smaller footprint (~35 MB, 384 dim)
or `nomic-ai/nomic-embed-text-v1.5-Q` for long-context (8k tokens). Run the
factorial configs against your own corpus to confirm. Full pg-raggraph benchmark
data in the sibling repo. See [`docs/embedders.md`](docs/embedders.md) for the
catalogue and A/B recipe. See [`docs/embedders.md#benchmark-on-docssamples`](docs/embedders.md#benchmark-on-docssamples)
for measured numbers on this repo's corpus.

All three implementations share the same YAML config schema, the same ONNX Runtime-based
embedder (via `fastembed` in Python; via `ort` in Rust; via `onnxruntime_go` in Go), and the
same target table layout — so vectors produced by any of them are interchangeable.

## YAML shape

One YAML = one cell = one end-to-end ingest. Six sections (framer optional, defaults to identity):

| Section   | Types                                                                        |
|-----------|------------------------------------------------------------------------------|
| source    | files · json_corpus · pg_table · http (stub) · s3 (stub)                     |
| framer    | identity (default) · heading_boundary · regex_boundary · jsonpath            |
| chunker   | sentence_aware · fixed_overlap · hierarchy · neighbor_expand · semantic · summary_embed · hierarchical_summary |
| embedder  | fastembed (Python); onnx_direct (Rust, Go, optional in Python)               |
| extractor | none · rake_keywords · keybert_phrases · spacy_entities · lang_detect · composite (opt-in extras) |
| target    | pgvector table `{schema}.{table}`; `mode: overwrite \| append \| create_if_missing`; `source_tag` + `promote_metadata` for multi-source tables; HNSW index |

Full field-by-field reference in [`python/README.md`](python/README.md).

## Target table schema

```sql
CREATE TABLE {schema}.{table} (
    id                  text PRIMARY KEY,        -- "{doc_id}::{seq_num}"
    doc_id              text NOT NULL,
    seq_num             int  NOT NULL,
    original_content    text NOT NULL,           -- raw chunk, for grep/fact-match/audit
    embedded_content    text NOT NULL,           -- what was embedded (may include heading prefix, neighbors)
    tags                text[] NOT NULL DEFAULT '{}',
    metadata            jsonb NOT NULL DEFAULT '{}',
    embedding           vector({dim}) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);
-- plus: UNIQUE on (doc_id, seq_num) via btree; HNSW index on embedding if hnsw=true.
```

## Documentation

| Doc                                            | For                                                              |
|------------------------------------------------|------------------------------------------------------------------|
| [`docs/executive-summary.md`](docs/executive-summary.md) | Two-page overview: what, why, current state, measured performance, who should use it. |
| [`docs/tutorial.md`](docs/tutorial.md)         | **Start here.** Zero-to-retrieval end-to-end walkthrough.        |
| [`docs/tutorial-multi-source.md`](docs/tutorial-multi-source.md) | Multi-source ingest: two cells, one table, filter by source. |
| [`docs/tutorial-framers.md`](docs/tutorial-framers.md) | DocFramer walkthrough: markdown heading splits + nested-JSON expansion. |
| [`docs/tutorial-metadata.md`](docs/tutorial-metadata.md) | Metadata extraction: composite extractor + promoted columns + filtered queries. |
| [`docs/tutorial-bakeoff.md`](docs/tutorial-bakeoff.md) | Bakeoff walkthrough: pick the best chunker+embedder for your corpus. |
| [`docs/tutorial-semantic.md`](docs/tutorial-semantic.md) | Semantic chunker walkthrough: split on topic shifts when your corpus has no headings. |
| [`docs/tutorial-summaries.md`](docs/tutorial-summaries.md) | Summary-embed + hierarchical walkthrough: lede/sumy integration, fine+coarse retrieval. |
| [`docs/quickstart-multi-source.md`](docs/quickstart-multi-source.md) | Recipe card: schema-flex modes + append pre-flight. |
| [`docs/quickstart-framers.md`](docs/quickstart-framers.md) | Recipe card: which framer for which source shape. |
| [`docs/quickstart-extractors.md`](docs/quickstart-extractors.md) | Recipe card: copy-paste YAML per extractor. |
| [`docs/quickstart-semantic.md`](docs/quickstart-semantic.md) | Recipe card: semantic chunker knobs (percentile tuning, memory-tight, neighbor-expand). |
| [`docs/quickstart-summaries.md`](docs/quickstart-summaries.md) | Recipe card: summary_embed + hierarchical_summary across all three summarizer modes. |
| [`docs/quickstart-bakeoff.md`](docs/quickstart-bakeoff.md) | Recipe card: common bakeoff shapes (embedder-only, chunker-only, full factorial). |
| [`python/README.md`](python/README.md)         | Reference: install, CLI flags, YAML field-by-field, troubleshooting. |
| [`docs/architecture.md`](docs/architecture.md) | How the pieces fit: components, data flow, extension points.     |
| [`docs/chunkers.md`](docs/chunkers.md)         | Each chunker: what it does, when to pick it, knobs incl. `max_chars`. |
| [`docs/summaries.md`](docs/summaries.md)       | Summary-embed + hierarchical chunker reference, summarizer modes, grouping strategies. |
| [`docs/embedders.md`](docs/embedders.md)       | Model catalogue, int8 registry, A/B testing embedders, measured bench. |
| [`docs/extractors.md`](docs/extractors.md)     | Each extractor: why use it, config, promoted-column pairing.     |
| [`docs/query-clients.md`](docs/query-clients.md) | Query the ingested table from Python, JS/TS, Rust, Go.          |
| [`docs/samples/`](docs/samples/)               | Sample markdown + runnable configs + framer demo fixtures.       |

## Monorepo layout

```
chunkshop/
├── python/                 reference implementation; runs today
│   ├── src/chunkshop/
│   └── tests/
├── rust/                   planned
├── go/                     planned
└── docs/
    ├── tutorial.md
    ├── architecture.md
    ├── chunkers.md
    ├── embedders.md
    └── samples/
```

## License

MIT. See `LICENSE`.
