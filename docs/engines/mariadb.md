# MariaDB 11.7+

chunkshop's MySQL-family backend. Native cosine via `VEC_DISTANCE_COSINE`,
no extension install required. Use when your stack standardizes on MySQL
and you don't want to bolt pgvector on the side.

## Status

| Capability | State |
|---|---|
| Ingest sink (`target.type: mariadb`) | ✅ Python and Rust |
| `mariadb_table` source (`source.type: mariadb_table`) | ✅ Python and Rust |
| Sink modes: `overwrite` / `append` / `create_if_missing` | ✅ all three |
| HNSW index (`target.hnsw: true`) | ⚠️ accepted but MariaDB has no native HNSW — uses MariaDB's vector index when available; brute-force otherwise |
| `delete_orphans` | ✅ |
| `promote_metadata` | ✅ |
| Multi-source `source_tag` | ✅ |
| Bakeoff CLI (Python) | ✅ multi-backend |
| Bakeoff CLI (Rust) | ❌ Rust bakeoff is PG-only (v0.4.1 follow-up) |

**Requires MariaDB 11.7+.** Earlier versions don't have `VEC_*` functions or
the `VECTOR(N)` column type. MySQL 8 / 9 are **not** supported — chunkshop
depends on MariaDB-specific vector syntax that hasn't been ported there.

## Connection

```bash
export CHUNKSHOP_DSN_MARIADB="mysql://root:rootpw@localhost:3307/test_db"
```

```yaml
target:
  type: mariadb
  dsn_env: CHUNKSHOP_DSN_MARIADB
  database: my_chunks   # mapped to MariaDB DATABASE
  table: chunks
```

The DSN's database (the `/test_db` segment) is the connection's default
database — it doesn't have to match the chunkshop YAML `database:`. chunkshop
will `CREATE DATABASE IF NOT EXISTS` its target database on first ingest.

Driver: PyMySQL on Python, sqlx (`mysql` feature) on Rust.

## Schema model

```
mariadb://host:3307/<default_db>          ← DSN default db (often "test")
    └── my_chunks                          ← chunkshop YAML "database:"
        └── chunks                         ← chunkshop YAML "table:"
            ├── id VARCHAR(255) PK
            ├── doc_id VARCHAR(255), seq_num INT
            ├── original_content LONGTEXT
            ├── embedded_content LONGTEXT
            ├── tags JSON  -- not text[] — MariaDB has no native array type
            ├── metadata JSON
            ├── embedding VECTOR(384)  -- 11.7+ native type
            └── (no HNSW — relies on MariaDB's vector index)
```

Two array-typed columns (`tags`, `metadata`) use MariaDB's `JSON` type
because there's no native array. Round-trip through Python's `json` module
on both sides; identical in payload to PG's `text[]` / `jsonb` columns
after deserialization.

## Sample YAML

```yaml
cell_name: mariadb_ingest

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
  type: mariadb
  dsn_env: CHUNKSHOP_DSN_MARIADB
  database: my_docs
  table: chunks
  mode: overwrite
  source_tag: my_corpus_v1
```

A full sample lives at [`docs/samples/sample-mariadb.yaml`](../samples/sample-mariadb.yaml).

## Sink modes

Same semantics as Postgres — see [`postgres.md`](postgres.md#sink-modes).
Pre-flight checks are translated to `INFORMATION_SCHEMA` queries; the foreign
`source_tag` refusal works identically. `force_overwrite: true` overrides.

## Querying

```python
import pymysql, os
from chunkshop.embedders import build_embedder
from chunkshop.config import FastembedEmbedder

emb = build_embedder(FastembedEmbedder(type="fastembed",
    model_name="Xenova/bge-small-en-v1.5-int8", dim=384))
[qvec] = emb.embed(["..."])
qvec_str = "[" + ",".join(str(x) for x in qvec) + "]"

conn = pymysql.connect(...parse DSN...)
with conn.cursor() as cur:
    cur.execute(
        "SELECT doc_id, original_content "
        "FROM my_docs.chunks "
        "ORDER BY VEC_DISTANCE_COSINE(embedding, VEC_FromText(%s)) "
        "LIMIT 5",
        (qvec_str,),
    )
    rows = cur.fetchall()
```

The chunkshop sink's `query_top_k(query_vec, k)` method wraps this; use it
from Python or Rust without writing the SQL.

## Gotchas

- **`embedding` writes go through `VEC_FromText(...)`, not parameter binding.**
  MariaDB's driver doesn't ship a typed vector parameter, so the sink renders
  vectors as text and wraps them with `VEC_FromText('[…]')`. The placeholder
  is interpolated server-side; values are still SQL-injection-safe because
  the float list is rendered through Python's repr / Rust's `format!`. No
  user-supplied string ever lands in this column.
- **`VEC_DISTANCE_COSINE` returns DOUBLE on Rust today**, read through a
  defensive `try_get::<f64>() → try_get::<f32>` cascade in the Rust sink.
  Not user-visible, but noted in v0.4.1 follow-ups for tightening.
- **`GET_LOCK` for create-table serialization is connection-scoped.** The
  Rust `acquire_create_lock` shape doesn't take a guard yet (v0.4.1
  follow-up); works in practice because the lock-holder transaction is
  short-lived.
- **`hnsw: true` is accepted but not load-bearing.** MariaDB 11.7's vector
  index is build-on-INSERT, not a separate post-ingest step like PG's HNSW.
  Set it for documentation intent; the sink doesn't emit a separate `CREATE
  INDEX` for vectors.
- **`tags JSON` doesn't round-trip identically to PG's `tags text[]` if you
  inspect the column raw.** Pythonland: `["a", "b"]` (list); MariaDB raw:
  `'["a", "b"]'` (JSON string). chunkshop's query path decodes either; only
  matters if you `SELECT tags` outside chunkshop's API.

## When to use MariaDB

- **Your stack is MySQL-family.** You already operate MariaDB; no reason to
  add Postgres.
- **You need transactional vector + relational queries in one DB.** Cosine
  joined to your customers table, no separate vector service.
- **You're on MariaDB 11.7 or newer.** If you're stuck on 10.x or MySQL, this
  isn't your backend — use Postgres or SQLite.
- **You don't need post-ingest HNSW tuning.** MariaDB's index story is
  simpler-but-less-tunable than pgvector's HNSW knobs.

See [`postgres.md`](postgres.md) for the canonical comparison,
[`../mixing-sources-and-sinks.md`](../mixing-sources-and-sinks.md) for moving
data between MariaDB and another engine.
