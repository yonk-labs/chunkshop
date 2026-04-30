# v4.0 Modular Backends — Design Spec

**Date:** 2026-04-30
**Status:** Draft (brainstorming complete, pending writing-plans)
**Branch:** `experimental/v4-modular-backends`
**Worktree:** `/home/yonk/yonk-tools/chunkshop-v4`

## 1. Goal

Make chunkshop's storage layer modular so the same YAML-driven cell can target Postgres, MariaDB, or ClickHouse — both as a *source of documents to chunk* and as a *sink for vectors*. Symmetric: cross-backend pipelines (e.g. read source rows from MariaDB, write vectors to PG) are first-class flows.

## 2. Non-Goals

- Replacing or deprecating Postgres support. PG remains the reference backend.
- Cross-backend bakeoff. Bakeoff stays PG-only.
- Migration tooling from 0.3.x → v4.0. Re-ingest is the policy.
- Async I/O. Everything stays sync.
- Connection pooling. Per-document short-lived connections (current PG pattern) is preserved across all backends.
- Backend hot-swap mid-pipeline (e.g. fanout-to-multiple-sinks).
- Rust/Go ports. Python first; ports follow once the abstraction settles.
- Rich HNSW tuning per backend. `target.hnsw: bool` is the only knob for first ship.

## 3. Core Decisions (recap)

| # | Decision | Choice |
|---|---|---|
| D1 | Modularity scope | Symmetric — both source side and sink side. Cross-backend pipelines allowed. |
| D2 | ClickHouse-as-sink semantics | Append-only. `delete_orphans` is no-op (warns). Provenance via natural append + `argMax(created_at)` reader pattern or `ReplacingMergeTree`. |
| D3 | Schema parity philosophy | Loose parity — shared logical model, native types per backend. |
| D4 | Abstraction shape | `backends/` layer for shared infra (connection, dialect helpers, identifier safety). Per-backend `sinks/<name>.py` and `sources/<name>_table.py` own their own SQL. |
| D5 | First-ship target | PG-refactor + MariaDB. ClickHouse design-supports-it but built later. v4.0 lives on a long-running experimental branch; no hard release commitment. |
| D6 | Migration policy | Re-ingest. v4.0 deliberately breaks the PG schema (column renames acceptable for cleanliness). No upgrade script. |
| D7 | YAML field rename | `target.schema` → `target.database`. Discriminator `target.type` is the backend name (`postgres` / `mariadb` / `clickhouse`). |

## 4. Architecture

The pipeline `Source → Chunker → Embedder → Extractor → Sink` is preserved. What changes is that DB-backed sources and the sink consume a `Backend` for connection management, identifier quoting, and dialect helpers.

### 4.1 Module layout

```
python/src/chunkshop/
├── backends/                    # NEW — shared dialect/connection infra
│   ├── __init__.py              #   load_backend(cfg) factory
│   ├── base.py                  #   Backend Protocol + ColSpec/ChunkRow dataclasses
│   ├── postgres.py              #   psycopg-based; absorbs current sink.py's helpers
│   ├── mariadb.py               #   PyMySQL-based; first-ship
│   └── clickhouse.py            #   clickhouse-connect-based; built after MariaDB
├── sinks/                       # NEW directory (was: sink.py file)
│   ├── __init__.py              #   load_sink(cfg)
│   ├── base.py                  #   Sink Protocol
│   ├── pg.py                    #   rewritten from current sink.py
│   ├── mariadb.py               #   first-ship
│   └── clickhouse.py            #   later
├── sources/
│   ├── pg_table.py              # REWRITTEN — uses backends/postgres.py
│   ├── mariadb_table.py         # NEW — first-ship
│   ├── clickhouse_table.py      # NEW — later
│   └── ... (files, etc., unchanged)
├── config.py                    # MODIFIED — discriminated unions get backend variants
├── runner.py                    # MODIFIED — wires load_backend if needed
└── sink.py                      # DELETED
```

### 4.2 The `Backend` Protocol

Backend's job is "everything that MUST be different per backend, including DDL sequencing." Sink's job is "chunkshop-specific data-model semantics" (modes, metadata promotion, `delete_orphans`, source-tag write-once, append-preflight). Sinks call into Backend; they don't reach around it to the driver.

```python
# backends/base.py
from typing import Protocol, Literal, Iterator, Any, ContextManager
from contextlib import contextmanager
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class ColSpec:
    name: str
    type_ddl: str          # backend-specific type fragment
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False

class Backend(Protocol):
    name: Literal["postgres", "mariadb", "clickhouse"]

    # Connection lifecycle
    @contextmanager
    def connect(self) -> Iterator[Any]: ...   # yields driver-native connection

    # Identifier safety
    def quote_ident(self, name: str) -> str: ...
    def fq_table(self, db: str, table: str) -> str: ...

    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str: ...
    def json_type_ddl(self) -> str: ...
    def tags_array_type_ddl(self) -> str: ...
    def text_pk_type_ddl(self) -> str: ...
    def timestamp_now_default_ddl(self) -> str: ...

    # Value literals (returned as parameter-bindable values for the driver)
    def vector_literal(self, arr: np.ndarray) -> Any: ...
    def tags_literal(self, tags: list[str]) -> Any: ...
    def json_literal(self, obj: Any) -> Any: ...

    # JSON path extraction (used by promote_metadata + metadata_columns)
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str: ...

    # Upsert / conflict handling
    supports_upsert: bool                              # CH = False
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str: ...

    # DDL primitives
    def create_database_sql(self, name: str) -> str: ...
        # PG → "CREATE SCHEMA IF NOT EXISTS ..."
        # MariaDB/CH → "CREATE DATABASE IF NOT EXISTS ..."
    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str: ...
    def drop_table_sql(self, fq: str) -> str: ...

    # Composite DDL — backend handles HNSW timing differences
    def emit_chunks_table_ddl(
        self, fq: str, cols: list[ColSpec], hnsw: bool, dim: int, engine: str | None = None,
    ) -> list[str]: ...

    # Introspection
    def table_exists(self, cur, db: str, table: str) -> bool: ...
    def embedding_dim(self, cur, db: str, table: str) -> int | None: ...

    # Concurrent-create serialization (some backends are no-op)
    def with_create_lock(self, cur, key: str) -> ContextManager[None]: ...
```

**Why `emit_chunks_table_ddl` returns a list of statements:** PG creates the table then a separate `CREATE INDEX USING hnsw`. MariaDB embeds the vector index inline in `CREATE TABLE`. ClickHouse does the same with an `INDEX ... TYPE vector_similarity(...)` clause. Hiding the sequencing inside Backend keeps Sink agnostic.

**Why fragments are returned as raw strings rather than psycopg `Composable`:** each backend uses a different driver. The security boundary is `quote_ident` + the existing identifier-validation regex on user-supplied names (table, schema, source_tag, promote_metadata.path). Same allowlist policy as today.

### 4.3 Sink Protocol (per-backend impls own the chunkshop data model)

```python
# sinks/base.py
class Sink(Protocol):
    def __init__(self, cfg: TargetConfig, backend: Backend, embed_dim: int): ...
    def create_table(self) -> None: ...
    def write_document(
        self,
        doc_id: str,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None: ...
    def count_docs(self) -> int: ...
```

The Sink owns:
- Mode dispatch (`overwrite` / `append` / `create_if_missing`).
- `_overwrite_create` foreign-tag safety check.
- `_append_preflight` (table exists, dim matches, source col present, promoted cols present).
- `_ensure_promote_columns` loop.
- `write_document` row composition (the chunkshop-canonical column list).
- `delete_orphans` (per-backend semantics: works on PG/MariaDB; no-op + warning on CH).
- `source` write-once (PG/MariaDB exclude from UPDATE SET; CH gets it for free via append-only).

### 4.4 Sink portability matrix

| Feature | Postgres | MariaDB (≥11.7) | ClickHouse (≥24) |
|---|---|---|---|
| Database/schema | `CREATE SCHEMA` | `CREATE DATABASE` | `CREATE DATABASE` |
| `id` column | `text PRIMARY KEY` | `VARCHAR(255) PRIMARY KEY` | `String` (in `ORDER BY (id)`) |
| `embedding` column | `vector(N)` (pgvector) | `VECTOR(N)` (native) | `Array(Float32)` |
| HNSW index | sep `CREATE INDEX USING hnsw` | inline `VECTOR INDEX` | inline `INDEX … TYPE vector_similarity('hnsw', 'cosineDistance', N)` |
| `metadata` | `jsonb` | `JSON` | `JSON` |
| `tags` | `text[]` | `JSON` (array) | `Array(String)` |
| Upsert | `ON CONFLICT (id) DO UPDATE` | `ON DUPLICATE KEY UPDATE` | append-only |
| JSON path | `metadata->'a'->>'b'` | `JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.a.b'))` | `JSONExtractString(metadata,'a','b')` |
| `delete_orphans` | DELETE in same txn | DELETE in same txn | **no-op + warning** |
| `source` write-once | EXCLUDE from `UPDATE SET` | EXCLUDE from `UPDATE SET` | natural via append-only |
| Concurrent create-table lock | `pg_advisory_xact_lock` | `GET_LOCK` | no-op (CH DDL serialized via Keeper/ZK) |
| Engine clause | n/a | `ENGINE=InnoDB` | `ENGINE=MergeTree() ORDER BY (id)` (or `ReplacingMergeTree(created_at)` if user opts in) |

### 4.5 Source portability matrix

| Feature | Postgres | MariaDB | ClickHouse |
|---|---|---|---|
| Read base table | `SELECT … FROM` | `SELECT … FROM` | `SELECT … FROM` |
| `metadata_columns` JSON-merge | `jsonb_build_object(…)` | `JSON_OBJECT(…)` | `toJSONString(map(…))` (research spike) |
| JOIN-via-VIEW pattern | `CREATE VIEW IF NOT EXISTS` | `CREATE OR REPLACE VIEW` | `CREATE VIEW IF NOT EXISTS` (deferred until CH source built) |
| Streaming cursors | psycopg server-side cursor | PyMySQL `SSCursor` | `clickhouse-connect` `query_rows_stream` |

## 5. YAML config shape

Discriminator approach, mirroring the existing pydantic discriminated-union pattern. `type` is the backend identity.

### Sink (target)

```yaml
target:
  type: postgres            # | mariadb | clickhouse
  dsn_env: PG_DSN
  database: chunkshop       # was "schema" in 0.3.x — renamed
  table: my_chunks
  mode: overwrite           # | append | create_if_missing
  source_tag: my-source
  hnsw: true                # bool today; future dict for backend-specific knobs
  promote_metadata: [...]   # PromoteColumn entries — unchanged
  delete_orphans: true      # honored on PG/MariaDB; ignored+warns on CH
  force_overwrite: false    # PG/MariaDB only
```

CH-specific extension (validation enforces `mode: append` only):

```yaml
target:
  type: clickhouse
  dsn_env: CLICKHOUSE_DSN
  database: chunkshop
  table: my_chunks
  mode: append
  engine: "ReplacingMergeTree(created_at)"   # optional, default "MergeTree() ORDER BY (id)"
  ...
```

### Source (DB-backed)

```yaml
source:
  type: pg_table            # | mariadb_table | clickhouse_table
  dsn_env: PG_DSN
  database: my_app
  table: documents
  doc_id_col: id
  content_col: body
  metadata_columns: [...]   # b374b31 feature, preserved per-backend
```

### Field harmonization (breaking changes from 0.3.x)

| 0.3.x field | v4.0 field | Notes |
|---|---|---|
| `target.type: pgvector` | `target.type: postgres` | Discriminator value renamed |
| `target.schema` | `target.database` | All backends use `database`. PG implements via `CREATE SCHEMA`; MariaDB/CH via `CREATE DATABASE`. |
| `target.overwrite: true` (legacy) | `target.mode: overwrite` | Legacy field already deprecated in 0.3.1; v4.0 removes acceptance entirely. |
| `source.type: pg_table` | unchanged | Already named correctly. |

## 6. Driver picks

| Backend | Driver | Why | Optional? |
|---|---|---|---|
| Postgres | `psycopg[binary]>=3` | Current dep; mature, type-rich, server-side cursor support. | **Required** dep (PG is the reference backend) |
| MariaDB | `PyMySQL>=1.1` | Pure-Python, no system deps. MariaDB and MySQL share wire protocol; vector literals via text (`VEC_FromText('[…]')`). 30-40% slower than `mysqlclient` (C-based) on bulk insert — document `mysqlclient` as a perf upgrade. | Optional via `[mariadb]` extra |
| ClickHouse | `clickhouse-connect>=0.7` | Official, HTTP-based (works through proxies), streaming `query_rows_stream`, type-rich. | Optional via `[clickhouse]` extra |

`pyproject.toml` shape:

```toml
[project]
dependencies = [
  "psycopg[binary]>=3",   # required — PG is reference backend
  # ... existing deps ...
]

[project.optional-dependencies]
mariadb      = ["PyMySQL>=1.1"]
clickhouse   = ["clickhouse-connect>=0.7"]
all-backends = ["chunkshop[mariadb,clickhouse]"]
```

## 7. Migration policy

**Re-ingest.** v4.0 deliberately breaks the 0.3.x PG schema (column renames where they help; field renames in YAML). No `ALTER TABLE` migration script. Documentation in release notes:

> v4.0 introduces a modular backend layer and renames a few YAML fields (`schema` → `database`, `type: pgvector` → `type: postgres`) and one or two column names where they help portability. Existing 0.3.x tables are not auto-migrated. Re-ingest with a v4.0 cell to populate a fresh table. Ingest is fast and reproducible.

## 8. Test strategy outline

(Plan-level details go in writing-plans output.)

- Unit tests per Backend impl (`backends/test_postgres.py`, `backends/test_mariadb.py`) — quoting, type DDL fragments, JSON-path SQL composition, vector literal formatting. No DB needed.
- Integration tests per Sink and Source — talk to a real DB, skip if unreachable (existing pattern). Per-backend env vars: `CHUNKSHOP_TEST_DSN_PG`, `CHUNKSHOP_TEST_DSN_MARIADB`, `CHUNKSHOP_TEST_DSN_CH`.
- Cross-backend smoke test — read MariaDB → write PG, end-to-end. Skipped if either DSN is unset.
- SQL-injection regression tests — already exist for `PromoteColumn._safe_path` etc.; expand to cover the new identifier surfaces (database, table) on each backend.
- `docker-compose.test.yaml` — PG + MariaDB containers for local dev. CI strategy is a writing-plans decision.

## 9. Out of scope (first-ship: PG-refactor + MariaDB)

- ClickHouse impl. Design supports it (Backend Protocol + parity matrices include CH). Files don't exist yet — added when CH gets built.
- ClickHouse JOIN-via-VIEW source equivalent (research spike).
- Cross-backend bakeoff. Factorial bakeoff stays PG-only.
- Rich HNSW tuning per backend. `target.hnsw: bool` is the only knob; backend-specific tuning dicts come later.
- Connection pooling. Per-document short-lived connections preserved across all backends.
- Async I/O.
- Migration scripts from 0.3.x.
- Vector distance function selection. Cosine is hardcoded for first ship.
- Backend hot-swap mid-pipeline (multi-sink fanout, etc.).
- Rust/Go ports.

## 10. Open questions deferred to writing-plans

These are research-flavored; the implementation plan resolves them as work progresses:

| # | Question | Plan resolution |
|---|---|---|
| OQ1 | MariaDB driver pilot | Default `PyMySQL`; revisit if bulk-insert perf is bad |
| OQ2 | MariaDB minimum version | Hard floor 11.7 (when `VECTOR` type landed); error on connect if `SELECT VERSION()` returns lower |
| OQ3 | CH minimum version | 24.x+ (when `vector_similarity` index landed); enforced when CH gets built |
| OQ4 | CH `metadata_columns` JSON output | Research spike: `toJSONString(map(...))` vs named-tuple approach |
| OQ5 | Test infrastructure | `docker-compose.test.yaml` vs env-var-only; CI is plan-level |
| OQ6 | HNSW operator class on PG | Stays hardcoded `vector_cosine_ops` for first ship |
| OQ7 | `created_at` semantics on CH | `DateTime64(6) DEFAULT now64()`; verify ORDER BY interaction |
| OQ8 | Should `psycopg` move to optional? | First ship: keep required (PG is reference). Revisit at v4.x. |

## 11. Branch + worktree

```bash
git worktree add /home/yonk/yonk-tools/chunkshop-v4 \
  -b experimental/v4-modular-backends \
  main
```

The `experimental/` prefix signals "long-running, not promised to ship." The active spec lives in `docs/superpowers/specs/2026-04-30-v4-modular-backends-design.md` (this file) and is committed on `main` so it's part of the project's planning history regardless of whether the experimental branch lands.

## 12. Success criteria (first-ship)

| ID | Criterion | Verification |
|---|---|---|
| SC-001 | Existing PG bakeoff suite (`factorial-int8`) runs unchanged on the new backend layer | `chunkshop orchestrate --config-dir src/chunkshop/configs/factorial-int8/` produces parity vs 0.3.1 |
| SC-002 | All existing PG tests pass after the refactor (with YAML field updates: `schema` → `database`, `pgvector` → `postgres`) | `uv run pytest -q` clean run |
| SC-003 | A MariaDB sink can ingest a sample doc into a chunks table with vector index | New integration test against a MariaDB 11.7+ container |
| SC-004 | A MariaDB source can read source rows and feed the pipeline | New integration test |
| SC-005 | Cross-backend smoke test works: read MariaDB → write PG | New integration test |
| SC-006 | append-mode preflight works on MariaDB (dim mismatch → clear error) | Mirror `test_sink_append_mode.py` for MariaDB |
| SC-007 | overwrite-mode foreign-tag safety works on MariaDB | Mirror existing PG safety test |
| SC-008 | `delete_orphans` works on MariaDB (DELETE in same txn) | Mirror existing PG test |
| SC-009 | HNSW vector index gets created on MariaDB chunks tables | Verify via `SHOW INDEX FROM` query |
| SC-010 | `promote_metadata` works on MariaDB with JSON-path extraction | Integration test |
| SC-011 | SQL-injection regression tests pass on both PG and MariaDB | Identifier validation regex coverage |
| SC-012 | Sample YAMLs in `docs/samples/` updated for v4.0 field names; pass schema validation | `uv run pytest tests/chunkshop/test_end_to_end_samples_corpus.py` |

## 13. Drift checkpoints

(For mission-brief-aware execution. These get re-checked at each phase transition during writing-plans execution.)

- DC-1 (after `backends/` skeleton): Backend Protocol shape matches §4.2; postgres.py absorbs current sink.py helpers without behavior change.
- DC-2 (after sinks/pg.py rewrite): Existing PG tests still pass. SC-001, SC-002 green.
- DC-3 (after sinks/mariadb.py): SC-003, SC-006, SC-007, SC-008, SC-009, SC-010 green.
- DC-4 (after sources/mariadb_table.py): SC-004 green.
- DC-5 (after cross-backend wiring): SC-005 green.
- DC-FINAL: All SC-001..SC-012 verified; spec is internally consistent with delivered code; CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS summary written.

## 14. References

- Existing codebase (paths cited inline in §4.1).
- pgvector docs: https://github.com/pgvector/pgvector
- MariaDB Vector docs: https://mariadb.com/kb/en/vector-overview/ (11.7+)
- ClickHouse vector search: https://clickhouse.com/docs/en/engines/table-engines/mergetree-family/annindexes (24.x+)
- PyMySQL: https://pymysql.readthedocs.io/
- clickhouse-connect: https://clickhouse.com/docs/en/integrations/python
