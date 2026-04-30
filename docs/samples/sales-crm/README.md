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
| [`from-files.yaml`](from-files.yaml) | Ingest YAML using `files` source against the markdown dump |
| [`setup-sql.sh`](setup-sql.sh) | Loads the `.sql` dump into a chunkshop-namespaced schema |
| [`run-demo.sh`](run-demo.sh) | End-to-end: load → ingest both → compare |

## ⚠️ Schema rename — why we don't load `sales_demo_app` directly

The `.sql` dumps shipped at `pg-raggraph/docs/cookbook/samples/` create a
`sales_demo_app` schema. That schema is also used by the pg-raggraph
AGE testing setup. **`setup-sql.sh` rewrites every reference to
`sales_demo_app` → `chunkshop_sales_demo` before loading**, so chunkshop's
demo data lives in its own namespace and never collides with whatever
AGE testing has loaded elsewhere.

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
