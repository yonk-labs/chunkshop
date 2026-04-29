#!/usr/bin/env bash
# add_row.sh — append a single new sales note to the demo source table with
# `updated_at = now()`, so the next watermarked run picks up exactly this row
# (and nothing else).

set -euo pipefail

DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"

psql "$DSN" -v ON_ERROR_STOP=1 <<SQL
INSERT INTO chunkshop_pg_demo.sales_notes (subject, body, author, account_id, updated_at)
VALUES (
  'Massive Dynamic — first call',
  'New lead via web form. They run a research lab and want to ' ||
  'evaluate semantic search over internal memos. ' ||
  'Asked us to ship a 14-day trial environment by end of week.',
  'rep_b', 'massive_dyn', now()
)
RETURNING id, subject, updated_at;
SQL
