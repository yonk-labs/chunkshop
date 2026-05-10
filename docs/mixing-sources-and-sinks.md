# Mixing sources and sinks

chunkshop deliberately decouples *where data comes from* (Source) from *where
embeddings land* (Sink). v0.4.0 ships 4 DB sources and 4 DB sinks, plus the
non-DB sources (`files`, `json_corpus`, `http`, `s3`, `inline`). **Any
combination works.**

This isn't an accident or a side effect — it's the entire point of the trait
surface. The 16-cell cross-backend matrix test
(`rust/chunkshop/tests/cross_backend_matrix.rs` and
`python/tests/chunkshop/test_cross_backend_matrix.py`) exists to prove every
DB-source × DB-sink combination round-trips correctly. Both languages pass
all 16 cells.

## The 16-cell matrix

| Source ↓ \ Sink → | postgres | mariadb | sqlite | clickhouse |
|---|---|---|---|---|
| **pg_table** | ✅ | ✅ | ✅ | ✅ |
| **mariadb_table** | ✅ | ✅ | ✅ | ✅ |
| **sqlite_table** | ✅ | ✅ | ✅ | ✅ |
| **clickhouse_table** | ✅ | ✅ | ✅ | ✅ |

Plus the non-DB sources work into all 4 sinks:

| Source ↓ \ Sink → | postgres | mariadb | sqlite | clickhouse |
|---|---|---|---|---|
| **files** | ✅ | ✅ | ✅ | ✅ |
| **json_corpus** | ✅ | ✅ | ✅ | ✅ |
| **http** | ✅ | ✅ | ✅ | ✅ |
| **s3** | ✅ | ✅ | ✅ | ✅ |
| **inline** (library mode) | ✅ | ✅ | ✅ | ✅ |

That's 9 sources × 4 sinks = 36 valid combinations. The 16 DB×DB cells are
test-pinned; the other 20 are covered by per-source / per-sink integration
tests on each side.

## Why decouple?

Coupling source to sink is a common but bad default. chunkshop avoids it
because real workloads keep needing different combinations:

| Scenario | Source | Sink | Why |
|---|---|---|---|
| Re-embed an existing chunkshop table with a new model | `pg_table` | `postgres` | Read from old table, embed with new model, write to new table — same DB, same column types |
| Migrate from SQLite prototype to Postgres production | `sqlite_table` | `postgres` | Develop on SQLite (no server), ship to PG |
| Pull docs from operational MariaDB into an analytical CH | `mariadb_table` | `clickhouse` | Source-of-truth in MariaDB; retrieval-augmented analytics in CH |
| Ingest fresh files into multiple backends for comparison | `files` | `postgres` + `mariadb` + `sqlite` + `clickhouse` | Run 4 cells, one per sink; compare retrieval quality |
| Backfill a clickhouse table from an S3 bucket | `s3` | `clickhouse` | One-shot bulk load, no transactional concerns |
| Embed inline text from a webapp | `inline` | `postgres` | Library mode — your app drives `pipeline.ingest_text(...)` |

In every case, the YAML is two configs side by side (source + target). The
chunker, embedder, and extractor stages are unchanged. **You never edit the
pipeline; you swap the bookends.**

## A worked example: SQLite → PG

You prototyped on SQLite. You're ready to ship to Postgres. Two cells, one
ingest each:

**Step 1.** Read from your prototype SQLite, write to fresh PG:

```yaml
# migrate-sqlite-to-pg.yaml
cell_name: migrate

source:
  type: sqlite_table
  dsn_env: SQLITE_PATH       # /path/to/prototype.db
  database: ignored
  table: chunks
  id_column: doc_id          # use doc_id from the SQLite side as the new ID
  content_column: original_content

# Same chunker / embedder / extractor stack as the original SQLite cell.
chunker:
  type: hierarchy
  max_chars: 800

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384

target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: production_chunks
  table: chunks
  mode: overwrite
  source_tag: migrated_from_sqlite
  hnsw: true
```

```bash
chunkshop ingest --config migrate-sqlite-to-pg.yaml
```

That's it. chunkshop re-chunks and re-embeds — you don't carry the SQLite
embedding directly. **This is intentional**: it lets you change the embedder
during the migration if you want a different model in production. If you
want to copy the exact vectors without re-embedding, see "Copying without
re-embedding" below.

## A worked example: multi-target bakeoff

The Python bakeoff CLI supports multi-backend natively. One config, one
command, leaderboards across all 4 backends:

```yaml
# bakeoff-everywhere.yaml
name: cross_backend_bakeoff

source:
  type: files
  glob: my_corpus/*.md

gold_queries: my_gold_queries.yaml

matrix:
  embedders:
    - { type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384 }
  chunkers:
    - { type: hierarchy }
    - { type: sentence_aware }

targets:
  - { type: postgres,   dsn_env: PG_DSN,   database: bk_pg }
  - { type: mariadb,    dsn_env: MD_DSN,   database: bk_md }
  - { type: sqlite,     dsn_env: SQ_PATH,  database: ignored }
  - { type: clickhouse, dsn_env: CH_DSN,   database: bk_ch }
```

```bash
chunkshop bakeoff --config bakeoff-everywhere.yaml --yes
```

The report (`skill-output/bakeoff/cross_backend_bakeoff/report.md`) shows
MRR + ingest latency + query latency per backend side by side.
**MRR should be identical across backends** when the chunker and embedder
are the same — the only differences are wall time. The v0.4.0 validation run
(8 cells, NTSB corpus) confirmed: same MRR (0.903 for `sentence_aware`,
0.896 for `hierarchy`) on all 4 backends.

The Rust bakeoff CLI is **PG-only** as of v0.4.0; multi-target Rust support
is a v0.4.1 follow-up. For cross-backend bakeoff today, use Python.

## Copying without re-embedding

When you want to physically move vectors between backends without re-running
the embedder (e.g., backups, sharding), use a direct backend-to-backend
script. chunkshop's CLI doesn't ship one — vectors are produced by the
pipeline, not transported by it. The pattern is:

```python
import psycopg
from clickhouse_connect import get_client

# Read from PG.
with psycopg.connect(os.environ["CHUNKSHOP_DSN"]) as pg:
    rows = pg.execute("SELECT id, doc_id, seq_num, original_content, "
                      "embedded_content, tags, metadata, embedding "
                      "FROM source_schema.chunks").fetchall()

# Write to CH.
ch = get_client(host=..., password=...)
ch.insert("dest_db.chunks",
          rows,
          column_names=["id", "doc_id", "seq_num", "original_content",
                        "embedded_content", "tags", "metadata", "embedding"])
```

Both languages can read vectors written by either; the storage format
(text columns + float32 vector) is shared. Cross-language parity tests
(`tests/cross_language_*.rs` / `tests/test_cross_language_*.py`) verify
identical vectors round-trip.

## When NOT to mix

- **Production single-backend.** Pick one engine and stick with it. The
  matrix exists to give you flexibility, not to encourage multi-backend
  deployments. Operating four databases is four times the toil.
- **Tight latency budgets.** Cross-engine pipelines add network hops. If
  your retrieval path is "user query → embed → query backend," the backend
  should be close to the embedder, not on a different cluster.
- **Bakeoff except as a one-shot.** Run bakeoffs to *pick* a backend, not
  to validate every commit against every backend forever. The cross-backend
  matrix tests already pin parity at the integration level.

## What stays the same across all 4 backends

| Behavior | All 4 backends |
|---|---|
| YAML schema | Identical |
| Chunker / embedder / extractor stack | Identical |
| Per-chunk storage: `original_content`, `embedded_content`, `embedding`, `tags`, `metadata` | Identical |
| `source_tag` provenance + `mode: overwrite/append/create_if_missing` | Identical (with engine-specific caveats — see per-engine docs) |
| `query_top_k(query_vec, k)` sink method | Identical signature; identical ranked output for the same input vector |
| MRR / recall on the same corpus + chunker + embedder | Identical (verified in v0.4.0 bakeoff) |

## What's engine-specific

Read the per-engine docs for the gotchas that only apply to one:

- [`engines/postgres.md`](engines/postgres.md) — PG-native HNSW, schema semantics
- [`engines/mariadb.md`](engines/mariadb.md) — `VEC_FromText` interpolation, 11.7+ requirement
- [`engines/sqlite.md`](engines/sqlite.md) — two-table dance, `hnsw=true` no-op
- [`engines/clickhouse.md`](engines/clickhouse.md) — append-only, `delete_orphans` no-op, `ReplacingMergeTree` for dedup

## The matrix as a regression net

`cargo test --test cross_backend_matrix` and `pytest tests/chunkshop/test_cross_backend_matrix.py` are the trip-wires. If you propose a change to a sink, a source, or a backend dialect that breaks one of the 16 cells, the matrix fails CI. The matrix is intentionally small (1 doc per cell, sentence-aware chunker, real fastembed) to fit a 3-minute wall-time budget while still exercising every code path.

See [`architecture.md`](architecture.md) for how the trait surface enables
this composition, and [`engines/`](engines/) for the per-engine details.
