#!/usr/bin/env bash
# run_incremental_watermark.sh — Pattern B from docs/incremental.md.
#
# Wraps `chunkshop ingest` with a durable cursor table so each run only sees
# rows updated since the previous successful run. Templates the YAML's WHERE
# clause from the cursor + the source table's current MAX(updated_at).
#
# Usage:
#   ./scripts/run_incremental_watermark.sh \
#     --source-tag sales_notes \
#     --source-dsn-env SALES_DB_DSN \
#     --target-dsn-env VECTORS_DB_DSN \
#     --config /etc/chunkshop/sales_notes_incremental.yaml \
#     --updated-column updated_at \
#     [--source-table sales_notes] \
#     [--cursor-schema chunkshop] [--cursor-table cursor]
#
# The cursor table is created on first run:
#   CREATE TABLE chunkshop.cursor (
#     source_tag   text PRIMARY KEY,
#     last_seen_at timestamptz NOT NULL,
#     updated_at   timestamptz NOT NULL DEFAULT now()
#   );
#
# Concurrency: a single advisory lock per source_tag prevents two wrappers
# from racing on the same cursor row.

set -euo pipefail

SOURCE_TAG=""
SOURCE_DSN_ENV=""
TARGET_DSN_ENV=""
CONFIG=""
UPDATED_COLUMN="updated_at"
SOURCE_TABLE=""
CURSOR_SCHEMA="chunkshop"
CURSOR_TABLE="cursor"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-tag)        SOURCE_TAG="$2"; shift 2 ;;
    --source-dsn-env)    SOURCE_DSN_ENV="$2"; shift 2 ;;
    --target-dsn-env)    TARGET_DSN_ENV="$2"; shift 2 ;;
    --config)            CONFIG="$2"; shift 2 ;;
    --updated-column)    UPDATED_COLUMN="$2"; shift 2 ;;
    --source-table)      SOURCE_TABLE="$2"; shift 2 ;;
    --cursor-schema)     CURSOR_SCHEMA="$2"; shift 2 ;;
    --cursor-table)      CURSOR_TABLE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *)
      echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

for v in SOURCE_TAG SOURCE_DSN_ENV TARGET_DSN_ENV CONFIG; do
  if [[ -z "${!v}" ]]; then
    echo "Missing required flag for $v" >&2
    exit 2
  fi
done

SOURCE_DSN="${!SOURCE_DSN_ENV:-}"
if [[ -z "$SOURCE_DSN" ]]; then
  echo "$SOURCE_DSN_ENV not exported" >&2; exit 2
fi
TARGET_DSN="${!TARGET_DSN_ENV:-}"
if [[ -z "$TARGET_DSN" ]]; then
  echo "$TARGET_DSN_ENV not exported" >&2; exit 2
fi

# Default the source table from the source_tag if not specified.
if [[ -z "$SOURCE_TABLE" ]]; then
  SOURCE_TABLE="$SOURCE_TAG"
fi

# Validate identifiers — same allowlist chunkshop uses internally.
for ident in "$SOURCE_TAG" "$UPDATED_COLUMN" "$SOURCE_TABLE" "$CURSOR_SCHEMA" "$CURSOR_TABLE"; do
  if ! [[ "$ident" =~ ^[a-z_][a-z0-9_]*$ ]]; then
    echo "Identifier '$ident' must match ^[a-z_][a-z0-9_]*$" >&2
    exit 2
  fi
done

CURSOR_FQ="\"$CURSOR_SCHEMA\".\"$CURSOR_TABLE\""

# Create cursor table if missing. Idempotent.
psql "$TARGET_DSN" -v ON_ERROR_STOP=1 -q <<SQL
CREATE SCHEMA IF NOT EXISTS "$CURSOR_SCHEMA";
CREATE TABLE IF NOT EXISTS $CURSOR_FQ (
  source_tag   text PRIMARY KEY,
  last_seen_at timestamptz NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
SQL

# Acquire an advisory lock keyed on the source_tag — prevents two wrappers
# from racing on the same cursor row. Uses pg_try_advisory_lock so we exit
# 0 (with a log line) instead of stacking up if a previous run is still in
# flight. The key is a 64-bit hash of the source_tag.
LOCK_KEY=$(python3 -c "
import hashlib, sys
d = hashlib.blake2b(sys.argv[1].encode(), digest_size=8).digest()
print(int.from_bytes(d, 'big', signed=True))
" "$SOURCE_TAG")

LOCK_OK=$(psql "$TARGET_DSN" -At -c "SELECT pg_try_advisory_lock($LOCK_KEY)")
if [[ "$LOCK_OK" != "t" ]]; then
  echo "[$(date -Is)] another run for source_tag=$SOURCE_TAG is in flight; exiting"
  exit 0
fi
trap "psql '$TARGET_DSN' -c 'SELECT pg_advisory_unlock($LOCK_KEY)' >/dev/null || true" EXIT

# Read last_seen_at (default to epoch on first run).
LAST_SEEN=$(psql "$TARGET_DSN" -At -c "
  SELECT COALESCE(
    (SELECT last_seen_at::text FROM $CURSOR_FQ WHERE source_tag = '$SOURCE_TAG'),
    '1970-01-01 00:00:00+00'
  )
")

# Read source table's current high-water mark.
NEW_HIGH=$(psql "$SOURCE_DSN" -At -c "
  SELECT COALESCE(MAX(\"$UPDATED_COLUMN\"), '1970-01-01 00:00:00+00')::text
  FROM \"$SOURCE_TABLE\"
")

if [[ "$NEW_HIGH" == "$LAST_SEEN" ]]; then
  echo "[$(date -Is)] source_tag=$SOURCE_TAG no new rows since $LAST_SEEN"
  exit 0
fi

WHERE_CLAUSE="\"$UPDATED_COLUMN\" > '$LAST_SEEN' AND \"$UPDATED_COLUMN\" <= '$NEW_HIGH'"
echo "[$(date -Is)] source_tag=$SOURCE_TAG window: ($LAST_SEEN, $NEW_HIGH]"

# Render a temp YAML with the templated WHERE clause. Original config is left
# untouched. We assume the original has either no `where:` line or one that
# we replace verbatim — yq is the right tool but `sed` keeps the wrapper
# dependency-free.
TMP_YAML=$(mktemp --suffix=.yaml)
trap "rm -f '$TMP_YAML'; psql '$TARGET_DSN' -c 'SELECT pg_advisory_unlock($LOCK_KEY)' >/dev/null || true" EXIT

# Replace any existing `  where: "..."` line with the new clause; if absent,
# append it under the source: block. The yaml-aware path is yq:
#   yq -i ".source.where = \"$WHERE_CLAUSE\"" "$TMP_YAML"
# Use yq if available; fall back to sed.
cp "$CONFIG" "$TMP_YAML"
if command -v yq >/dev/null 2>&1; then
  yq -i ".source.where = \"$WHERE_CLAUSE\"" "$TMP_YAML"
else
  if grep -qE '^[[:space:]]+where:' "$TMP_YAML"; then
    # Replace existing where: line
    sed -i -E "s|^([[:space:]]+)where:.*$|\1where: \"$WHERE_CLAUSE\"|" "$TMP_YAML"
  else
    # Insert under source: — assume `  table:` is the line that always exists.
    sed -i -E "/^[[:space:]]+table:.*$/a\\  where: \"$WHERE_CLAUSE\"" "$TMP_YAML"
  fi
fi

# Run chunkshop. Failure aborts before we bump the cursor.
chunkshop ingest --config "$TMP_YAML"

# Bump the cursor only on success.
psql "$TARGET_DSN" -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO $CURSOR_FQ (source_tag, last_seen_at)
VALUES ('$SOURCE_TAG', '$NEW_HIGH')
ON CONFLICT (source_tag) DO UPDATE
SET last_seen_at = EXCLUDED.last_seen_at,
    updated_at   = now();
SQL

echo "[$(date -Is)] source_tag=$SOURCE_TAG advanced cursor to $NEW_HIGH"
