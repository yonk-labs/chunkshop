#!/usr/bin/env bash
# run_demo.sh — runs the full incremental-ingest demo end-to-end.
#
#   1. Fresh fake `sales_notes` table with 4 rows (setup_demo.sh)
#   2. First watermarked run — processes all 4 rows
#   3. Re-run with no source changes — should be a no-op
#   4. Insert a 5th row (add_row.sh) — should be the only row processed next time
#   5. Second watermarked run — picks up only the 5th row
#   6. Print final chunk distribution by source doc_id
#
# Requires:
#   - $CHUNKSHOP_TEST_DSN (default localhost test DB)
#   - `uv` and a synced python/.venv (the script invokes `uv run` so it
#     uses chunkshop's installed binary)
#
# Run from repo root:
#   bash docs/samples/incremental-pg-table/run_demo.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"
export CHUNKSHOP_TEST_DSN="$DSN"

DIR="docs/samples/incremental-pg-table"

if [[ ! -x "$REPO_ROOT/python/.venv/bin/chunkshop" ]]; then
  echo "chunkshop binary not found at python/.venv/bin/chunkshop." >&2
  echo "Run: cd python && uv sync --extra dev --extra extractors" >&2
  exit 1
fi

heading() { echo; echo "==== $* ===="; }

heading "step 1: setup fake sales_notes table"
bash "$DIR/setup_demo.sh"

heading "step 2: first watermarked run (epoch -> now, all 4 rows)"
uv run --project python python scripts/run_incremental_watermark.py \
    --source-tag sales_notes_demo \
    --source-dsn-env CHUNKSHOP_TEST_DSN \
    --target-dsn-env CHUNKSHOP_TEST_DSN \
    --source-schema chunkshop_pg_demo \
    --source-table sales_notes \
    --cursor-schema chunkshop_pg_demo_cursor \
    --cursor-table cursor \
    --updated-column updated_at \
    --config "$DIR/demo.yaml" \
    --chunkshop-bin "$REPO_ROOT/python/.venv/bin/chunkshop"

heading "step 3: re-run with no source changes (should be a no-op)"
uv run --project python python scripts/run_incremental_watermark.py \
    --source-tag sales_notes_demo \
    --source-dsn-env CHUNKSHOP_TEST_DSN \
    --target-dsn-env CHUNKSHOP_TEST_DSN \
    --source-schema chunkshop_pg_demo \
    --source-table sales_notes \
    --cursor-schema chunkshop_pg_demo_cursor \
    --cursor-table cursor \
    --updated-column updated_at \
    --config "$DIR/demo.yaml" \
    --chunkshop-bin "$REPO_ROOT/python/.venv/bin/chunkshop"

heading "step 4: insert a 5th row"
bash "$DIR/add_row.sh"

heading "step 5: second watermarked run (should process only the 5th row)"
uv run --project python python scripts/run_incremental_watermark.py \
    --source-tag sales_notes_demo \
    --source-dsn-env CHUNKSHOP_TEST_DSN \
    --target-dsn-env CHUNKSHOP_TEST_DSN \
    --source-schema chunkshop_pg_demo \
    --source-table sales_notes \
    --cursor-schema chunkshop_pg_demo_cursor \
    --cursor-table cursor \
    --updated-column updated_at \
    --config "$DIR/demo.yaml" \
    --chunkshop-bin "$REPO_ROOT/python/.venv/bin/chunkshop"

heading "step 6: final chunk distribution by source doc_id"
psql "$DSN" -c "
  SELECT doc_id, COUNT(*) AS chunks, source
  FROM chunkshop_pg_demo_chunks.notes_chunks
  GROUP BY doc_id, source
  ORDER BY doc_id
"

heading "step 7: cursor state"
psql "$DSN" -c "SELECT * FROM chunkshop_pg_demo_cursor.cursor"
