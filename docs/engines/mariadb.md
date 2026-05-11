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

## Benefits

- **No extension install.** Vector support is in MariaDB 11.7+ out of the
  box. No `CREATE EXTENSION` step, no superuser required.
- **MySQL-family compatibility.** Existing client libraries, ORMs, and
  monitoring tools work. If your stack runs MySQL/MariaDB today, no new
  ops surface.
- **Native typed vector column.** `VECTOR(N)` enforces dim at the schema
  level. A dim mismatch on INSERT fails immediately, not at query time.
- **Native `Array(String)` ... actually, JSON for arrays.** Wait — that's
  in the Limitations list. The benefit here: MariaDB does have a robust
  JSON type with path operators (`JSON_EXTRACT`, `->`, `->>`) for querying
  inside `tags` and `metadata` columns.
- **Transactional + vector together.** Cosine search joined to your
  customers table, all in one ACID transaction.

## Limitations

These are intrinsic to MariaDB — chunkshop can't work around them:

- **11.7+ required.** No vector type or `VEC_*` functions exist on
  earlier versions. Plain MySQL 8 / 9 are not supported (different vector
  story, not yet ported). Workaround: stay on Postgres or SQLite if you
  can't upgrade past MariaDB 10.x.
- **No first-class array type.** chunkshop's `tags text[]` (PG) becomes
  `tags JSON` here. Functionally equivalent through chunkshop's query API,
  but raw `SELECT tags` returns a JSON-string blob, not a typed array.
  Workaround: use `JSON_EXTRACT(tags, '$[*]')` or chunkshop's `query_top_k`.
- **`embedding` writes via `VEC_FromText` interpolation, not parameter
  binding.** MariaDB's driver doesn't ship a typed vector parameter. The
  sink renders vectors as text and server-side-parses them. Values are
  SQL-injection-safe (rendered through Python `repr` / Rust `format!` over
  `Vec<f32>` — no user strings ever touch this code path), just slightly
  less efficient than a typed bind.
- **MariaDB's vector index is build-on-INSERT, not post-ingest.** Unlike
  pgvector HNSW, there's no separate index-build step to tune. `hnsw: true`
  in YAML is accepted but doesn't trigger a CREATE INDEX — the index is
  managed by MariaDB when the `VECTOR(N)` column is queried.

## Gaps

Tracked for v0.4.1+:

- **Rust distance reader uses defensive cascade** — `try_get::<f64>` then
  `try_get::<f32>`. MariaDB's `VEC_DISTANCE_COSINE` return type can be
  pinned once a larger-vector parity test confirms behavior. Wave-2
  follow-up #2. End users don't see this; it's a Rust-side cleanup.
- **`acquire_create_lock` has no paired release guard.** MariaDB's
  `GET_LOCK` is connection-scoped, so the lock auto-releases when the
  tx-bearing connection drops — works in practice for short-lived create
  transactions. Wave-2 follow-up #1: lift to a `LockGuard<'_>` for RAII
  symmetry with the Python `with_create_lock` context manager.
- **Bakeoff CLI is Python-only multi-backend.** Rust's `chunkshop-rs
  bakeoff` is PG-only. Use `python -m chunkshop.cli bakeoff` for
  cross-backend bakeoffs including MariaDB until v0.4.1.

## Troubleshooting

**`ERROR 1064 ... near 'VECTOR(384)'`**

Your MariaDB is older than 11.7. Check with:

```sql
SELECT VERSION();
```

If it reports `10.x` or any MariaDB <11.7, you need to upgrade — there is
no shim or extension that adds `VECTOR` to older versions.

**`Unknown function VEC_DISTANCE_COSINE` on query**

Same root cause as above — pre-11.7 server. Verify
`SELECT @@version_compile_os, VERSION();` and upgrade.

**Connection succeeds but `CREATE TABLE` fails with privilege error**

The user in your DSN needs `CREATE`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`,
plus `INDEX` (for the vector index) on the target database. A minimal grant:

```sql
GRANT ALL PRIVILEGES ON `my_chunks`.* TO 'chunkshop_user'@'%';
FLUSH PRIVILEGES;
```

`ALL` is overkill but easiest; tighten in production.

**`tags` shows up as a string in psql/MySQL Workbench but as a list in
chunkshop**

This is expected — the underlying column type is `JSON`, which MySQL
clients render as a string. chunkshop's read path decodes it. To filter on
tags inline:

```sql
SELECT doc_id FROM my_docs.chunks
WHERE JSON_CONTAINS(tags, '"my_tag"');
```

**`mysql:` vs `mariadb:` DSN scheme**

Both work. chunkshop uses `pymysql` / sqlx-mysql under the hood; both
drivers connect to MariaDB via the MySQL wire protocol regardless of the
scheme name in your DSN.

## Security note: transitive `rsa` Marvin Attack CVE

chunkshop's MariaDB code path uses `sqlx-mysql` (Rust) and `pymysql` (Python).
`sqlx-mysql` pulls in the `rsa` crate transitively for RSA-based MariaDB auth
plugins. As of v0.4.0, `rsa 0.9.10` carries
[RUSTSEC-2023-0071](https://rustsec.org/advisories/RUSTSEC-2023-0071)
(Marvin Attack: potential key recovery through timing sidechannels).
**No upstream fix is available.**

**Risk:** An adversary on the network path between chunkshop and MariaDB,
with the ability to observe many TLS / auth handshakes, could recover RSA
private key bits over time. Requires (a) MariaDB configured to use an
RSA-based auth plugin (`caching_sha2_password` or `sha256_password`), AND
(b) network position to observe handshake timing.

**Mitigation for users on untrusted networks:**

1. Prefer `mysql_native_password` auth, which doesn't use RSA:
   ```sql
   ALTER USER 'chunkshop_user'@'%' IDENTIFIED WITH mysql_native_password BY '...';
   ```
2. Terminate TLS outside chunkshop (e.g., at a sidecar / proxy) so the
   handshake timing is in a sealed context.
3. Watch the `rsa` crate for a Marvin-resistant release; chunkshop will bump
   as soon as one ships.

**This risk does NOT apply to:**
- Local MariaDB connections (loopback).
- MariaDB in a trusted VPC with no adversarial network position.
- Connections using `mysql_native_password` (RSA path is not exercised).

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
