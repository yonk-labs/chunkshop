#!/usr/bin/env bash
# setup_demo.sh — create the fake `chunkshop_pg_demo.sales_notes` source
# table the demo.yaml/run_demo.sh harness reads from. Idempotent.
#
# Requires:
#   $CHUNKSHOP_TEST_DSN (or default localhost test DB)
#
# Drops and recreates the demo schemas, then inserts 4 sales notes with
# varying updated_at timestamps so Pattern A / B can both demonstrate
# windowing on subsequent runs.

set -euo pipefail

DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"

psql "$DSN" -v ON_ERROR_STOP=1 <<'SQL'
DROP SCHEMA IF EXISTS chunkshop_pg_demo CASCADE;
DROP SCHEMA IF EXISTS chunkshop_pg_demo_chunks CASCADE;
DROP SCHEMA IF EXISTS chunkshop_pg_demo_cursor CASCADE;
CREATE SCHEMA chunkshop_pg_demo;

CREATE TABLE chunkshop_pg_demo.sales_notes (
  id          bigserial PRIMARY KEY,
  subject     text NOT NULL,
  body        text NOT NULL,
  author      text,
  account_id  text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Spread updated_at across the last day so a sliding 24h window catches all.
INSERT INTO chunkshop_pg_demo.sales_notes (subject, body, author, account_id, updated_at) VALUES
  ('Acme — kickoff',
   'Met with Acme. They are evaluating us against three competitors. ' ||
   'Main concerns: latency, support SLA, onboarding time. ' ||
   'We promised a follow-up technical deep-dive next Tuesday. ' ||
   'Their CTO wants benchmark numbers ahead of the meeting.',
   'rep_a', 'acme', now() - interval '6 hours'),

  ('Northwind — renewal',
   'Northwind renewed for three years at the enterprise tier. ' ||
   'Champion is Sarah Chen on their data team. ' ||
   'Risk: their procurement team flagged a data-residency clause ' ||
   'we''ll need to negotiate at next renewal.',
   'rep_b', 'northwind', now() - interval '3 hours'),

  ('Globex — discovery',
   'Discovery call. They have a 50-engineer team, currently on a competitor. ' ||
   'Pain point: prep latency before a query is too high. ' ||
   'Demo scheduled for Thursday.',
   'rep_a', 'globex', now() - interval '90 minutes'),

  ('Initech — pricing',
   'Initech pushed back on per-seat pricing. ' ||
   'They want a flat rate at 100 seats. Sending them the volume tier doc. ' ||
   'Decision-maker: Bill Lumbergh.',
   'rep_c', 'initech', now() - interval '20 minutes');

SQL

echo "✓ chunkshop_pg_demo.sales_notes created with $(psql "$DSN" -At -c 'SELECT COUNT(*) FROM chunkshop_pg_demo.sales_notes') rows"
psql "$DSN" -c "SELECT id, subject, account_id, updated_at FROM chunkshop_pg_demo.sales_notes ORDER BY updated_at"
