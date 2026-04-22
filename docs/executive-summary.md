# chunkshop — executive summary

**One YAML config → one end-to-end document ingest into a pgvector table.**
Read from a source, optionally re-frame the rows, chunk the text, embed each
chunk with an ONNX model, optionally extract metadata, and land it in
Postgres. Embeddable as a Python library or driven from the CLI. One schema
across Python, Rust, and Go (the latter two planned) — vectors are
interchangeable across implementations.

## The problem this solves

RAG and retrieval systems need a clean pipeline between "documents" and
"vectors in a database." Teams assemble this from scratch every time: one
script to pull from S3, another to split markdown, another to call an
embedder, another to write to Postgres, another to remember which model
version goes with which table. The glue is brittle, the defaults are
invisible, and the configuration lives in code. When you want to swap an
embedder or try a new chunking strategy, you touch five files.

chunkshop collapses that pipeline into a single declarative config. Every
axis (source, framer, chunker, embedder, extractor, target) is a Protocol
with one or two methods; new providers drop into their package directory
without touching the runner. Every config field has a documented default
that a knowledgeable engineer would have picked anyway.

## Current state (v0.2.0 alpha, Python reference impl)

**Shipping today:**

- **5 source types:** `files` (glob), `json_corpus`, `pg_table`, `http` (stub),
  `s3` (stub).
- **4 framers** (Source → Chunker layer for messy corpora): `identity`
  (default), `heading_boundary`, `regex_boundary`, `jsonpath`. A JSON API
  dump becomes per-item documents in 4 lines of YAML instead of a 30-line
  Python splitter.
- **4 chunkers:** `sentence_aware`, `fixed_overlap`, `hierarchy` (default,
  heading-aware), `neighbor_expand` (wraps any base chunker, glues ±N
  neighbor context into the embedding).
- **6 embedder options** via `fastembed` (ONNX Runtime + HF tokenizers):
  fp32 and int8 variants of BGE-small, BGE-base, and Nomic-embed. Int8
  variants are pre-registered; swap with one YAML line.
- **5 extractors:** `none`, `rake_keywords` (built-in), plus three opt-in
  pip extras: `keybert_phrases`, `spacy_entities`, `lang_detect`, and a
  `composite` that chains them.
- **Schema-flex target:** `mode: overwrite | append | create_if_missing`,
  `source_tag` for write-once provenance, `promote_metadata` to lift jsonb
  paths into typed, indexable columns (e.g., `entities.ORG` → `text[]`).
- **Parallel orchestration:** `chunkshop orchestrate --concurrency N`
  spawns cells as subprocesses — ONNX Runtime's process-global state makes
  this the safe default.

**Planned (not shipped):** Rust (`ort` + `tokenizers`) and Go
(`onnxruntime_go` + `hugot`) ports, sharing the same YAML schema and
pgvector layout. Vectors produced by any implementation will be
interchangeable.

## What makes the defaults trustworthy

Every default ships with a reason.

### Chunker default: `hierarchy`

chunkshop's internal factorial benchmark on a 772-doc legal QA corpus
(30 gold questions, `gpt-4.1-mini` answer + judge) found:

- **Hierarchy wins across every embedder column** — prepending the
  section heading to each embedded chunk adds free framing context that
  the embedder uses.
- **int8 ≥ fp32 in aggregate** (160 vs 152 fully_correct across 12 cells)
  with 2× faster ingest.

### Embedder default: `Xenova/bge-base-en-v1.5-int8` (768 dim)

Public MTEB places bge-base ~3–5 points above bge-small. Our own
5-chunker × 3-embedder bakeoff on the shipped sample corpus (`scripts/
bench_matrix.py`, 14 hand-written gold queries):

| strategy \ embedder | `bge-small-int8` | `bge-base-int8` | `nomic-q` |
|---|---:|---:|---:|
| A: `sentence_aware`       | 0.917 | 0.929 | 0.871 |
| B: `hierarchy` (default)  | 0.917 | **0.964** | 0.911 |
| C: `fixed_overlap`        | 0.854 | 0.946 | 0.863 |
| D: `neighbor+sentence`    | 0.869 | 0.952 | 0.911 |
| E: `neighbor+hierarchy`   | **0.964** | 0.952 | 0.911 |

(MRR; higher is better; 14 queries × 4 docs.)

Two combos tie for the top at MRR=0.964: the shipped default
(`hierarchy + bge-base-int8`) and `neighbor+hierarchy + bge-small-int8`
— a smaller embedder paired with context-augmented chunks. Swap to
bge-small with neighbor_expand when disk/RAM is tight.

Full caveat: 4 docs × 14 queries is low statistical power. The bench is
a reproducible harness, not a verdict — point it at your own corpus to
decide.

### Chunker size: `max_chars: 2000`

Default enforces ≈500 tokens, safe for bge's 512-token limit. Above
that, fastembed silently truncates and the stored vector only represents
the first ~2 KB of the chunk. Raise `max_chars` to 6000 for
text-embedding-3-small's 8k context.

## Engineering quality

- **93 tests passing** (unit + integration). Integration tests skip
  cleanly when Postgres is unreachable.
- **Surgical concurrency.** `pg_advisory_xact_lock` on schema creation
  prevents the flaky `CREATE SCHEMA IF NOT EXISTS` race between parallel
  cells.
- **Pydantic-validated YAML** with `extra="forbid"` — a typo in YAML
  fails at config-load with a clear error, not at ingest time.
- **Identifier safety.** `schema`, `table`, `source_tag`, and every
  `promote_metadata` path is regex-allowlisted; SQL injection at the
  sink is prevented by construction.
- **Write-once `source` column** on ON CONFLICT upserts. Two cells
  colliding on `(doc_id, seq_num)` → the first writer's tag wins
  forever. Provenance is a guarantee, not a race.

## Two scenario libraries ship in-repo

- **`tests/sub/scenarios/`** (feature axis, 8 scenarios) — CI fixtures
  pairing specific features (markdown/hierarchy, jsonpath framer, composite
  extractor with promote, max_chars splitting, etc.) with runnable configs.
- **`tests/use-cases/scenarios/`** (use-case axis, 6 scenarios) —
  narrative demos: **support helpdesk, legal clause review,
  dev API docs RAG, research paper library, sales meeting notes
  (multilingual), e-commerce catalog.** Each pairs a business problem
  with the specific chunker/embedder/extractor combo that fits, with
  per-axis reasoning documented in the scenario README.

## Cross-language query clients

`docs/query-clients.md` ships working minimal-retrieval snippets in:
- **Python** (`fastembed` + `psycopg`)
- **JavaScript/TypeScript** Node (`@huggingface/transformers` + `pg` + `pgvector/pg`)
- **Rust** (`fastembed` crate v5 + `sqlx` + `pgvector` crate)
- **Go** (`hugot` + `pgx` + `pgvector-go`)

Ingest is Python today; **the query side is already language-neutral**
because chunkshop writes to a standard pgvector table with documented
pooling, normalization, and dim conventions.

## Who should use chunkshop

- **Building a RAG app over a corpus that fits in Postgres** (0 to ~50M
  chunks) — chunkshop is the ingest half. Pair with any pgvector query
  client.
- **Comparing chunkers/embedders on your own data** — `scripts/
  bench_matrix.py` runs a 15-combo factorial against any corpus; drop
  your files in and read the MRR grid.
- **Consolidating one-off ingest scripts** into a single YAML-driven
  pipeline per source.

**Not a fit:** streaming/real-time ingest (batch only), LLM-in-the-ingest-
path workflows (the optional extractors are all local), "retrieval-as-a-
service" (chunkshop writes; your app reads).

## Get started in 60 seconds

```bash
cd chunkshop/python
uv sync --extra dev
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"
cd ..   # repo root
chunkshop ingest --config docs/samples/sample.yaml
```

First run downloads the ONNX model (~85 MB for int8 bge-base).
Full walkthrough: [`docs/tutorial.md`](tutorial.md).
Field-by-field reference: [`python/README.md`](../python/README.md).
Architecture: [`docs/architecture.md`](architecture.md).

---

*Document status: reflects state as of the 2026-04-22 working set. For
the live record, see [`CHANGELOG.md`](../CHANGELOG.md) and
[`docs/embedders.md#full-factorial`](embedders.md) for bench numbers.*
