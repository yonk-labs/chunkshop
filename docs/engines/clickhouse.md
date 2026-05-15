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

## Benefits

- **Horizontal scale.** ClickHouse is built for petabyte-scale columnar
  workloads. Chunk tables routinely outgrow what PG / MariaDB can serve
  with low latency; CH handles them with the same data-model layout.
- **Columnar reads.** Querying just `doc_id, original_content` over
  billions of rows is much faster than the equivalent row-store scan.
- **Native `Array(String)` for `tags`.** First-class array column type —
  `arrayJoin(tags)`, `has(tags, 'foo')`, all work without JSON parsing.
- **Analytics-first.** Vector search joined to `GROUP BY` /
  `quantile()` / window functions over millions of rows where columnar
  engines pay off. Retrieval-augmented analytics in one place.
- **Cheap append.** `INSERT` into `MergeTree` is genuinely append-only —
  no row-level locks, no UPSERT lookups. Ingest throughput is higher than
  any of the other 3 backends per unit of CPU.
- **Experimental `vector_similarity` index.** Available in 24.10+ with a
  server config flag; brings cosine queries from brute-force to indexed.

## Limitations

These are intrinsic to ClickHouse, not chunkshop bugs:

- **No row-level UPSERT.** Re-running an `append` cell against the
  default `MergeTree` engine writes **duplicate rows**. This is
  fundamental to CH's append-only design. **Workaround:** use
  `engine: "ReplacingMergeTree(created_at) ORDER BY (id)"` — duplicates
  collapse at merge time (run `OPTIMIZE TABLE ... FINAL` to force merge
  for tests).
- **Async mutations don't fit per-document atomicity.**
  `ALTER TABLE ... DELETE` is queued, not synchronous. **`delete_orphans:
  true` is therefore a no-op + warn** in chunkshop — the sink emits a
  one-time process-level warning when it sees it. For per-doc shrink
  cleanup, use ReplacingMergeTree dedup at merge time instead.
- **24.10+ required for `vector_similarity` index.** Earlier 24.x can
  still ingest and query (cosine falls back to brute-force); the
  experimental index requires server config to enable.
- **`vector_similarity` is experimental.** Behavior and syntax may change
  in future CH releases. If you depend on the index, pin your CH version
  and re-validate on upgrade.
- **`metadata` is stored as a JSON-encoded String, not a typed Tuple.**
  ClickHouse has rich nested types, but chunkshop normalizes metadata as
  String for cross-backend portability. If you query metadata raw, use
  `JSONExtractString(metadata, 'key')`.

## Gaps

Tracked for v0.4.1+:

- **`ClickhouseBackend` doesn't implement the `BackendConn` trait** on the
  Rust side. R4 scoped it as inherent methods before R2's GAT lift made
  the trait shape work for CH. Lifting it to the trait surface is a
  v0.4.1 follow-up (Wave-2 #8). End users don't notice; trait-surface
  contributors and embedders do.
- **Bakeoff CLI in Rust is PG-only.** Rust's `chunkshop-rs bakeoff`
  ignores CH targets. Use `python -m chunkshop.cli bakeoff` for
  multi-backend bakeoffs including ClickHouse. v0.4.1 follow-up.
- **No default to `ReplacingMergeTree`.** chunkshop's default is plain
  `MergeTree`. Many users would actually want dedup-by-default. Will
  consider flipping the default in a future release.

## Troubleshooting

**`Code: 36. DB::Exception: Unknown index type: vector_similarity`**

Your CH server doesn't have the experimental setting enabled. Add this to
`/etc/clickhouse-server/users.d/vector.xml` and restart the server:

```xml
<clickhouse>
  <profiles>
    <default>
      <allow_experimental_vector_similarity_index>1</allow_experimental_vector_similarity_index>
    </default>
  </profiles>
</clickhouse>
```

If you can't change the server config, set `hnsw: false` in your YAML —
chunkshop will skip the CREATE INDEX and queries fall back to brute-force
`cosineDistance`. Slower on big tables, but functional.

**Re-running `mode: append` produced duplicate chunks**

Default `MergeTree` doesn't dedup. Two options:

```yaml
# Option A: opt into lazy dedup
target:
  type: clickhouse
  ...
  engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
```

Then force a merge for tests:

```sql
OPTIMIZE TABLE my_docs.chunks FINAL;
```

```yaml
# Option B: use overwrite mode each run
target:
  type: clickhouse
  ...
  mode: overwrite       # drops + recreates; no duplicate accumulation
```

**Connection succeeds but queries return empty after `INSERT`**

CH's INSERT path is eventually-consistent across replicas. On a
single-node setup the row IS there immediately; on a replicated cluster
with `internal_replication: true`, allow a few hundred ms for replication.
Check `system.parts` to confirm:

```sql
SELECT count() FROM my_docs.chunks;
SELECT * FROM system.parts WHERE database = 'my_docs' AND table = 'chunks';
```

**Query latency much higher than expected**

Probably brute-force `cosineDistance` because the `vector_similarity`
index wasn't created. Check:

```sql
SHOW CREATE TABLE my_docs.chunks;
-- Look for INDEX ... TYPE vector_similarity
```

If the index isn't there, see the first troubleshooting entry.

**`HTTP code 401: Unauthorized`**

Verify your DSN credentials work directly:

```bash
curl -s -u "$USER:$PASS" "$HOST:8123/?query=SELECT+1"
```

The chunkshop CH driver uses HTTP basic auth via clickhouse-connect
(Python) / `clickhouse` crate (Rust). DSN format is
`clickhouse://user:pass@host:8123/database`.

**`Memory limit exceeded` during a large ingest**

CH defaults are conservative. For batch ingest of large corpora, raise
the per-query memory limit:

```sql
SET max_memory_usage = 10000000000;  -- 10 GB
```

Or set it server-wide in `users.d/`.

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
