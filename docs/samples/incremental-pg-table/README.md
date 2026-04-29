# Incremental ingest from a Postgres source — runnable demo

Pattern A (sliding window) and Pattern B (watermarked cursor) from
[`docs/incremental.md`](../../incremental.md), with a runnable harness that
creates a fake source table, runs the watermarked wrapper twice, inserts a
new row, runs again, and prints the cursor state.

## Files

| File | Role |
|---|---|
| [`sample.yaml`](sample.yaml) | Production-shape reference. Points at `SALES_DB_DSN` / `VECTORS_DB_DSN`. Edit and ship. |
| [`demo.yaml`](demo.yaml) | Runnable variant. Uses `CHUNKSHOP_TEST_DSN` for both source and target so the demo runs in one DB. |
| [`setup_demo.sh`](setup_demo.sh) | Drops + recreates `chunkshop_pg_demo.sales_notes` with 4 rows. |
| [`add_row.sh`](add_row.sh) | Inserts one new row with `updated_at = now()`. |
| [`run_demo.sh`](run_demo.sh) | Orchestrates the full demo end-to-end. |

## Run it

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop                 # repo root
cd python && uv sync --extra dev --extra extractors && cd ..
bash docs/samples/incremental-pg-table/run_demo.sh
```

## What you'll see

```
==== step 1: setup fake sales_notes table ====
✓ chunkshop_pg_demo.sales_notes created with 4 rows

==== step 2: first watermarked run (epoch -> now, all 4 rows) ====
[12:01:32] cell sales_notes_incremental_demo DONE docs=4 chunks=6 wall=0.2s
source_tag=sales_notes_demo window: (1970-01-01T00:00:00+00:00, ...high water...]
source_tag=sales_notes_demo advanced cursor to 2026-04-29T15:41:31...

==== step 3: re-run with no source changes (should be a no-op) ====
source_tag=sales_notes_demo no new rows since 2026-04-29T15:41:31...

==== step 4: insert a 5th row ====
INSERT 0 1

==== step 5: second watermarked run (should process only the 5th row) ====
[12:01:33] cell sales_notes_incremental_demo DONE docs=1 chunks=1 wall=0.1s
source_tag=sales_notes_demo advanced cursor to 2026-04-29T16:01:32...

==== step 6: final chunk distribution by source doc_id ====
 doc_id | chunks |      source
--------+--------+------------------
 1      |      2 | sales_notes_demo
 2      |      2 | sales_notes_demo
 3      |      1 | sales_notes_demo
 4      |      1 | sales_notes_demo
 5      |      1 | sales_notes_demo
```

## What this exercises

- The **`pg_table` source** end-to-end (DSN env, schema/table/id_column/content_column, optional title_column, runtime `where` clause).
- The **`scripts/run_incremental_watermark.py`** wrapper — cursor table creation, advisory-lock contention safety, MAX(updated_at) read, YAML rendering with the windowed WHERE clause via pyyaml, chunkshop invocation, cursor advancement on success.
- **Idempotent re-runs** — step 3 proves no-op when the cursor already covers all rows; the upserts in step 5 don't disturb the chunks step 2 wrote.
- **`source_tag` provenance** — every chunk row in the target table carries `source = 'sales_notes_demo'` so multi-source filtering works.

## Adapting for production

`sample.yaml` is the production shape. Differences from `demo.yaml`:
- Two distinct DSN env vars (`SALES_DB_DSN`, `VECTORS_DB_DSN`) instead of one shared `CHUNKSHOP_TEST_DSN`.
- `mode: append` (strict pre-flight) once you've done a first ingest with `mode: create_if_missing`.
- `extractor: composite` with `lang_detect` + `rake_keywords` enabled (requires `--extra lang --extra extractors`).
- `hnsw: true` for the index.

The watermark wrapper accepts arbitrary YAMLs — it only edits `source.where`. Use it under cron, k8s `CronJob`, Airflow, Prefect, etc. — anything that runs a Python script on a schedule.

## Cleaning up

```bash
psql "$CHUNKSHOP_TEST_DSN" -c "
  DROP SCHEMA IF EXISTS chunkshop_pg_demo CASCADE;
  DROP SCHEMA IF EXISTS chunkshop_pg_demo_chunks CASCADE;
  DROP SCHEMA IF EXISTS chunkshop_pg_demo_cursor CASCADE;
"
```
