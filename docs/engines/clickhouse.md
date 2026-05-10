# ClickHouse 24.10+

chunkshop's OLAP / analytical backend. Append-only by design — chunks are
written once and read many times, ideal for retrieval-augmented analytics
workloads where you'd run aggregations next to vector search.

## Status

| Capability | State |
|---|---|
| Ingest sink (`target.type: clickhouse`) | ✅ Python and Rust |
| `clickhouse_table` source (`source.type: clickhouse_table`) | ✅ Python and Rust |
| Sink modes: `create_if_missing` / `append` | ✅ |
| Sink mode: `overwrite` | ✅ (drops + recreates the table) |
| HNSW index (`target.hnsw: true`) | ⚠️ accepted; uses CH's experimental `vector_similarity` index when enabled at server level |
| `delete_orphans` | ⚠️ **NO-OP + WARN** — CH mutations are async; see gotcha below |
| `promote_metadata` | ✅ |
| Multi-source `source_tag` | ✅ |
| `engine:` override (`ReplacingMergeTree(...)`) | ✅ for lazy dedup |
| Bakeoff CLI (Python) | ✅ multi-backend |
| Bakeoff CLI (Rust) | ❌ Rust bakeoff is PG-only (v0.4.1 follow-up) |

**Requires ClickHouse 24.10+** for the `vector_similarity` index (experimental,
enabled via server config). 24.x earlier versions work for ingest/query
without the index — cosine distance just falls back to brute-force.

The `vector_similarity` index requires a server-side setting enable:

```xml
<!-- /etc/clickhouse-server/users.d/vector.xml -->
<clickhouse>
  <profiles>
    <default>
      <allow_experimental_vector_similarity_index>1</allow_experimental_vector_similarity_index>
    </default>
  </profiles>
</clickhouse>
```

(chunkshop's `docker-compose.test.yaml` mounts exactly this config under
`.docker/clickhouse-config/`.)

## Connection

```bash
export CHUNKSHOP_DSN_CH="clickhouse://user:pass@host:8123/database"
```

```yaml
target:
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_chunks
  table: chunks
```

Driver: `clickhouse-connect` (HTTP) on Python; `clickhouse` crate (HTTP) on
Rust. Both speak the HTTP interface (port 8123 by default), NOT the native
TCP wire protocol — chunkshop never opens port 9000.

## Schema model

```
clickhouse://host:8123/<default_db>       ← DSN default (often "default")
    └── my_chunks                          ← chunkshop YAML "database:"
        └── chunks                         ← chunkshop YAML "table:"
            ENGINE = MergeTree() ORDER BY (id)
            ├── id String (sorting key)
            ├── doc_id String, seq_num Int32
            ├── original_content String
            ├── embedded_content String
            ├── tags Array(String)         -- native typed array
            ├── metadata String            -- JSON-encoded; CH has Tuple types but JSON-string is portable
            ├── embedding Array(Float32)   -- length validated at INSERT
            └── INDEX … TYPE vector_similarity (if hnsw=true + server allows)
```

chunkshop creates the database on first ingest. The default engine is
`MergeTree() ORDER BY (id)`; pass `engine: "ReplacingMergeTree(created_at) ORDER
BY (id)"` in YAML to opt into lazy dedup on merge.

## Sample YAML

```yaml
cell_name: clickhouse_ingest

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
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_docs
  table: chunks
  mode: overwrite
  source_tag: my_corpus_v1
  # Optional: opt into lazy dedup at merge time
  # engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
```

A full sample lives at [`docs/samples/sample-clickhouse.yaml`](../samples/sample-clickhouse.yaml).

## Sink modes

| Mode | ClickHouse behavior |
|---|---|
| `overwrite` | `DROP DATABASE IF EXISTS ... SYNC; CREATE DATABASE; CREATE TABLE`. Same foreign-tag refusal as PG (set `force_overwrite: true` to bypass). |
| `append` | Pre-flight checks table exists and embedding dim matches; `INSERT` rows. **Will NOT delete duplicate `(doc_id, seq_num)` from prior runs** — use `ReplacingMergeTree(created_at)` if you need that. |
| `create_if_missing` | `CREATE TABLE` if absent; otherwise append. |

There's no per-row UPSERT — ClickHouse doesn't have one. The
`{doc_id}::{seq_num}` key uniqueness is upheld at *merge time* by
`ReplacingMergeTree` if you opt in; otherwise duplicates accumulate. Re-running
the same `mode: append` cell N times → N copies of each chunk on default
`MergeTree`.

## Querying

```python
import clickhouse_connect, os
from chunkshop.embedders import build_embedder
from chunkshop.config import FastembedEmbedder

client = clickhouse_connect.get_client(host=..., port=8123, password=...)

emb = build_embedder(FastembedEmbedder(type="fastembed",
    model_name="Xenova/bge-small-en-v1.5-int8", dim=384))
[qvec] = emb.embed(["..."])

result = client.query(
    "SELECT doc_id, original_content, "
    "       cosineDistance(embedding, %(qv)s) AS dist "
    "FROM my_docs.chunks "
    "ORDER BY dist LIMIT 5",
    parameters={"qv": qvec},
)
```

The chunkshop sink wraps the same query in `query_top_k(query_vec, k)`.

## Gotchas

- **`delete_orphans: true` is a no-op + warn.** CH's `ALTER TABLE ... DELETE`
  runs as an async mutation; it doesn't fit chunkshop's per-document atomic
  write contract. The sink emits a one-time process-level warning when it
  sees `delete_orphans: true`. To get dedup, use `engine:
  "ReplacingMergeTree(created_at) ORDER BY (id)"` — duplicates collapse at
  merge time (run `OPTIMIZE TABLE ... FINAL` to force merge for tests).
- **No UPSERT path.** Re-running an `append` cell against `MergeTree` writes
  duplicate rows. This is fundamental, not a chunkshop limitation — use
  `ReplacingMergeTree` or `overwrite` mode.
- **The `vector_similarity` index is experimental** in CH 24.10. Without the
  server setting enabled, `hnsw: true` doesn't error — the CREATE INDEX
  silently no-ops and queries fall back to brute-force `cosineDistance`. If
  you measure that queries are slower than expected on big tables, the
  index probably wasn't created.
- **`metadata` is stored as a JSON string, not a typed Tuple.** ClickHouse
  has rich nested types, but chunkshop normalizes metadata as a JSON-encoded
  String for cross-backend portability. If you query metadata raw, you'll
  need `JSONExtractString(metadata, 'key')`.
- **`tags` is `Array(String)` — native, not JSON.** Unlike `metadata`, the
  tags column uses CH's first-class array type, so `arrayJoin(tags)` and
  similar work as expected.
- **The Rust `ClickhouseBackend` is inherent-only**, not behind chunkshop's
  `BackendConn` trait. R4 scoped it that way before R2's GAT lift made the
  trait shape work for CH; lifting it to the trait surface is a v0.4.1
  follow-up. End users don't notice; trait-surface contributors do.

## When to use ClickHouse

- **You already run ClickHouse.** Same operational story; chunks just live
  in a new table.
- **Append-mostly workload.** You ingest a corpus once, query it many times.
  Re-ingest patterns are rare or use `engine: ReplacingMergeTree`.
- **Analytical retrieval.** Vector search joined to `GROUP BY` /
  `quantile()` over millions of rows where CH's columnar engine pays off.
- **Big corpora.** CH scales horizontally; PG and MariaDB don't.
- **DON'T use it for** small workloads where the operational overhead isn't
  worth it (use SQLite or PG), or for write-heavy upsert-driven patterns
  (use PG or MariaDB).

See [`postgres.md`](postgres.md) and [`mariadb.md`](mariadb.md) for the
upsert-friendly alternatives,
[`../mixing-sources-and-sinks.md`](../mixing-sources-and-sinks.md) for moving
data into CH from a transactional source.
