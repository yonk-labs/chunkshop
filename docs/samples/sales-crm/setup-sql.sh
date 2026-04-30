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

# Create a denormalized VIEW that pre-joins notes → orders → customers
# and notes → salespeople. chunkshop's pg_table source reads ONE table
# at a time, so when you want columns from JOINED tables (e.g.
# customer.industry, salesperson.region), the canonical pattern is:
# define a Postgres view that does the join in SQL, then point chunkshop
# at the view as if it were a table. The view costs nothing at write
# time; chunkshop pays the join cost once at ingest.
echo
echo "Creating denormalized view sales_notes_enriched (notes → orders → customers, salespeople)..."
psql "$DSN" -v ON_ERROR_STOP=1 -q <<SQL
CREATE OR REPLACE VIEW $SCHEMA.sales_notes_enriched AS
SELECT
  n.note_id,
  n.note_text,
  n.note_type,
  n.sentiment,
  n.product_name,
  n.use_case,
  n.created_at,
  o.order_id,
  o.status              AS deal_status,
  o.total_value         AS deal_value,
  o.actual_close_date   AS deal_closed_at,
  c.company_name        AS customer_name,
  c.industry            AS customer_industry,
  c.hq_country          AS customer_country,
  c.hq_state            AS customer_state,
  sp.name               AS salesperson_name,
  sp.region             AS salesperson_region
FROM $SCHEMA.sales_notes n
LEFT JOIN $SCHEMA.sales_orders o  ON n.order_id        = o.order_id
LEFT JOIN $SCHEMA.customers   c  ON o.customer_id     = c.customer_id
LEFT JOIN $SCHEMA.salespeople sp ON n.salesperson_id  = sp.salesperson_id;
SQL
psql "$DSN" -c "SELECT COUNT(*) AS enriched_view_rows FROM $SCHEMA.sales_notes_enriched"
