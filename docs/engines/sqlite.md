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

## Benefits

- **Zero server.** No daemon to run, no port to open, no auth to configure.
  The `.db` file IS the database. Move it, copy it, version it, ship it
  in a Docker image, embed it in your app binary.
- **`:memory:` mode.** Point `dsn_env` at a var resolving to `":memory:"`
  for ephemeral in-process tests. Fastest possible test isolation.
- **Tiny operational surface.** ~5 MB total native code (SQLite +
  sqlite-vec). No service to monitor, no backup story beyond `cp file.db`.
- **Same chunkshop pipeline.** Every chunker, embedder, extractor, source,
  and sink mode works. No "SQLite mode" feature gap.
- **Single-file portable archives.** Bake an embedded vector store into
  the artifact you ship — CLI tools, desktop apps, edge agents.

## Limitations

These are intrinsic to SQLite + sqlite-vec, not chunkshop bugs:

- **Brute-force KNN only.** sqlite-vec has no ANN index data structure.
  `hnsw: true` in YAML is **accepted but a no-op** (the sink emits a
  one-time process-level warning when it sees it). Set `hnsw: false` to
  silence the warning. Queries with `MATCH '[…]' AND k = N` still work —
  they just scan every vector. Workable to ~1M chunks; painful beyond.
- **`vec0` virtual table refuses UPSERT.** chunkshop's write path is
  `DELETE FROM chunks_vec WHERE id = ?; INSERT INTO chunks_vec (...) VALUES (...)`
  — two statements per chunk on rewrites. Fine for thousands of chunks;
  less ideal for tens of millions.
- **No schema namespace.** `database:` in YAML is accepted for cross-engine
  parity but **ignored at runtime**. Two chunkshop tables can't share a
  `.db` file by "different database" — point them at different files.
- **Single-writer file lock.** SQLite serializes writers via OS file lock.
  Multi-process ingest into the same `.db` file is sequential, not
  parallel. Use one `.db` per cell, or use Postgres / ClickHouse for
  fan-out.
- **`:memory:` is per-connection, not per-process.** A fresh `.connect()`
  to `:memory:` is an empty database — every connection holds its own.
  Don't share across processes.

## Gaps

Tracked for future versions:

- **No connection pool.** Rust uses `rusqlite` directly (no pool); Python
  uses `sqlite3.connect` per write. Both languages open + close per
  document. For single-cell ingest this is invisible (the file is local
  and the open syscall is microseconds); for high-cell-count fan-out you
  might want pooling. Not on the v0.4 roadmap.
- **Bakeoff CLI in Rust is PG-only.** The Rust `chunkshop-rs bakeoff` CLI
  ignores SQLite targets. Use `python -m chunkshop.cli bakeoff` for
  multi-backend bakeoffs including SQLite. v0.4.1 follow-up.

## Troubleshooting

**`ImportError: No module named 'sqlite_vec'`** (Python)

Install the extras: `uv sync --extra sqlite` or `pip install
'chunkshop[sqlite]'`. The `sqlite-vec` package ships pre-built native
binaries for common platforms.

**`Could not load extension`** at runtime

You need to call `conn.enable_load_extension(True)` BEFORE `sqlite_vec.load(conn)`.
chunkshop's sink does this internally; only matters if you query the
`.db` from outside chunkshop's API.

**Concurrent ingest is slower than expected**

If you're running `chunkshop orchestrate --concurrency 4` against
**the same `.db` file**, you're hitting SQLite's writer lock — only one
process can write at a time. Two fixes:

```yaml
# Option A: one file per cell (use cell_name as suffix)
target:
  type: sqlite
  dsn_env: SQLITE_PATH       # set per-cell to /tmp/chunks-{cell}.db
  database: ignored
  table: chunks
```

```bash
# Option B: switch to a server backend for parallel ingest
chunkshop ingest --config cell.yaml  # one at a time, single .db
# or
export CHUNKSHOP_DSN=postgresql://...   # move to PG and fan out
```

**`disk image is malformed` after a crash**

WAL mode (which sqlite-vec uses by default) usually recovers cleanly. If
it doesn't, the `.db` was being written to mid-`fsync`. Recovery:

```bash
sqlite3 broken.db ".recover" | sqlite3 recovered.db
```

For production use cases where this matters, switch to Postgres — SQLite
on networked storage is not a supported deployment topology.

**`:memory:` ingest works, but queries return empty**

Each `connect()` opens a fresh `:memory:` database. If your ingest and
your query are in different processes (or different `sqlite3.connect`
calls without the URI `file::memory:?cache=shared` extension), they don't
share state. Use a real file path for cross-process work.

**`target.hnsw: true` warning is annoying**

You set `hnsw: true` but sqlite-vec has no index. Either flip to
`hnsw: false` (no functional change, warning silenced) or accept the
warning. The warning fires once per process; subsequent cells stay quiet.

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
