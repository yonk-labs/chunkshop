#!/usr/bin/env bash
# setup-sql.sh — load the pg-raggraph sales-crm sample data into chunkshop's
# test DB under a chunkshop-namespaced schema (avoids clobbering AGE testing
# that may have the original sales_demo_app schema loaded elsewhere).
#
# Renames sales_demo_app -> chunkshop_sales_demo before piping to psql, so:
#   - The pg-raggraph cookbook's original schema stays untouched.
#   - chunkshop's pg_table sample reads from a schema clearly labeled
#     as the chunkshop demo's.
#
# Run from repo root:
#   bash docs/samples/sales-crm/setup-sql.sh        # default: small tier
#   bash docs/samples/sales-crm/setup-sql.sh medium # 1000-deal tier

set -euo pipefail

TIER="${1:-small}"
case "$TIER" in
  small|medium) ;;
  *) echo "tier must be 'small' or 'medium', got: $TIER" >&2; exit 2 ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SQL_PATH="$REPO_ROOT/docs/samples/sales-crm/sql/sales-crm-demo-${TIER}.sql.gz"
if [[ ! -f "$SQL_PATH" ]]; then
  echo "SQL file not found: $SQL_PATH" >&2
  echo "(Are you running from a chunkshop checkout? The sample SQL ships" >&2
  echo "gzipped in docs/samples/sales-crm/sql/.)" >&2
  exit 2
fi

DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"
SCHEMA="chunkshop_sales_demo"

echo "==== loading $TIER tier into $SCHEMA ($DSN) ===="

# Drop our chunkshop-namespaced schema if present. Only touches the
# chunkshop_sales_demo namespace — never the original sales_demo_app.
psql "$DSN" -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS $SCHEMA CASCADE"

# Decompress, substitute schema name, and load. The sed substitution
# handles all `sales_demo_app.X` occurrences plus the bare
# `CREATE SCHEMA sales_demo_app` line. gunzip streams; no temp file.
gunzip -c "$SQL_PATH" \
  | sed "s/sales_demo_app/$SCHEMA/g" \
  | psql "$DSN" -v ON_ERROR_STOP=1 --quiet

echo
echo "Loaded. Row counts:"
psql "$DSN" -c "
  SELECT
    'customers'    AS tbl, COUNT(*) FROM $SCHEMA.customers UNION ALL
  SELECT 'products',     COUNT(*) FROM $SCHEMA.products    UNION ALL
  SELECT 'salespeople',  COUNT(*) FROM $SCHEMA.salespeople  UNION ALL
  SELECT 'sales_orders', COUNT(*) FROM $SCHEMA.sales_orders UNION ALL
  SELECT 'sales_notes',  COUNT(*) FROM $SCHEMA.sales_notes
"
