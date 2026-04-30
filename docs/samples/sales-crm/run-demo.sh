#!/usr/bin/env bash
# run-demo.sh — runs the full sales-crm demo end-to-end.
#
#   1. Load SQL into chunkshop_sales_demo schema (setup-sql.sh)
#   2. Ingest from pg_table source -> chunkshop_sales_chunks.notes_from_pg
#   3. Ingest from files source    -> chunkshop_sales_chunks.notes_from_files
#   4. Compare row counts + sample chunks
#
# Pre-conditions:
#   - $CHUNKSHOP_TEST_DSN points at a pgvector-enabled Postgres
#   - python/.venv has chunkshop installed
#
# Run from repo root:
#   bash docs/samples/sales-crm/run-demo.sh           # default: small tier
#   bash docs/samples/sales-crm/run-demo.sh medium    # 1000-deal tier

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

TIER="${1:-small}"
DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"
export CHUNKSHOP_TEST_DSN="$DSN"

PY_BIN="$REPO_ROOT/python/.venv/bin/chunkshop"
if [[ ! -x "$PY_BIN" ]]; then
  echo "chunkshop binary missing at $PY_BIN" >&2
  echo "  Run: cd python && uv sync --extra dev" >&2
  exit 2
fi

heading() { echo; echo "==== $* ===="; }

heading "step 1: load SQL into chunkshop_sales_demo (tier=$TIER)"
bash "$REPO_ROOT/docs/samples/sales-crm/setup-sql.sh" "$TIER"

heading "step 2: extract notes archive (if not already extracted)"
NOTES_DIR="$REPO_ROOT/docs/samples/sales-crm/notes"
NOTES_TGZ="$REPO_ROOT/docs/samples/sales-crm/notes.tar.gz"
if [[ -d "$NOTES_DIR" && -n "$(ls -A "$NOTES_DIR" 2>/dev/null)" ]]; then
  echo "  $NOTES_DIR exists with content; skipping extract"
else
  tar xzf "$NOTES_TGZ" -C "$REPO_ROOT/docs/samples/sales-crm/"
  echo "  extracted $(ls "$NOTES_DIR" | wc -l) notes from $NOTES_TGZ"
fi

heading "step 3: ingest via pg_table source"
psql "$DSN" -c "DROP SCHEMA IF EXISTS chunkshop_sales_chunks CASCADE" >/dev/null
"$PY_BIN" ingest --config docs/samples/sales-crm/from-pg-table.yaml

heading "step 4: ingest via files source"
"$PY_BIN" ingest --config docs/samples/sales-crm/from-files.yaml

heading "step 5: side-by-side comparison"
psql "$DSN" -c "
  WITH pg_stats AS (
    SELECT
      COUNT(*)                    AS chunks,
      COUNT(DISTINCT doc_id)      AS distinct_docs,
      (SELECT vector_dims(embedding)
         FROM chunkshop_sales_chunks.notes_from_pg LIMIT 1) AS dim
    FROM chunkshop_sales_chunks.notes_from_pg
  ),
  file_stats AS (
    SELECT
      COUNT(*)                    AS chunks,
      COUNT(DISTINCT doc_id)      AS distinct_docs,
      (SELECT vector_dims(embedding)
         FROM chunkshop_sales_chunks.notes_from_files LIMIT 1) AS dim
    FROM chunkshop_sales_chunks.notes_from_files
  )
  SELECT 'pg_table' AS source, * FROM pg_stats
  UNION ALL
  SELECT 'files',                * FROM file_stats
"

heading "step 6: sample query — top-5 'price negotiation' across both"
psql "$DSN" -At -c "
  WITH q AS (
    -- Use any chunk that mentions price negotiation as a proxy query vector.
    SELECT embedding AS qv
    FROM chunkshop_sales_chunks.notes_from_pg
    WHERE original_content ILIKE '%price%'
       OR original_content ILIKE '%discount%'
    LIMIT 1
  )
  SELECT 'pg_table:'  || doc_id || ' :: ' || left(original_content, 70)
  FROM chunkshop_sales_chunks.notes_from_pg, q
  ORDER BY embedding <=> qv LIMIT 3
" || true

echo
echo "Done. Both source paths land in chunkshop_sales_chunks (pg vs files)."
echo "Source rows in chunkshop_sales_demo (untouched after ingest)."
