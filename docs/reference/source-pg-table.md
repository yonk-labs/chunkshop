# `pg_table` source — Postgres table reader with tuple cursor

**Module**: `chunkshop.sources.pg_table`
**Type**: Source (chunkshop core)
**Ship status**: verified
**Optional extra**: none (psycopg ships in core dependencies)
**Since**: extended 2026-05-25 (tuple cursor in commit `ff01268`)

## Purpose

Read rows from an arbitrary Postgres table and yield one chunkshop
`Document` per row. The SP-1 review tightened the incremental-sync
cursor from a single `{after_ts}` key to a tuple
`{after_ts, after_id}` so rows sharing a boundary timestamp aren't
silently dropped (a hard-to-debug correctness bug in the alpha).

## Config schema

`chunkshop.config.PgTableSource` (pydantic v2, `extra="forbid"`):

| Field                | Type        | Default              | Notes |
|----------------------|-------------|----------------------|-------|
| `type`               | `Literal["pg_table"]` | **Required** | Discriminator. |
| `database_name`      | `str`       | **Required** (alias `database`) | Postgres schema (NOT database). |
| `table`              | `str`       | **Required**         | Table name. |
| `id_column`          | `str`       | **Required**         | Column to use as `Document.id`. |
| `content_column`     | `str`       | **Required**         | Column to use as `Document.content`. |
| `title_column`       | `str?`      | `None`               | Column for `Document.title`. |
| `where`              | `str?`      | `None`               | **TRUSTED OPERATOR INPUT** — raw SQL fragment, no parameterization. |
| `updated_at_column`  | `str?`      | `None`               | When set, enables `IncrementalSource` with tuple cursor. |
| `metadata_columns`   | `list[str]` | `[]`                 | Extra columns to fold into `Document.metadata`. |

Inherits DSN resolution from `_DsnResolvable`: `dsn` or `dsn_env`.

## Public API

```python
from chunkshop.sources.pg_table import PgTableSource

class PgTableSource:
    sync_mode = SyncMode.CURSOR  # effective only when updated_at_column is set

    def __init__(self, cfg: PgTableSourceCfg) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...

    # IncrementalSource
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterator[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...
```

## Behavior contract

1. **Sync mode depends on config.** When `updated_at_column` is set,
   `iter_changes_since` returns only rows past the cursor. When unset,
   it falls back to `iter_documents()` (full resync semantics).
2. **Tuple cursor:** `{"after_ts": "<iso-8601 datetime>", "after_id": "<id-as-text>"}`.
   The SQL predicate is
   `WHERE (updated_at, id::text) > (cursor.after_ts, cursor.after_id)`.
   Ordering: `ORDER BY updated_at, id::text`.
3. **`id::text` cast** makes the predicate uniform across `int`/`uuid`/`text`
   ID columns. Sync only requires a CONSISTENT ordering, not a numerically
   correct one — lexicographic-on-text is consistent.
4. **`where` is raw SQL** — not parameterized. Use literal values or
   prepare your own injection-safe input. Same contract as
   `MariaDbTableSource` / `SqliteTableSource` / `ClickhouseTableSource`.
5. **Type coercion via `_json_safe`:** `Decimal` → `float`, datetime/date/time
   → ISO string, `bytes` → base64.
6. **`metadata._updated_at` is stamped automatically** during
   `iter_changes_since` so `cursor_from` can find the row's updated_at
   without re-querying.

## Inputs

- Postgres table with at minimum `id_column` + `content_column`.
- Optional `updated_at_column` of type `timestamp` / `timestamptz` for
  cursor semantics.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | `str(row[id_column])` |
| `content`     | `row[content_column]` (text expected) |
| `title`       | `row[title_column]` if column configured, else `None` |
| `metadata`    | `{<metadata_column>: <_json_safe value>}` + `_updated_at` (incremental path only) |
| `fingerprint` | `None` |

## Errors

| Exception | When |
|-----------|------|
| `pydantic.ValidationError` | Bad config — missing required fields, extra keys. |
| `psycopg.errors.*` | Underlying SQL errors (table missing, column missing, bad `where` SQL). |

## Example: minimal

```yaml
source:
  type: pg_table
  dsn_env: SOURCE_DSN
  database: public
  table: articles
  id_column: id
  content_column: body
```

## Example: realistic with incremental sync

```yaml
cell_name: blog_ingest
source:
  type: pg_table
  dsn_env: SOURCE_DSN
  database: public
  table: blog_posts
  id_column: id
  content_column: body_markdown
  title_column: headline
  updated_at_column: updated_at
  where: "status = 'published'"
  metadata_columns: [author_id, category, published_at, slug]
chunker: {type: hierarchy, prefix_heading: true}
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: blog_kb
  table: chunks
  mode: append
  source_tag: blog_posts
  promote_metadata:
    - {path: author_id, type: text}
    - {path: category, type: text}
    - {path: published_at, type: timestamptz}
```

## How it integrates with the pipeline

`PgTableSource` is loaded by `chunkshop.sources.__init__.load_source`
from the `PgTableSource` config discriminator. It's chunkshop's
reference example of a tuple-cursor `IncrementalSource`.

The boundary-row safety property is documented in the SP-1 review
finding (changelog SHA `ff01268`): when an `updated_at` column has
multiple rows sharing the same boundary timestamp, a single-key cursor
loses all but one. The tuple cursor sorts the tie deterministically
by `id::text` so the next sync resumes mid-batch correctly.

## Tests proving the contract

- `tests/chunkshop/test_pg_table_source.py`:
  - basic iter_documents over a real Postgres table
  - tuple-cursor `iter_changes_since` advances past boundary-row ties
  - cursor merge converges across multi-row syncs
  - `metadata_columns` flow into `Document.metadata`
  - `where` clause filters rows correctly
- Live demo: `python/connectors/examples/e2e_database.py`.

## See also

- [`docs/incremental.md`](../incremental.md) — sync mode taxonomy
- Reference: [`utility-testing`](utility-testing.md) — `merge_cursor`,
  `assert_cursor_advances`, `assert_idempotent_on_re_emit`
- Reference: [`source-http`](source-http.md) — sibling `IncrementalSource`
