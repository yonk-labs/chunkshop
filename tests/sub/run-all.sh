#!/usr/bin/env bash
# Run every scenario under tests/sub/scenarios/ through `chunkshop ingest`.
#
# - Skips cleanly (exit 0) if $CHUNKSHOP_TEST_DSN is unset — CI matrices without
#   Postgres won't see a red run.
# - Drops the shared schema before the run so each invocation starts clean.
# - Runs scenario 07's two cells in a fixed order (config-a then config-b) to
#   exercise the schema-flex append path on an existing table.
# - On any non-zero exit from a cell, prints that cell's log and exits 1.
#
# Invoke from repo root:
#   bash tests/sub/run-all.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${CHUNKSHOP_TEST_DSN:-}" ]]; then
  echo "SKIP: CHUNKSHOP_TEST_DSN not set — skipping tests/sub/run-all.sh"
  exit 0
fi

SCHEMA="chunkshop_sub_scenarios"
echo "[sub] using schema: $SCHEMA"
echo "[sub] DSN: ${CHUNKSHOP_TEST_DSN%@*}@..."

# Clean slate: drop the schema so each run starts from zero.
# We use the `python` venv that chunkshop is installed into (same one `uv run` uses).
uv run --project python python - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["CHUNKSHOP_TEST_DSN"]) as conn:
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_sub_scenarios CASCADE")
    conn.commit()
print("[sub] dropped schema chunkshop_sub_scenarios")
PY

run_cell() {
  local cfg="$1"
  local label
  label="$(basename "$(dirname "$cfg")")/$(basename "$cfg")"
  local log="/tmp/chunkshop_sub_run_$(echo "$label" | tr '/.' '__').log"
  echo
  echo "[sub] ==> $label"
  # Invoke from repo root so the relative globs inside each config.yaml resolve.
  # `--project python` tells uv which project to run (the Python package lives
  # under python/) without changing the working directory — so the relative
  # source.glob in each config keeps resolving against the repo root.
  if uv run --project python chunkshop ingest --config "$cfg" > "$log" 2>&1; then
    echo "[sub]     OK ($label)"
  else
    local rc=$?
    echo "[sub]     FAILED ($label) exit=$rc"
    echo "---- log: $log ----"
    cat "$log"
    echo "---- end log ----"
    exit 1
  fi
}

# Collect every config.yaml / config-*.yaml under scenarios/*/ in sorted order.
# scenario 07 ships two configs — the leading letter (a < b) enforces run order.
mapfile -t CONFIGS < <(
  find tests/sub/scenarios -maxdepth 2 -type f \( -name "config.yaml" -o -name "config-*.yaml" \) \
    | sort
)

if [[ ${#CONFIGS[@]} -eq 0 ]]; then
  echo "[sub] no scenarios found under tests/sub/scenarios/"
  exit 1
fi

echo "[sub] found ${#CONFIGS[@]} cell(s):"
printf '  %s\n' "${CONFIGS[@]}"

for cfg in "${CONFIGS[@]}"; do
  run_cell "$cfg"
done

echo
echo "[sub] all scenarios ingested successfully"
