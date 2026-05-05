# P1 — Python ClickHouse Source — Design Spec

**Date:** 2026-05-05
**Status:** Draft (brainstorming complete, pending writing-plans)
**Sub-project:** P1 of [v0.4.0 finishing roadmap](2026-05-05-v4-finishing-roadmap-design.md)
**Branch:** `experimental/v4-py-clickhouse-source`
**Worktree:** `/home/yonk/yonk-tools/chunkshop-py-ch-source`
**Predecessor specs:**
- [`2026-05-05-v4-finishing-roadmap-design.md`](2026-05-05-v4-finishing-roadmap-design.md) — sub-project framing
- [`2026-04-30-v4-modular-backends-design.md`](2026-04-30-v4-modular-backends-design.md) — `Source` Protocol, Backend layer, identifier-safety policy

## 1. Goal

Add a ClickHouse table source — `python/src/chunkshop/sources/clickhouse_table.py` — so chunkshop can read source documents from ClickHouse the same way it already reads from Postgres, MariaDB, and SQLite. This closes OQ4 from the v4 modular-backends design and unblocks expansion of the cross-backend matrix from 12 cells (3 sources × 4 sinks) to the full 16 cells (4 × 4) required by V4-SC-002.

The CH backend (`backends/clickhouse.py`), CH sink (`sinks/clickhouse.py`), DSN parser, identifier quoting, and `CHUNKSHOP_TEST_DSN_CH` test infrastructure all already exist. P1 is purely a new `Source` impl plus its config branch, loader branch, tests, and a sample YAML.

## 2. Non-goals

- Server-side JOIN-via-VIEW support — operator-side feature; reading a CH view is byte-for-byte the same code path as reading a CH table.
- Materialized-view-specific code paths — reads identically to a regular table.
- `ReplacingMergeTree` source-side dedup helpers — operator-side `SELECT … FINAL` via a view if dedup matters.
- Server-side cursors / streaming retrofit for the PG/MariaDB sibling sources.
- Connection pooling, async I/O, retry loops on transient connection failures.
- New `[clickhouse-source]` extra — `[clickhouse]` already brings `clickhouse-connect`.
- DSN-param passthrough for connect timeouts (separate cross-CH concern, not P1).
- Tightening the source-side identifier regex (would need to be done across all four sources at once).
- Driver exception translation into custom exception types.

## 3. Inherited decisions (from v4 modular-backends design)

These are locked. P1 does not re-litigate them.

- `Source` Protocol surface: `iter_documents() -> Iterator[Document]`. `Document = (id, content, title?, metadata?)`.
- Per-document short-lived `Backend.connect()` connections (no pooling).
- Identifier safety: config-level regex on `target` identifiers (already in place); source-side identifiers rely on `backend.quote_ident()` for SQL safety. P1 mirrors sibling sources exactly — no new regex on source-side identifiers.
- `cfg.where` is **trusted operator input** — raw passthrough into SQL, no parameterization. Same contract as `PgTableSource`/`MariaDbTableSource`/`SqliteTableSource`.
- `metadata_columns: list[str]` is built **client-side** by selecting the named columns and constructing a Python dict — the same pattern as the three sibling sources (none of them use SQL-level JSON construction). This resolves OQ4 as a non-issue: the "JSON-merge" framing in the v4 design table assumed server-side JSON; the actual implementation pattern is column-walk + Python-dict-build.
- DSN env var: `CHUNKSHOP_TEST_DSN_CH`. Already wired in `docker-compose.test.yaml` and the existing 12-cell matrix test.

## 4. Architecture

### 4.1 Files touched

**One new source file:**
- `python/src/chunkshop/sources/clickhouse_table.py` — the source implementation (~60 LOC, structurally a copy of `mariadb_table.py` with three substitutions).

**Three modified files:**
- `python/src/chunkshop/config.py` — add `ClickhouseTableSource` pydantic model; add it to the `SourceConfig` discriminated union.
- `python/src/chunkshop/sources/__init__.py` — import the new config + impl, add a branch in `load_source()`.
- `python/tests/chunkshop/test_cross_backend_matrix.py` — extend `SOURCE_KINDS` from 3 to 4 entries, add `_seed_ch()` source helper and a `clickhouse_table` branch in `_build_source()` and the seed/teardown switches. Matrix expands from 12 cells to 16 mechanically.

**Two new test/sample files:**
- `python/tests/chunkshop/test_source_clickhouse.py` — 5 dedicated tests (see §7).
- `docs/samples/sample-clickhouse-source.yaml` — operator-facing sample, analogous to the existing sink-side `sample-clickhouse.yaml`.

### 4.2 Three differences from sibling sources

The new file is structurally `mariadb_table.py` with three substitutions:

1. **No cursor.** `ClickHouseBackend.connect()` yields the `clickhouse-connect` Client directly (documented in `backends/clickhouse.py:6-12`). The source calls `client.query_rows_stream(sql)` rather than `conn.cursor().execute(sql)`.
2. **Streaming iteration.** `query_rows_stream` returns a context manager around a chunked HTTP response, yielding rows one at a time without materializing the full result set. This diverges from PG/MariaDB siblings, which fully buffer results in RAM. The divergence is intentional: ClickHouse tables routinely contain millions of rows in chunkshop's expected use cases, where the same operator pattern on PG/MariaDB would be unusual. SQLite siblings stream naturally (in-process). PG/MariaDB retrofit is a separate, cross-source concern, deliberately out of P1's scope.
3. **Recursive `_json_safe`.** Sibling `_json_safe` helpers handle `Decimal → float`, `datetime/date/time → isoformat`, `bytes → base64` for flat scalars. CH's type system (`Tuple`, `Map`, nested `Array`, `Nullable`, `UUID`, `IPv4Address`/`IPv6Address`) is broader and nests more deeply in practice. The CH `_json_safe` recurses into `list`/`tuple`/`dict` and adds `UUID → str`, `IPv4Address`/`IPv6Address → str`. Tuples become lists (true JSON has no tuple).

Everything else (config field names, identifier quoting via backend, trusted-`where` contract, `metadata_columns` client-side dict-build, `Document` shape) is identical to siblings.

## 5. Components

### 5.1 `ClickhouseTableSource` config model (in `config.py`)

```python
class ClickhouseTableSource(_Base):
    type: Literal["clickhouse_table"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)
```

Mirrors `MariaDbTableSource` field-for-field. Inherits `extra="forbid"` from `_Base`. Added to the `SourceConfig` `Annotated[Union[...], Field(discriminator="type")]`.

### 5.2 `ClickhouseTableSource` runtime class (in `sources/clickhouse_table.py`)

```python
class ClickhouseTableSource:
    def __init__(self, cfg: ClickhouseTableSourceCfg):
        self.cfg = cfg
        self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        # Build column list: id, content, [title?], *metadata_columns
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in cols)
        fq = self.backend.fq_table(self.cfg.database_name, self.cfg.table)
        query = f"SELECT {cols_sql} FROM {fq}"
        if self.cfg.where:
            # `where` is documented as trusted operator input —
            # same contract as PgTableSource / MariaDbTableSource / SqliteTableSource.
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as client:
            with client.query_rows_stream(query) as stream:
                for row in stream:
                    metadata = {
                        self.cfg.metadata_columns[i]: _json_safe(row[meta_start + i])
                        for i in range(len(self.cfg.metadata_columns))
                    }
                    yield Document(
                        id=str(row[0]),
                        content=row[1],
                        title=row[title_idx] if title_idx is not None else None,
                        metadata=metadata if metadata else None,
                    )
```

### 5.3 `_json_safe` (CH variant, ~15 LOC, recursive)

```python
def _json_safe(v: Any) -> Any:
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(v)
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    if isinstance(v, dict):
        return {k: _json_safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v
```

Recursion bottoms out on JSON-native scalars. Tuples become lists.

### 5.4 `load_source()` branch (in `sources/__init__.py`)

```python
from chunkshop.config import ClickhouseTableSource as ChCfg
from chunkshop.sources.clickhouse_table import ClickhouseTableSource

# in load_source():
if isinstance(cfg, ChCfg):
    return ClickhouseTableSource(cfg)
```

Branch order is irrelevant — discriminated union types are mutually exclusive.

## 6. Data flow

```
YAML cell
  └── source: {type: clickhouse_table, dsn_env, database, table, ...}
        │
        ├─ pydantic parse → ClickhouseTableSourceCfg (extra="forbid")
        │
        ├─ load_source(cfg) → ClickhouseTableSource(cfg)
        │     └── self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)
        │
        └─ runner.run_cell calls source.iter_documents()
              │
              ├─ build SELECT (quote_ident'd cols, fq_table)
              ├─ append raw cfg.where if set
              ├─ backend.connect() → clickhouse-connect Client
              ├─ with client.query_rows_stream(sql) as stream:
              │     for row in stream:
              │         metadata = {col: _json_safe(row[i]) for ...}
              │         yield Document(id, content, title?, metadata?)
              │
              └─ Document → chunker → embedder → extractor → sink
```

The `Document` payload is byte-for-byte identical in shape to what the other three DB sources emit. That's what makes the 16-cell matrix pass without any sink-side changes.

## 7. Error handling & identifier safety

### 7.1 Failure modes

| Failure | Surface |
|---|---|
| `cfg.dsn_env` env var unset | `KeyError` from `os.environ` (sibling parity) |
| Bad DSN scheme | `ValueError` from existing `_parse_clickhouse_dsn` |
| Server unreachable / auth failure | Driver `OperationalError` propagates uncaught |
| Table doesn't exist | CH `Code: 60. UNKNOWN_TABLE` propagates |
| Bad column name | CH `Code: 47. UNKNOWN_IDENTIFIER` propagates |
| Malformed `where` | CH parse error propagates (operator-trusted input) |
| `content_column` value is `None` | Downstream surfaces it; we don't pre-check (sibling parity) |
| Non-JSON-safe value slips past `_json_safe` | Sink-side `json.dumps` raises `TypeError` — bug in `_json_safe`, not a runtime guard |
| Stream interrupted mid-iteration | `query_rows_stream` `__exit__` releases the chunked HTTP response automatically |

**We do not translate driver errors.** Same as siblings.

### 7.2 Identifier-safety boundary (three layers)

1. **Config-load regex** — already in place project-wide for *target* identifiers via `field_validator("table", "database_name", "source_tag")` on `TargetConfig`. Source-side identifiers are *not* regex-validated on PG/MariaDB/SQLite today; P1 mirrors that exactly.
2. **`backend.quote_ident()`** — backticks + double-backtick escape on every column and identifier in the SELECT (`backends/clickhouse.py:43-44`).
3. **`backend.fq_table()`** — composes `db.table` from two `quote_ident`'d parts.

### 7.3 Trusted-`where` contract

The new file's `iter_documents` carries the comment from `mariadb_table.py:40` verbatim (with `Mariadb` swapped for `Click`). The `ClickhouseTableSource` config model's `where` field gets a docstring referencing the contract.

CH SQL dialect for date/time predicates differs from PG/MariaDB (e.g. `toDateTime(...)`, `now64()`). We don't translate or validate; the trusted-input contract means the operator's string goes raw to the server. The sample YAML and the new file's docstring will note one example: `where: "created_at > toDateTime('2025-01-01 00:00:00')"`.

## 8. Test strategy

### 8.1 `test_source_clickhouse.py` — 5 tests

All gated on `CHUNKSHOP_TEST_DSN_CH` (skip with clear reason if unset). Each test creates and drops a unique `chunkshop_src_test_*` database to avoid cross-test pollution.

| ID | Test | Asserts |
|---|---|---|
| **P1-T1** | `test_iter_documents_happy_path` | 2 documents returned with correct id/content/metadata; ids round-trip as strings (mirrors `test_source_mariadb.test_sc006_iter_documents`) |
| **P1-T2** | `test_streaming_does_not_materialize` | Seed 2,000 rows; assert first row arrives within a generous wall-clock bound (smoke that streaming is wired); iteration completes; cleanup is clean. Soft test — locks in `query_rows_stream` as the call shape so a regression to `query` would fail |
| **P1-T3** | `test_json_safe_recursive_coercion` | Seed one row whose `metadata_columns` includes `Array(DateTime)`, `Map(String, UUID)`, `Decimal(10,2)`, `Tuple(String, Date)`, `Nullable(IPv4)` (set), `Nullable(IPv4)` (NULL). Assert `json.dumps(doc.metadata)` succeeds and nested values are coerced |
| **P1-T4** | `test_where_clause_trusted_input` | Seed 3 rows with `created_at`; build config with `where: "created_at > toDateTime('2025-06-01 00:00:00')"`; only matching rows returned |
| **P1-T5** | `test_title_column_optional` | Seed 2 rows with a `headline` column; without `title_column`, `Document.title is None`; with `title_column="headline"`, `Document.title == row.headline` |

### 8.2 Cross-backend matrix extension

In `test_cross_backend_matrix.py`:

```python
SOURCE_KINDS = ["pg_table", "mariadb_table", "sqlite_table", "clickhouse_table"]   # was 3
```

Add `_seed_ch()` source helper:

```python
def _seed_ch(dsn_env: str, db: str) -> None:
    be = ClickHouseBackend(dsn_env=dsn_env)
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{db}` SYNC")
        client.command(f"CREATE DATABASE `{db}`")
        client.command(
            f"CREATE TABLE `{db}`.`docs` "
            f"(id String, body String) ENGINE = MergeTree() ORDER BY id"
        )
        client.insert(f"`{db}`.`docs`",
                      [["doc1", "Hello world. This is sentence two. " * 10]],
                      column_names=["id", "body"])
```

Add `clickhouse_table` branch in `_build_source()`:

```python
if src_kind == "clickhouse_table":
    return ClickhouseTableSource(
        type="clickhouse_table", dsn_env=src_dsn_env, database=src_db_name,
        table="docs", id_column="id", content_column="body",
    )
```

Add seed/teardown switches:

```python
elif src_kind == "clickhouse_table":
    _seed_ch("CHUNKSHOP_TEST_DSN_CH", src_db_name)
    src_dsn = "CHUNKSHOP_TEST_DSN_CH"
# ... and in finally:
elif src_kind == "clickhouse_table":
    _drop_ch("CHUNKSHOP_TEST_DSN_CH", src_db_name)
```

`_drop_ch` already exists. Matrix expands from 12 (3×4) to 16 (4×4) parametrized cells automatically.

The CH-source × CH-sink cell uses the same `CHUNKSHOP_TEST_DSN_CH` for both with different database names. Independent CH databases are independent namespaces; if any client-state collision surfaces, that's a backend-level issue surfaced *by* P1, flagged but not fixed inside P1.

### 8.3 Test infrastructure changes

**None.** `docker-compose.test.yaml` already runs `clickhouse:24.10` with the experimental vector index profile. `CHUNKSHOP_TEST_DSN_CH` is the documented env var. `test_backend_clickhouse.py` and `test_sink_clickhouse.py` already use both. No new fixtures, no new docker config, no new dev-deps.

### 8.4 What is not tested (and why)

- `where`-clause SQL injection — trusted-operator contract makes this a non-test by design.
- Driver error translation — we don't translate, so nothing to test.
- Connection-pool behavior — there is no pool.
- View / materialized-view reads — operator-side feature; same code path as table reads. P1-T1 implicitly covers it.
- HNSW index interactions — source side doesn't touch the index.
- `ReplacingMergeTree` dedup behavior — operator-side concern.

## 9. Sample YAML

`docs/samples/sample-clickhouse-source.yaml` — analogous to the existing sink-side `sample-clickhouse.yaml`. Reads from a CH source table, writes to a PG sink (cross-backend cell, demonstrates the new capability).

```yaml
# ClickHouse SOURCE example. Reads source documents from a CH table and
# writes vectors to PG. Requires CH 24.10+ container and Postgres + pgvector.
#
# From the chunkshop repo root:
#   export CHUNKSHOP_DSN_CH=clickhouse://default:chpw@localhost:8124/default
#   export CHUNKSHOP_DSN_PG=postgresql://postgres:postgres@localhost:5434/chunkshop
#   chunkshop ingest --config docs/samples/sample-clickhouse-source.yaml
cell_name: samples_clickhouse_source_demo

source:
  type: clickhouse_table
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_app
  table: documents
  id_column: id
  content_column: body
  title_column: title          # optional
  metadata_columns: [lang, author]
  # `where` is trusted operator input — passed raw into the SELECT.
  # CH SQL dialect example:
  # where: "created_at > toDateTime('2025-01-01 00:00:00')"

chunker:
  type: hierarchy
  prefix_heading: true

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 2
  batch_size: 32

extractor:
  type: none

target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN_PG
  database: chunkshop_samples
  table: docs_from_clickhouse
  mode: overwrite
  source_tag: ch-source-demo
  hnsw: true

runtime:
  omp_num_threads: 2
```

## 10. Success criteria

| ID | Criterion | Verification |
|---|---|---|
| **P1-SC-001** | `ClickhouseTableSource` config model parses valid YAML and rejects typos via `extra="forbid"` | Implicit via P1-T1 (loads parsed config); inherited from `_Base` |
| **P1-SC-002** | `clickhouse_table` source registered in `load_source()` dispatch | `from chunkshop.sources import load_source` returns a `ClickhouseTableSource` instance for the matching cfg; covered transitively by every matrix test |
| **P1-SC-003** | Source emits `Document` objects with the same shape as PG/MariaDB/SQLite siblings | P1-T1, P1-T5 |
| **P1-SC-004** | `metadata_columns` round-trips correctly with CH-native types including nested containers | P1-T3 |
| **P1-SC-005** | Streaming iteration is wired (uses `query_rows_stream`, not `query`) | P1-T2 + code review of the call site |
| **P1-SC-006** | Trusted-`where` contract works with CH SQL dialect | P1-T4 |
| **P1-SC-007** | Cross-backend matrix expands from 12 cells to 16 (4×4) and all 16 pass with all DSNs set | `pytest python/tests/chunkshop/test_cross_backend_matrix.py -v` |
| **P1-SC-008** | Existing 12 cells still pass after `SOURCE_KINDS` extension | Same matrix run; pre-existing rows unchanged |
| **P1-SC-009** | Sample YAML for CH source is valid and operator-runnable | `docs/samples/sample-clickhouse-source.yaml` parses; add a one-line schema-validation assertion if not picked up by `test_end_to_end_samples_corpus.py` |
| **P1-SC-010** | OQ4 from the v4 design is resolved as a documented non-issue (not a code change) | This spec §3 + §4.2 explicitly note: `metadata_columns` is built client-side via column-walk; SQL-level JSON construction was a phantom problem |

## 11. Drift checkpoints

For mission-brief-aware execution. Re-checked at each phase transition during plan execution.

- **DC-1** (after `config.py` change): `from chunkshop.config import ClickhouseTableSource` works; the `SourceConfig` discriminated union accepts `type: clickhouse_table`. Existing config tests still green. *Question: did anything in the discriminated union ordering or validator chain need adjusting?*
- **DC-2** (after `sources/clickhouse_table.py` exists + `load_source()` branch added): a hand-built `ClickhouseTableSource` cfg → `load_source(cfg)` → instance creation works. *Question: is `_json_safe` actually recursive, or did the implementation accidentally collapse to the flat sibling pattern?*
- **DC-3** (after `test_source_clickhouse.py` lands): all 5 tests green against the local CH container. *Question: did P1-T2 (streaming) end up testing what it claims?*
- **DC-4** (after matrix extension): all 16 cells pass with all 4 DSNs set. *Question: the CH-source × CH-sink cell — any client-state collision (same DSN, different databases)? If yes, surface as a backend-level issue, do not fix inside P1.*
- **DC-FINAL**: all 10 P1-SC items verified; sample YAML parses; CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS summary written; OQ4 explicitly closed in the spec doc with a one-line note.

## 12. Constraints (Always / Ask First / Never)

**ALWAYS:**
- Mirror sibling source contract surface (config field names, identifier safety via `quote_ident`, trusted-`where` semantics).
- Use `ClickHouseBackend` for connections and identifier quoting (no direct `clickhouse_connect.get_client()` calls in the source file).
- Make tests skip cleanly when `CHUNKSHOP_TEST_DSN_CH` is unset.

**ASK FIRST:**
- Any change to a sibling source file (`pg_table.py`, `mariadb_table.py`, `sqlite_table.py`) — the trusted-`where` contract being part-shared makes "harmonization" tempting; don't, without explicit approval.
- Any change to `ClickHouseBackend` itself — backend changes have blast radius into the existing CH sink.
- Any deviation from the 5-test scope (P1-T1 through P1-T5).

**NEVER:**
- Add a regex validator on source-side identifiers without doing it across all four sources at once (would create silent inconsistency).
- Translate driver exceptions into custom exception types (breaks sibling parity).
- Add server-side cursor / streaming retrofit to sibling sources as part of P1.
- Add a connection-pool layer.
- Modify the `Source` Protocol shape.

## 13. Out of scope (cross-reference with roadmap §8)

All v4 modular-backends and v0.4.0 finishing-roadmap "out of scope" items remain out of scope. Specifically: no JOIN-via-VIEW source-side helpers, no `FINAL` modifier helpers, no MV-specific code paths, no DSN-param-passthrough for connect timeouts, no async, no pooling, no retry loops.

## 14. References

- [v0.4.0 finishing roadmap](2026-05-05-v4-finishing-roadmap-design.md) — sub-project framing
- [v4 modular-backends design](2026-04-30-v4-modular-backends-design.md) — `Source` Protocol, Backend layer, identifier-safety policy, OQ4
- `python/src/chunkshop/sources/mariadb_table.py` — closest sibling implementation
- `python/src/chunkshop/sources/pg_table.py` — sibling with `psycopg.sql.Identifier` quoting (different driver model)
- `python/src/chunkshop/sources/sqlite_table.py` — sibling with naturally-streaming cursor
- `python/src/chunkshop/backends/clickhouse.py` — backend (already exists)
- `python/src/chunkshop/sinks/clickhouse.py` — sink (already exists)
- `python/tests/chunkshop/test_source_mariadb.py` — happy-path test pattern
- `python/tests/chunkshop/test_cross_backend_matrix.py` — 12-cell matrix to extend
- `docs/samples/sample-clickhouse.yaml` — sink-side sample (template for source-side sample)
- `docker-compose.test.yaml` — CH 24.10 container service definition
- clickhouse-connect docs: https://clickhouse.com/docs/integrations/python
