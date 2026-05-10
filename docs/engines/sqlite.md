# SQLite + sqlite-vec

chunkshop's embedded backend. Zero-server, in-process vector search via the
[`sqlite-vec`](https://github.com/asg017/sqlite-vec) extension. Use for
notebooks, CI, edge ingest, or any case where running a database server is
overkill.

## Status

| Capability | State |
|---|---|
| Ingest sink (`target.type: sqlite`) | ✅ Python and Rust |
| `sqlite_table` source (`source.type: sqlite_table`) | ✅ Python and Rust |
| Sink modes: `overwrite` / `append` / `create_if_missing` | ✅ all three |
| HNSW index (`target.hnsw: true`) | ⚠️ no-op — emits a one-time warning |
| `delete_orphans` | ✅ (deletes from BOTH main + vec0 tables) |
| `promote_metadata` | ✅ |
| Multi-source `source_tag` | ✅ |
| Bakeoff CLI (Python) | ✅ multi-backend |
| Bakeoff CLI (Rust) | ❌ Rust bakeoff is PG-only (v0.4.1 follow-up) |
| `:memory:` mode | ✅ — DSN env can point at `":memory:"` for ephemeral runs |

## Connection

SQLite has no network DSN. chunkshop's `dsn_env:` names an env var whose
**value is the file path** (or `:memory:`).

```bash
export SQLITE_PATH="/tmp/my_chunks.db"
# or for ephemeral:
export SQLITE_PATH=":memory:"
```

```yaml
target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored     # SQLite has no schema namespace; field accepted but ignored
  table: chunks
```

Driver: `sqlite-vec` Python package on Python side; `rusqlite` (with
`bundled` + `load_extension` features) + `sqlite-vec` extension loaded at
runtime on the Rust side.

## Schema model

SQLite has no schema namespace, so chunkshop's `database:` YAML field is
validated as an identifier but **ignored at runtime**. The table-name layout
uses a two-table dance because sqlite-vec's `vec0` virtual table refuses
arbitrary columns:

```
<your-file>.db
├── chunks                ← main table (id, doc_id, original_content, …)
│     PRIMARY KEY id
│     (no embedding column!)
└── chunks_vec            ← vec0 virtual table (id, embedding)
      ENGINE: vec0
      embedding float[384]
```

Joined on `id` for queries:

```sql
SELECT c.doc_id, c.original_content, v.distance
FROM chunks c
JOIN chunks_vec v ON c.id = v.id
WHERE v.embedding MATCH '[…]' AND k = 5
ORDER BY v.distance;
```

The sink's `query_top_k(query_vec, k)` issues this join for you.

## Sample YAML

```yaml
cell_name: sqlite_ingest

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
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored
  table: chunks
  mode: overwrite
  source_tag: my_corpus_v1
```

A full sample lives at [`docs/samples/sample-sqlite.yaml`](../samples/sample-sqlite.yaml).

## Sink modes

Same semantics as Postgres — see [`postgres.md`](postgres.md#sink-modes).
The append pre-flight checks both the main table AND the `_vec` partner
table; a table without its `_vec` companion is rejected as "not a
chunkshop table." `delete_orphans` removes by-id from both partner tables
atomically.

## Querying

From Python:

```python
import sqlite3, sqlite_vec, os
from chunkshop.embedders import build_embedder
from chunkshop.config import FastembedEmbedder

conn = sqlite3.connect(os.environ["SQLITE_PATH"])
conn.enable_load_extension(True)
sqlite_vec.load(conn)

emb = build_embedder(FastembedEmbedder(type="fastembed",
    model_name="Xenova/bge-small-en-v1.5-int8", dim=384))
[qvec] = emb.embed(["..."])
qvec_str = "[" + ",".join(str(x) for x in qvec) + "]"

rows = conn.execute(
    "SELECT c.doc_id, c.original_content, v.distance "
    "FROM chunks c JOIN chunks_vec v ON c.id = v.id "
    "WHERE v.embedding MATCH ? AND k = 5 ORDER BY v.distance",
    (qvec_str,),
).fetchall()
```

The chunkshop sink wraps this in `query_top_k(query_vec, k)`.

## Gotchas

- **`target.hnsw: true` is a no-op.** sqlite-vec uses **brute-force KNN** —
  there is no index data structure to build. The sink emits a one-time
  process-level warning when it sees `hnsw: true`. Set `hnsw: false` to
  silence it. Querying with `embedding MATCH '[...]' AND k = N` works just
  fine without an index.
- **vec0 refuses UPSERT / INSERT OR REPLACE.** chunkshop's write path does
  `DELETE FROM chunks_vec WHERE id = ?` then `INSERT` — two statements per
  chunk on rewrites. Fine for tens of thousands of chunks; less ideal for
  millions.
- **No schema namespace.** `database:` is accepted for parity with the other
  backends' YAML shape, but ignored at runtime. Don't try to scope two
  chunkshop tables by "database" — point them at different `.db` files
  instead.
- **No connection pool.** rusqlite holds a single connection per backend
  instance. Concurrent ingest into the same `.db` file from multiple cells
  will serialize on SQLite's file lock; this is fine for a single-machine
  build, painful for fan-out.
- **`:memory:` is per-connection.** If you set `dsn_env` to a var resolving
  to `:memory:`, every fresh `connect()` is a fresh empty database. Use for
  tests; not useful for cross-process ingest.

## When to use SQLite

- **CI / tests / fixtures.** No server to provision; `:memory:` for fast
  test isolation.
- **Notebook prototyping.** Ingest 1k docs, run some retrieval, throw the
  file away.
- **Edge ingest.** Bake the vector store into the app binary; chunkshop +
  sqlite-vec ships ~5 MB of native code total.
- **Single-machine workloads under ~1M chunks.** Brute-force KNN is fast
  enough at that scale; the operational simplicity is worth it.
- **DON'T use it for** concurrent multi-cell ingest into one file, or for
  >10M chunk workloads where HNSW recall + speed matters.

See [`postgres.md`](postgres.md) for the production-scale alternative,
[`../mixing-sources-and-sinks.md`](../mixing-sources-and-sinks.md) for the
classic "develop on SQLite, ship to PG" pattern.
