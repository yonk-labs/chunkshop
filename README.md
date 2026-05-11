# chunkshop

[![CI](https://github.com/yonk-labs/chunkshop/actions/workflows/ci.yml/badge.svg)](https://github.com/yonk-labs/chunkshop/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/chunkshop?label=pypi&color=blueviolet)](https://pypi.org/project/chunkshop/)
[![crates.io](https://img.shields.io/crates/v/chunkshop-rs?label=crates.io&color=blueviolet)](https://crates.io/crates/chunkshop-rs)
[![status: beta](https://img.shields.io/badge/status-beta-yellow)](python/pyproject.toml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)](python/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A small, standalone, embeddable ingestion tool. Pulls text from a source,
chunks it, embeds it, optionally tags it, and lands the result in a vector
table on one of **four supported backends — Postgres + pgvector, MariaDB
11.7+, SQLite + sqlite-vec, or ClickHouse 24.10+.** Designed to be consumed
as a library or driven from the command line.

**One YAML config = one end-to-end ingest ("cell").** Same YAML, two
languages: Python is the reference; Rust ships to crates.io. Vectors written
by either are interchangeable.

## v0.4.0 — modular backends + cross-language matrix

- **4 sink backends** (postgres, mariadb, sqlite, clickhouse) — all 4 in
  Python AND Rust.
- **9 sources** (`files`, `json_corpus`, `http`, `s3`, `inline`,
  `pg_table`, `mariadb_table`, `sqlite_table`, `clickhouse_table`).
- **The 16-cell cross-backend matrix** — every DB-source × DB-sink combo
  round-trips in both languages. Pinned in CI (`cargo test --test
  cross_backend_matrix` / `pytest tests/chunkshop/test_cross_backend_matrix.py`).
- **Cross-backend bakeoff** — one YAML, one `chunkshop bakeoff` command,
  leaderboards across all 4 backends side-by-side (Python; Rust bakeoff is
  PG-only for now).
- **Identical retrieval quality across backends.** The v0.4.0 validation
  bakeoff (NTSB corpus, 20 docs, 12 gold queries, 2 chunkers, 1 embedder)
  produced **identical MRR (0.903 sentence_aware, 0.896 hierarchy) on all 4
  backends.** Differences are wall time, not accuracy.

## Pipeline

```mermaid
flowchart LR
    S[Source<br/>files · json_corpus · http · s3<br/>pg_table · mariadb_table<br/>sqlite_table · clickhouse_table<br/>inline] --> F[Framer<br/>identity · heading_boundary<br/>regex_boundary · jsonpath]
    F --> C[Chunker<br/>sentence_aware · fixed_overlap<br/>hierarchy · neighbor_expand<br/>semantic · summary_embed<br/>hierarchical_summary]
    C --> E[Embedder<br/>fastembed · ONNX · int8 or fp32]
    E --> X[Extractor<br/>none · rake_keywords · keybert_phrases<br/>spacy_entities · lang_detect · composite]
    X --> SK[Sink<br/>postgres · mariadb<br/>sqlite · clickhouse]
    SK --> DB[(Vector table + index)]
```

Each arrow is a contract — swap any box without touching the others. See
[`docs/architecture.md`](docs/architecture.md) for the per-module
breakdown and the trait surface that makes the matrix work.

## The user journey (start here)

chunkshop has one canonical loop. Every adoption goes through these five
steps:

1. **Bring real data.** Your corpus — sales notes, NTSB reports, court
   rulings, a docs site, whatever you actually need to retrieve over.
2. **Write a small gold set.** ~10 hand-crafted queries, each paired with
   the doc that *should* rank #1.
3. **Run the bakeoff.** `chunkshop bakeoff` runs every (chunker × embedder
   × backend) combo against your corpus and gold set. Out comes a
   leaderboard and a `recommended.yaml` — the recipe that won on *your*
   data on *your* backend.
4. **Ship the recommended cell.** `chunkshop ingest --config recommended.yaml`
   runs that recipe in production.
5. **New corpus → repeat from step 2.** Each domain gets its own bakeoff;
   each production pipeline gets its own tuned cell.

The bakeoff is **step 1 of every adoption**, not a sample. It's the
experiment that picks the recipe you'll ship with.

```bash
# 1. Install
cd chunkshop/python && uv sync --extra dev --extra all-backends && cd ..

# 2. Pick your backend. Postgres is the default; SQLite for zero-server.
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# 3. Run the canonical demo: bakeoff against the NTSB aviation-accident corpus
chunkshop bakeoff --config docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml --yes

# 4. Take the recommended cell to production
chunkshop ingest --config skill-output/bakeoff/ntsb_bakeoff/recommended.yaml
```

The walkthrough — install → bakeoff → recommended → ingest, with the
NTSB corpus as the worked example — lives in
[**`docs/getting-started.md`**](docs/getting-started.md).

Already past step 3 and just want the runtime? `chunkshop ingest --config
<your-cell>.yaml`. Same for Rust: `chunkshop-rs ingest --config
<your-cell>.yaml`.

## Pick your backend

| Backend | When to use it | Engine doc |
|---|---|---|
| **Postgres** + pgvector | Default. Mature, full feature surface, HNSW. | [`docs/engines/postgres.md`](docs/engines/postgres.md) |
| **MariaDB 11.7+** | MySQL-family stack; no extension install. | [`docs/engines/mariadb.md`](docs/engines/mariadb.md) |
| **SQLite** + sqlite-vec | Embedded / no server. Notebooks, CI, edge. | [`docs/engines/sqlite.md`](docs/engines/sqlite.md) |
| **ClickHouse 24.10+** | OLAP / append-only / big corpora. | [`docs/engines/clickhouse.md`](docs/engines/clickhouse.md) |

Mixing sources from one engine into sinks on another is fully supported and
test-pinned. See [`docs/mixing-sources-and-sinks.md`](docs/mixing-sources-and-sinks.md).

## Status

| Impl | Path | State |
|---|---|---|
| Python reference | `python/` | Published on [PyPI](https://pypi.org/project/chunkshop/). All features. int8 default. |
| Rust | `rust/` | Published on [crates.io](https://crates.io/crates/chunkshop-rs). Full pipeline + 4 backends. Bakeoff is PG-only (multi-target Rust bakeoff is a v0.4.1 follow-up). Orchestrator is Python-only. |
| Go | `go/` | Not started. |

### What "parity" means and doesn't mean

| Layer | Python | Rust |
|---|---|---|
| Source / framer / chunker / embedder / extractor / sink | ✅ all 4 backends | ✅ all 4 backends |
| `Pipeline` (inline / library mode) | ✅ | ✅ |
| `chunkshop ingest` (one YAML → one cell) | ✅ | ✅ |
| **16-cell cross-backend matrix (4 sources × 4 sinks)** | ✅ | ✅ |
| `chunkshop bakeoff` (matrix → leaderboard → recommended.yaml) | ✅ multi-backend | ✅ PG-only |
| `chunkshop orchestrate` (N cells as parallel subprocesses) | ✅ | ❌ |
| Embedder registry breadth | full fastembed catalogue + custom-registered HF | BGE int8 (bit-near-exact) + nomic v1.5 + stock fastembed-rs catalogue + YAML-driven HF |

## Defaults

The example config ships with `chunker.type: hierarchy` and
`embedder.model_name: Xenova/bge-base-en-v1.5-int8`.

**Chunker choice is benchmark-backed.** chunkshop's factorial on a 772-doc
legal QA corpus (30 gold questions, `gpt-4.1-mini` answer + judge) found:

- **Hierarchy chunker wins across every embedder column** — prepending the
  section heading to each embedded chunk adds free framing context.
- **int8 >= fp32 in aggregate** (160 vs 152 fully_correct across 12 cells)
  with 2× faster ingest.
- Zero hallucinations across 720 answers — prompt discipline, not model
  choice.

**Embedder default is MTEB-backed.** `bge-base` beats `bge-small` by ~3–5
points on public retrieval benchmarks (MTEB). Swap to
`Xenova/bge-small-en-v1.5-int8` for a smaller footprint (~35 MB, 384 dim)
or `nomic-ai/nomic-embed-text-v1.5-Q` for long-context (8k tokens). Run
the factorial configs against your own corpus to confirm. See
[`docs/embedders.md`](docs/embedders.md).

## YAML shape

One YAML = one cell = one end-to-end ingest. Six sections (framer optional,
defaults to identity):

| Section | Types |
|---|---|
| source | files · json_corpus · http · s3 · pg_table · mariadb_table · sqlite_table · clickhouse_table · inline |
| framer | identity (default) · heading_boundary · regex_boundary · jsonpath |
| chunker | sentence_aware · fixed_overlap · hierarchy · neighbor_expand · semantic · summary_embed · hierarchical_summary |
| embedder | fastembed (ONNX via `fastembed` in Python, `ort` in Rust) |
| extractor | none · rake_keywords · keybert_phrases · spacy_entities · lang_detect · composite (opt-in extras) |
| target | postgres · mariadb · sqlite · clickhouse; `mode: overwrite \| append \| create_if_missing`; `source_tag` + `promote_metadata` for multi-source tables |

Full field-by-field reference: [`python/README.md`](python/README.md).
Per-engine specifics: [`docs/engines/`](docs/engines/).

## Target table schema (PG variant, others structurally identical)

```sql
CREATE TABLE {schema}.{table} (
    id                  text PRIMARY KEY,        -- "{doc_id}::{seq_num}"
    doc_id              text NOT NULL,
    seq_num             int  NOT NULL,
    original_content    text NOT NULL,           -- raw chunk, for grep / fact-match / audit
    embedded_content    text NOT NULL,           -- what was embedded (may include heading prefix, neighbors)
    tags                text[] NOT NULL DEFAULT '{}',
    metadata            jsonb NOT NULL DEFAULT '{}',
    embedding           vector({dim}) NOT NULL,
    source              text,                    -- write-once provenance
    created_at          timestamptz NOT NULL DEFAULT now()
);
-- plus: UNIQUE on (doc_id, seq_num); HNSW index on embedding if hnsw=true
```

MariaDB uses `VECTOR(N)` + `JSON` columns; SQLite uses a two-table dance
(main + vec0 virtual); ClickHouse uses `Array(Float32)` + `MergeTree`
engine. Storage shape is identical; only column types differ. See
[`docs/storage-model.md`](docs/storage-model.md) and per-engine docs.

## Documentation

### Start here

| Doc | For |
|---|---|
| [`docs/executive-summary.md`](docs/executive-summary.md) | Two-page overview: what, why, current state, who should use it. |
| [`docs/getting-started.md`](docs/getting-started.md) | Zero-to-retrieval end-to-end walkthrough. |
| [`docs/architecture.md`](docs/architecture.md) | The trait surface, the pipeline, the cross-language parity story. |
| [`docs/upgrading.md`](docs/upgrading.md) | Version-to-version migration notes. |
| [`docs/benchmarks.md`](docs/benchmarks.md) | Measured performance + accuracy across backends. HNSW vs brute-force, concurrent-ingest scaling, 8k-chunk throughput. |

### Pick your backend

| Doc | For |
|---|---|
| [`docs/engines/postgres.md`](docs/engines/postgres.md) | Postgres + pgvector reference. |
| [`docs/engines/mariadb.md`](docs/engines/mariadb.md) | MariaDB 11.7+ reference. |
| [`docs/engines/sqlite.md`](docs/engines/sqlite.md) | SQLite + sqlite-vec reference. |
| [`docs/engines/clickhouse.md`](docs/engines/clickhouse.md) | ClickHouse 24.10+ reference. |
| [`docs/mixing-sources-and-sinks.md`](docs/mixing-sources-and-sinks.md) | How sources and sinks compose — the 16-cell matrix, when to mix. |

### Pipeline references

| Doc | For |
|---|---|
| [`docs/chunkers.md`](docs/chunkers.md) | Each chunker: what it does, when to pick it, oversize behavior. |
| [`docs/embedders.md`](docs/embedders.md) | Embedder mechanics: registration patterns, BYO mode, A/B testing. |
| [`docs/embedder-catalogue.md`](docs/embedder-catalogue.md) | Verified-working models, known-broken ones, dim / size. |
| [`docs/extractors.md`](docs/extractors.md) | Each extractor: why use it, config, promoted-column pairing. |
| [`docs/summaries.md`](docs/summaries.md) | summary_embed + hierarchical chunker reference. |
| [`docs/storage-model.md`](docs/storage-model.md) | What chunkshop writes per row + how to query each payload. |
| [`docs/query-clients.md`](docs/query-clients.md) | Query the ingested table from Python, JS/TS, Rust, Go. |

### Tutorials

| Doc | For |
|---|---|
| [`docs/tutorial.md`](docs/tutorial.md) | Start here. End-to-end walkthrough. |
| [`docs/tutorial-bakeoff.md`](docs/tutorial-bakeoff.md) | Bakeoff walkthrough: pick the best combo for your corpus. |
| [`docs/tutorial-multi-source.md`](docs/tutorial-multi-source.md) | Multi-source ingest: two cells, one table, filter by source. |
| [`docs/tutorial-framers.md`](docs/tutorial-framers.md) | DocFramer walkthrough: heading splits + JSON expansion. |
| [`docs/tutorial-metadata.md`](docs/tutorial-metadata.md) | Metadata extraction + promoted columns + filtered queries. |
| [`docs/tutorial-semantic.md`](docs/tutorial-semantic.md) | Semantic chunker walkthrough. |
| [`docs/tutorial-summaries.md`](docs/tutorial-summaries.md) | summary_embed + hierarchical walkthrough. |

### Quickstart recipe cards

| Doc | For |
|---|---|
| [`docs/quickstart-bakeoff.md`](docs/quickstart-bakeoff.md) | Common bakeoff shapes (embedder-only, chunker-only, full factorial). |
| [`docs/quickstart-multi-source.md`](docs/quickstart-multi-source.md) | Schema-flex modes + append pre-flight. |
| [`docs/quickstart-framers.md`](docs/quickstart-framers.md) | Which framer for which source shape. |
| [`docs/quickstart-extractors.md`](docs/quickstart-extractors.md) | Copy-paste YAML per extractor. |
| [`docs/quickstart-semantic.md`](docs/quickstart-semantic.md) | Semantic chunker knobs. |
| [`docs/quickstart-summaries.md`](docs/quickstart-summaries.md) | summary_embed + hierarchical_summary recipes. |

## Monorepo layout

```
chunkshop/
├── python/                 reference implementation; published to PyPI
│   ├── src/chunkshop/
│   │   ├── backends/       Backend dialect/connect per engine
│   │   ├── sinks/          Sink data-model semantics per engine
│   │   ├── sources/        Source impls (files, http, s3, *_table, ...)
│   │   ├── chunkers/
│   │   ├── embedders/
│   │   ├── extractors/
│   │   ├── framers/
│   │   ├── summarizers/
│   │   ├── bakeoff/
│   │   ├── runner.py
│   │   ├── orchestrator.py
│   │   └── cli.py
│   └── tests/
├── rust/                   second implementation; published to crates.io
│   └── chunkshop/
│       ├── src/
│       │   ├── backends/
│       │   ├── sinks/
│       │   ├── sources/
│       │   ├── bakeoff/
│       │   ├── chunker.rs
│       │   ├── embedder.rs
│       │   ├── extractor.rs
│       │   ├── framer.rs
│       │   ├── runner.rs
│       │   └── main.rs
│       └── tests/
├── go/                     planned
└── docs/
    ├── architecture.md
    ├── mixing-sources-and-sinks.md
    ├── engines/            ← per-engine docs (postgres, mariadb, sqlite, clickhouse)
    ├── tutorial.md
    ├── chunkers.md
    ├── embedders.md
    ├── extractors.md
    ├── storage-model.md
    └── samples/            ← sample corpora + runnable configs + bakeoffs
```

## License

MIT. See `LICENSE`.
