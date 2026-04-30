# Sales CRM demo — chunkshop on a real OLTP schema, two ways

Demonstrates ingesting a sales-CRM dataset with chunkshop via **two
distinct source paths** that target the same vector schema:

1. **From SQL (pg_table source).** Load the `.sql` dump into Postgres,
   point chunkshop's `pg_table` source at the `sales_notes` table,
   chunk + embed straight from the database.
2. **From files (files source).** Same data, exported as one markdown
   file per note. Point chunkshop's `files` source at the dump
   directory.

Both produce 384-dim vectors in `chunkshop_sales_chunks` — pick whichever
fits your operational story (database-backed OLTP vs document inbox).

## Why this sample exists

Most chunkshop docs use simple corpora (handbook markdown, NTSB reports).
Real-world ingest is messier:

- The data lives in a relational schema with foreign keys and indices
- The same data may be exported as documents for sharing
- You want to filter retrievals by structured metadata (sentiment,
  customer, salesperson) at query time

This sample uses a synthetic-but-realistic CRM dataset (200 won + 100
lost deals, 974 sales-call notes, "small" tier — there's also a
`medium` tier with 1000 deals / 3299 notes) shipped with the
`pg-raggraph` cookbook. Both load paths are verified end-to-end.

## Files

| File | Role |
|---|---|
| [`from-pg-table.yaml`](from-pg-table.yaml) | Ingest YAML using `pg_table` source against `chunkshop_sales_demo.sales_notes` |
| [`from-files.yaml`](from-files.yaml) | Ingest YAML using `files` source against the (extracted) markdown dump |
| [`setup-sql.sh`](setup-sql.sh) | Streams the gzipped SQL dump into a chunkshop-namespaced schema via `gunzip \| sed \| psql` |
| [`run-demo.sh`](run-demo.sh) | End-to-end: load SQL → extract notes → ingest both → compare |
| `sql/sales-crm-demo-{small,medium}.sql.gz` | Compressed schema + data dump (108 KB / 320 KB; ~6× compression) |
| `notes.tar.gz` | 649 sales-note markdown files compressed (~130 KB; ~20× compression). `run-demo.sh` extracts to `notes/` on first run; `notes/` is gitignored. |

## ⚠️ Schema rename — why we don't load `sales_demo_app` directly

The `.sql` dumps (sourced from `pg-raggraph/docs/cookbook/samples/`,
bundled here gzipped) create a `sales_demo_app` schema. That same
schema name is used by pg-raggraph's AGE testing setup. **`setup-sql.sh`
rewrites every reference to `sales_demo_app` → `chunkshop_sales_demo`
before loading**, so chunkshop's demo data lives in its own namespace
and never collides with whatever AGE testing has loaded elsewhere.

If you want to load the schema as-shipped (because you're not running
AGE tests), edit `setup-sql.sh` to skip the `sed` step.

## Run it

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop                 # repo root
cd python && uv sync --extra dev && cd ..

# Default: small tier (~300 deals, ~974 notes). Use 'medium' for the
# 1000-deal / 3299-note tier.
bash docs/samples/sales-crm/run-demo.sh small
```

## Verified output (small tier)

```
==== step 1: load SQL into chunkshop_sales_demo (tier=small) ====
Loaded. Row counts:
     tbl     | count
-------------+-------
 customers   |    78
 products    |    11
 salespeople |     8
 sales_orders|   300
 sales_notes |   974

==== step 2: ingest via pg_table source ====
{
  "cell_name": "sales_crm_from_pg",
  "docs_processed": 974,
  "chunks_written": 1062,
  "wall_seconds": 16.1
}

==== step 3: ingest via files source ====
[heartbeats every 25 docs]
{
  "cell_name": "sales_crm_from_files",
  "docs_processed": 649,
  "chunks_written": 1675,
  "wall_seconds": 24.0
}

==== step 4: side-by-side comparison ====
  source  | chunks | distinct_docs | dim
----------+--------+---------------+-----
 pg_table |   1062 |           974 | 384
 files    |   1675 |           649 | 384
```

## Why the chunk counts differ

The `pg_table` source pulls **all 974 sales-note rows** straight from
the database. Each note becomes one document; chunks roll out at
`max_chars: 1200` (notes are short, so most produce 1 chunk; some longer
notes split — 1062 chunks total).

The `files` source globs **649 markdown files** from the dump
directory. Each file is structurally richer than a raw `note_text`
(it includes a markdown heading, the customer/deal/product/salesperson
metadata table, and "## Notes" + "## Win reason" sections). The
hierarchy chunker honors those sections and produces ~2.6 chunks per
file (1675 chunks total).

The dump was selectively generated — not every database row has a
corresponding markdown file. That's why distinct_docs differ (974 vs
649). For a perfect-parity comparison you'd want the dumps to mirror
the rows exactly; for a "two viable load paths" demo, the difference
is informative.

## Pulling JOINed columns into chunk metadata (the VIEW pattern)

A common question: chunkshop's `pg_table` source reads ONE table at a
time, so how do you get `customer_name` (which lives on the
`customers` table, not `sales_notes`) into your chunk metadata?

**Answer:** define a Postgres VIEW that pre-joins, then point chunkshop
at the view as if it were a table. `setup-sql.sh` ships an example:

```sql
CREATE OR REPLACE VIEW chunkshop_sales_demo.sales_notes_enriched AS
SELECT
  n.note_id, n.note_text, n.note_type, n.sentiment, n.product_name,
  n.use_case, n.created_at, n.order_id,
  o.status              AS deal_status,
  o.total_value         AS deal_value,
  o.actual_close_date   AS deal_closed_at,
  c.company_name        AS customer_name,        -- ← from customers, joined via order_id
  c.industry            AS customer_industry,
  c.hq_country          AS customer_country,
  c.hq_state            AS customer_state,
  sp.name               AS salesperson_name,
  sp.region             AS salesperson_region
FROM chunkshop_sales_demo.sales_notes n
LEFT JOIN chunkshop_sales_demo.sales_orders o ON n.order_id      = o.order_id
LEFT JOIN chunkshop_sales_demo.customers   c ON o.customer_id   = c.customer_id
LEFT JOIN chunkshop_sales_demo.salespeople sp ON n.salesperson_id = sp.salesperson_id;
```

The YAML then targets the view, listing the JOINed columns under
`metadata_columns` and promoting the most-queried ones to typed columns
via `target.promote_metadata`:

```yaml
source:
  type: pg_table
  schema: chunkshop_sales_demo
  table: sales_notes_enriched      # the VIEW, not the raw notes table
  id_column: note_id
  content_column: note_text
  metadata_columns:
    - customer_name                # ← lives on customers, surfaced via the view
    - customer_industry
    - salesperson_name
    - deal_status
    - sentiment
    - product_name

target:
  promote_metadata:
    - { path: customer_name,     type: text }
    - { path: customer_industry, type: text }
    - { path: deal_status,       type: text }
    - { path: salesperson_name,  type: text }
```

After ingest you can filter retrievals by joined columns directly:

```sql
SELECT customer_name, salesperson_name, left(original_content, 60)
FROM chunkshop_sales_chunks.notes_from_pg
WHERE customer_industry = 'Consulting'
  AND deal_status = 'won'
ORDER BY embedding <=> (SELECT embedding FROM ...) LIMIT 5;
```

**Why a view instead of a `joins:` field in the YAML?** Postgres views
are the right place for join logic — they're SQL, and SQL is what
Postgres optimizes. Putting JOIN expressions in YAML would either be a
weak shadow of SQL or a SQL-injection hazard. The view is also reusable
for non-chunkshop callers (analytics, dashboards).

### Composability with the incremental-ingest patterns

The view pattern works with every change-detection pattern in
[`docs/incremental.md`](../../incremental.md), with one important caveat
about updates to JOINed-table columns:

| Incremental pattern | Works with view? | Notes |
|---|---|---|
| **A. Cron + sliding `WHERE`** | ✅ | The view passes `WHERE updated_at > NOW() - interval '15 min'` straight through to the underlying notes table. New notes get picked up. |
| **B. Watermarked cursor** (`run_incremental_watermark.py`) | ✅ | Same as A — wrapper rewrites the WHERE; view substitutes transparently. |
| **C. Staging-file inbox** | N/A | Files-source pattern. |
| **D. CDC → staging table → chunkshop** | ✅ | Point the view at the staging table; rest is identical. |
| **E. Object-storage events** | N/A | Files-source pattern. |
| **F. Inline / library mode** | ✅ | Pipeline.ingest_text driven by your app — view is irrelevant; you push pre-joined docs in. |

**The caveat — updates to JOINed columns:** chunkshop only re-ingests
rows that match the WHERE clause on each run. If a customer renames
(`customers.company_name` changes) but no new `sales_notes` are inserted
for that customer, the watermark patterns won't pick up the change —
existing chunks keep the stale `customer_name`.

Three options for handling it:

1. **Periodic full re-ingest.** Run with `mode: overwrite` and no WHERE
   clause every N hours. Wasteful at scale; fine when JOINed-table data
   changes rarely (the customer table updates monthly, say).
2. **Trigger-based invalidation.** Add a Postgres trigger on `customers`
   that bumps `sales_notes.updated_at` for every related note. The
   watermark pattern then catches the cascade naturally.
3. **CDC on the dependency tables.** Pattern D, but tap `customers` and
   `sales_orders` too. Drives a re-ingest of affected `note_id`s into
   a staging table.

For this sales-crm dataset (notes are append-only; customer/order data
is stable) option 1 plus a low cron cadence is sufficient. For
production OLTP where customers do rename, options 2 or 3 are real
considerations.

## Adapting this to your own data

**You have a Postgres OLTP schema with documents in a column.** Use
`from-pg-table.yaml` as the template. Point `source.dsn_env`,
`source.schema`, `source.table`, `source.id_column`, and
`source.content_column` at your data. Optionally add a `where:` clause
for incremental ingest (see [`docs/incremental.md`](../../incremental.md)).

**You have a directory of markdown / text files.** Use `from-files.yaml`.
Point `source.glob` at your file pattern. The `id_from: stem` setting
makes doc_ids = filenames-without-extension; switch to `id_from: full`
if you want full paths.

**You have both** (e.g. snapshot exports of a database):
- The `pg_table` path is the source of truth at run time.
- The `files` path is good for staging, snapshots, or sharing with
  parties who don't have DB access.
- Both can ingest into the SAME target table with different
  `source_tag` values (use `mode: append` instead of `overwrite`),
  so a downstream consumer queries one schema and gets both sources
  with provenance preserved.

## What to read next

- [`docs/incremental.md`](../../incremental.md) — five hookup patterns
  for keeping the vector store in sync with the source as new sales
  notes arrive.
- [`docs/embedders.md`](../../embedders.md) — try a different embedder
  on this corpus via the BYO YAML pattern.
- [`docs/samples/bakeoff-ntsb/`](../bakeoff-ntsb/) — run a chunker ×
  embedder bakeoff against this corpus by adapting the bakeoff YAML
  to point at `chunkshop_sales_demo.sales_notes` or the file glob.
