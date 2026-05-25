# Postgres + pgvector

chunkshop's reference backend. Full feature surface, mature, fastest path to
production. Use as the default unless you have a reason not to.

## Status

| Capability | State |
|---|---|
| Ingest sink (`target.type: postgres`) | ✅ Python and Rust |
| `pg_table` source (`source.type: pg_table`) | ✅ Python and Rust |
| Sink modes: `overwrite` / `append` / `create_if_missing` | ✅ all three |
| HNSW index (`target.hnsw: true`) | ✅ pgvector native |
| `delete_orphans` (per-doc shrink cleanup) | ✅ |
| `promote_metadata` (typed columns from jsonb) | ✅ |
| `target.documents` companion document table | ✅ Python only; Rust rejects until parity lands |
| Multi-source `source_tag` provenance | ✅ |
| Bakeoff CLI (Python — `chunkshop bakeoff`) | ✅ multi-backend |
| Bakeoff CLI (Rust — `chunkshop-rs bakeoff`) | ✅ PG-only |

## Connection

```bash
export CHUNKSHOP_DSN="postgresql://user:pass@host:5432/database"
```

The YAML names the env var, not the DSN itself, so the same config travels
between dev / staging / prod without edits:

```yaml
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: my_schema   # mapped to PG SCHEMA at the sink — see below
  table: chunks
```

Tested against Postgres 14, 15, 16. Requires the `pgvector` extension (CREATE
EXTENSION vector). pgvector 0.7+ recommended for HNSW.

## Schema model

chunkshop maps its `database` YAML field to a Postgres **SCHEMA**, not a
database name. One Postgres database holds N chunkshop schemas; each schema
holds one or more chunkshop tables.

```
postgres://host:5432/<your_db_here>      ← PG database (set in DSN)
    └── my_schema                         ← chunkshop YAML "database:"
        └── chunks                        ← chunkshop YAML "table:"
            ├── (id text PK)
            ├── (doc_id text, seq_num int)
            ├── (original_content, embedded_content text)
            ├── (tags text[])
            ├── (metadata jsonb)
            ├── (embedding vector(384))
            └── HNSW index (if hnsw=true)
```

The schema is created on first ingest (or dropped + recreated on
`mode: overwrite`).

## Sample YAML

Minimal ingest config:

```yaml
cell_name: pg_ingest

source:
  type: files
  glob: docs/*.md
  id_from: stem

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
  database: my_docs
  table: chunks
  mode: overwrite
  hnsw: true
  source_tag: my_corpus_v1
```

A full sample lives at [`docs/samples/sample.yaml`](../samples/sample.yaml).

## Optional document table

Python/Postgres can write a companion one-row-per-document table by enabling
`target.documents.enabled: true`. This stores document-level content, lede
summary/facts/TOC fields, optional full text, FTS text, and document-level
promoted metadata beside the chunk table. It is disabled by default and is not
implemented in Rust yet; Rust rejects enabled document stores at config load.

See [`../storage-model.md`](../storage-model.md) for the exact schema.

## Sink modes

| Mode | Behavior |
|---|---|
| `overwrite` (default) | `DROP SCHEMA ... CASCADE; CREATE SCHEMA; CREATE TABLE`. Refuses to drop a table that holds rows with a different `source_tag` unless `target.force_overwrite: true`. |
| `append` | Writes additional rows to an existing chunkshop table. Pre-flight checks the table exists, has the `embedding vector(N)` column, and matches the cell's embedder dim. `source_tag` is required. Promote columns added with `ADD COLUMN IF NOT EXISTS`. |
| `create_if_missing` | Like `overwrite` if the table is absent; like `append` if it exists. The right choice for "first cell of a multi-source ingest." |

## Querying

After ingest, query directly with pgvector's cosine operator. Top-5 from
a Python client:

```python
import psycopg
from chunkshop.embedders import build_embedder
from chunkshop.config import FastembedEmbedder

emb = build_embedder(FastembedEmbedder(type="fastembed",
    model_name="Xenova/bge-small-en-v1.5-int8", dim=384))
[qvec] = emb.embed(["what does the report say about runway lighting?"])

with psycopg.connect(os.environ["CHUNKSHOP_DSN"]) as conn:
    rows = conn.execute(
        "SELECT doc_id, original_content "
        "FROM my_docs.chunks ORDER BY embedding <=> %s LIMIT 5",
        (str(qvec),),
    ).fetchall()
```

Or via the sink's `query_top_k(query_vec, k)` method from either language.
See [`docs/query-clients.md`](../query-clients.md) for full client examples.

## Benefits

- **Most mature path.** pgvector is years old, widely deployed, well-tuned.
  Every chunkshop feature works here first; other backends catch up.
- **Native HNSW.** `hnsw: true` builds a proper approximate-nearest-neighbor
  index. Queries on 100k+ chunks stay sub-100ms with the index; brute-force
  on the same table would be seconds.
- **Full upsert semantics.** `INSERT ... ON CONFLICT (id) DO UPDATE` lets
  cells safely re-run without duplicating rows. Re-ingest the same corpus
  with a new chunker — primary key collisions overwrite cleanly.
- **Vectors join to your operational data.** Chunks in one schema, customers
  in another — `JOIN` across them in a single query. None of the other 3
  backends give you this without an ETL stage.
- **Live progress queries work.** Per-document transactions mean
  `SELECT COUNT(DISTINCT doc_id) FROM ...` from another `psql` session
  reflects in-progress ingest state.

## Limitations

These are intrinsic to Postgres or pgvector — chunkshop won't change them:

- **HNSW build is upfront.** With `hnsw: true`, the index builds after
  ingest finishes. For >100k chunks expect minute-scale build time.
  Workaround: skip during bakeoffs (`hnsw: false`), enable for production.
- **Per-document transactions** make this a batch tool, not a streaming
  one. One INSERT + COMMIT per document. Fine for batches up to ~1M docs;
  unsuited for online single-row ingest at high QPS.
- **`source` column is write-once on `ON CONFLICT`.** The sink deliberately
  excludes `source` from the UPDATE clause. Two cells colliding on
  `(doc_id, seq_num)` → first writer's `source_tag` wins forever.
  Provenance is load-bearing, not a race.

## Gaps

Tracked for future versions, not in v0.4.0:

- **No IVFFlat index variant.** chunkshop only exposes the HNSW knob;
  pgvector also supports IVFFlat (smaller index, slower queries). If you
  need IVFFlat for memory-constrained deployments, build the index manually
  outside chunkshop after ingest.
- **No `halfvec` / `bit` column type.** chunkshop writes `vector(N)`; pgvector
  also supports half-precision and binary vector types for compression. Not
  on the v0.4 roadmap; if you need this, file an issue.

## Troubleshooting

**`ERROR: type "vector" does not exist`**

The pgvector extension isn't installed in your target database. Run as
superuser:

```sql
CREATE EXTENSION vector;
```

chunkshop's sink does NOT auto-create the extension — that requires
superuser, which most DSNs don't have.

**`database:` confusion**

If you're staring at psql and your chunkshop table doesn't appear under
`\dt`, you're probably in the wrong schema. The YAML `database: my_docs`
maps to a PG schema, not a database:

```sql
-- Wrong: looking in the public schema
SELECT * FROM chunks;

-- Right: chunkshop wrote to "my_docs.chunks"
SELECT * FROM my_docs.chunks;
-- or
SET search_path = my_docs, public;
SELECT * FROM chunks;
```

**`mode: overwrite` refused due to foreign source_tag**

If you see `overwrite refuses to drop {schema}.{table}: table holds rows
with source_tag X, this cell's source_tag is Y`, the table was populated
by a different cell. Either:

- Use `mode: append` if you meant to add rows alongside the existing cell's data.
- Set `force_overwrite: true` if you really do want to drop a foreign cell's data.
- Pick a different `database:` (schema) to keep cells isolated.

**HNSW queries are slow on a fresh-ingest table**

Did you run `ANALYZE my_docs.chunks;`? Postgres' query planner sometimes
picks brute-force seq-scan over the HNSW index without fresh statistics.

**Connection pool exhaustion under `chunkshop orchestrate`**

Each cell subprocess opens its own per-document connection. With
`orchestrate --concurrency 8`, you have up to 8 concurrent connections per
backend. Make sure your PG `max_connections` accommodates the orchestrator
+ any other consumers.

## When to use Postgres

- **Default choice.** Mature pgvector, fast, every chunkshop feature works.
- **You already run Postgres.** No reason to add another datastore.
- **Mixed workload.** When chunk vectors sit next to operational data and you
  want joins across them.
- **You need ON CONFLICT semantics.** PG and MariaDB both support upsert;
  ClickHouse and SQLite have weaker stories.

See [`mariadb.md`](mariadb.md) for the MySQL-family alternative,
[`sqlite.md`](sqlite.md) for the embedded option,
[`clickhouse.md`](clickhouse.md) for the OLAP / analytics-heavy option, and
[`../mixing-sources-and-sinks.md`](../mixing-sources-and-sinks.md) for how to
pair a Postgres source with a non-Postgres sink (and vice versa).
