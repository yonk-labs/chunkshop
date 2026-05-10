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

## Gotchas

- **`database:` means SCHEMA on PG.** This trips up new users; everywhere else
  in chunkshop's vocabulary `database` is consistent, but the actual PG
  catalog object is a schema. The DSN names the database; YAML names the
  schema inside it.
- **`source` column is write-once on `ON CONFLICT`.** The PG sink deliberately
  excludes `source` from the UPDATE clause of an upsert. Two cells colliding
  on `(doc_id, seq_num)` → the first writer's `source_tag` wins forever.
  This is provenance, not a race condition.
- **`PgVectorSink.write_document` commits per-document.** Mid-run crash only
  loses the in-flight doc; the table's `{doc_id}::{seq_num}` primary key
  means a rerun upserts cleanly. Live `SELECT COUNT(DISTINCT doc_id)` from
  another psql session is a valid progress query.
- **HNSW build cost is upfront.** `hnsw: true` builds the index after
  ingest finishes. For >100k chunks expect minute-scale build time. Skip
  it during bakeoffs (`hnsw: false`); enable for production tables.

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
