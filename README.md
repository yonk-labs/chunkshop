# chunkshop

A small, standalone, embeddable ingestion tool. Pulls text from a source, chunks it, embeds it,
optionally tags it, and lands the result in a pgvector table. Designed to be consumed as a
library or driven from the command line.

## Status

- **Python** (`python/`): v0.2.0, int8 by default.
- **Rust** (`rust/`): planned.
- **Go** (`go/`): planned.

## Defaults, from the benchmark

The example config ships with `chunker.type: hierarchy` and `embedder.model_name:
Xenova/bge-small-en-v1.5-int8` because chunkshop's own factorial benchmark on a 772-doc
legal QA corpus (30 gold-labeled questions, `gpt-4.1-mini` answer + judge) found:

- **Hierarchy chunker wins across every embedder column** — prepending the section
  heading to each embedded chunk adds free framing context.
- **int8 >= fp32 in aggregate** (160 vs 152 fully_correct across 12 cells) with 2x faster
  ingest. int8 `bge-small` ties the best fp32 cell at 18/30.
- Zero hallucinations across 720 answers (both runs) — prompt discipline, not model choice.

Swap to fp32 (`BAAI/bge-small-en-v1.5`) or nomic (`nomic-ai/nomic-embed-text-v1.5`) if your
corpus needs the extra recall margin. Full benchmark data in the `pg-raggraph` repo.

All three implementations share the same YAML config schema, the same ONNX Runtime-based
embedder (via `fastembed` in Python; via `ort` in Rust; via `onnxruntime_go` in Go), and the
same target table layout — so vectors produced by any of them are interchangeable.

## Shape

One YAML = one "cell" = one end-to-end ingest. Five sections:

| Section   | Types                                                                        |
|-----------|------------------------------------------------------------------------------|
| source    | files · json_corpus · pg_table · http (stub) · s3 (stub)                     |
| chunker   | sentence_aware · fixed_overlap · hierarchy · neighbor_expand                 |
| embedder  | fastembed (Python); onnx_direct (Rust, Go, optional in Python)               |
| extractor | none · rake_keywords (Python)                                                |
| target    | pgvector table `{schema}.{table}` with HNSW index                            |

See `python/README.md` for the reference documentation and example YAMLs.

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
```

## Monorepo layout

```
chunkshop/
├── python/                 reference implementation; runs MVP today
│   ├── src/chunkshop/
│   └── tests/
├── rust/                   planned
├── go/                     planned
└── docs/
```

## License

MIT. See `LICENSE`.
