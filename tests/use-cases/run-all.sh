#!/usr/bin/env bash
# Run every scenario under tests/use-cases/scenarios/ through `chunkshop ingest`.
#
# Mirror of tests/sub/run-all.sh — same shape so CI can drive both identically:
# - Skips cleanly (exit 0) if $CHUNKSHOP_TEST_DSN is unset — CI matrices without
#   Postgres won't see a red run.
# - Drops the shared schema before the run so each invocation starts clean.
# - On any non-zero exit from a cell, prints that cell's log and exits 1.
#
# Each use-case scenario is self-contained (single config.yaml, mode=overwrite)
# so run order doesn't matter — we just iterate the lexical sort.
#
# Invoke from repo root:
#   bash tests/use-cases/run-all.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${CHUNKSHOP_TEST_DSN:-}" ]]; then
  echo "SKIP: CHUNKSHOP_TEST_DSN not set — skipping tests/use-cases/run-all.sh"
  exit 0
fi

SCHEMA="chunkshop_use_cases"
echo "[use-cases] using schema: $SCHEMA"
echo "[use-cases] DSN: ${CHUNKSHOP_TEST_DSN%@*}@..."

# Clean slate: drop the schema so each run starts from zero.
uv run --project python python - <<'PY'
import os, psycopg
with psycopg.connect(os.environ["CHUNKSHOP_TEST_DSN"]) as conn:
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_use_cases CASCADE")
    conn.commit()
print("[use-cases] dropped schema chunkshop_use_cases")
PY

run_cell() {
  local cfg="$1"
  local label
  label="$(basename "$(dirname "$cfg")")/$(basename "$cfg")"
  local log="/tmp/chunkshop_usecase_run_$(echo "$label" | tr '/.' '__').log"
  echo
  echo "[use-cases] ==> $label"
  # Invoke from repo root so the relative globs inside each config.yaml resolve.
  if uv run --project python chunkshop ingest --config "$cfg" > "$log" 2>&1; then
    echo "[use-cases]     OK ($label)"
  else
    local rc=$?
    echo "[use-cases]     FAILED ($label) exit=$rc"
    echo "---- log: $log ----"
    cat "$log"
    echo "---- end log ----"
    exit 1
  fi
}

mapfile -t CONFIGS < <(
  find tests/use-cases/scenarios -maxdepth 2 -type f \( -name "config.yaml" -o -name "config-*.yaml" \) \
    | sort
)

if [[ ${#CONFIGS[@]} -eq 0 ]]; then
  echo "[use-cases] no scenarios found under tests/use-cases/scenarios/"
  exit 1
fi

echo "[use-cases] found ${#CONFIGS[@]} cell(s):"
printf '  %s\n' "${CONFIGS[@]}"

for cfg in "${CONFIGS[@]}"; do
  run_cell "$cfg"
done

echo
echo "[use-cases] all scenarios ingested successfully"
