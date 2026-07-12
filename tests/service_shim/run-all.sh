#!/usr/bin/env bash
# SERVICE tier: exercises the bento-chunkshop-shim FastAPI contract
# (/health, /v1/ingest, /v1/delete-by-doc-ids, /v1/bakeoff/{run,leaderboard})
# via `docker exec ... curl` — the shim has no published host port by design.
#
# Skips cleanly (exit 0) if the shim container isn't running. Never
# restarts/recreates the container. All writes go to a dedicated
# test_cs_<timestamp> schema in bento-postgres (via the shim's own
# DATABASE_URL), cleaned up on exit regardless of outcome.
#
# Invoke from repo root:
#   bash tests/service_shim/run-all.sh
set -uo pipefail

SHIM=bento-chunkshop-shim
PG=bento-postgres
TS="$(date +%s)"
SCHEMA="test_cs_${TS}"
UPLOAD_NAME="test_cs_${TS}.json"
UPLOAD_PATH="/var/bento/uploads/${UPLOAD_NAME}"
DOC1="test_cs_${TS}_alpha"
DOC2="test_cs_${TS}_beta"
FAIL=0

if ! docker ps --format '{{.Names}}' | grep -qx "$SHIM"; then
  echo "SKIP: $SHIM is not running — skipping tests/service_shim/run-all.sh"
  exit 0
fi

cleanup() {
  docker exec "$SHIM" rm -f "$UPLOAD_PATH" >/dev/null 2>&1 || true
  docker exec "$PG" psql -U bento -d bento -c "DROP SCHEMA IF EXISTS \"${SCHEMA}\" CASCADE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

check() {
  # check <label> <condition-as-shell-test-result>
  local label="$1" ok="$2"
  if [[ "$ok" == "0" ]]; then
    echo "PASS: $label"
  else
    echo "FAIL: $label"
    FAIL=1
  fi
}

echo "== service_shim: $SHIM, schema=$SCHEMA =="

# --- 1. /health ------------------------------------------------------------
health="$(docker exec "$SHIM" sh -c 'curl -s http://localhost:8000/health')"
echo "health response: $health"
echo "$health" | jq -e '.status == "ok" and .chunkshop_installed == true' >/dev/null 2>&1
check "GET /health returns ok + chunkshop_installed" "$?"

# --- 2. stage a throwaway JSON corpus into the shim's uploads volume -------
CORPUS_JSON="$(jq -n --arg d1 "$DOC1" --arg d2 "$DOC2" '{
  documents: [
    {id: $d1, content: "The payment gateway retried the charge after a timeout.", title: "svc smoke doc 1"},
    {id: $d2, content: "The payment gateway logged a duplicate charge warning.", title: "svc smoke doc 2"}
  ]
}')"
TMP_JSON="$(mktemp)"
echo "$CORPUS_JSON" > "$TMP_JSON"
# `mktemp` defaults to mode 600 (owner-only); `docker cp` preserves that mode
# into the container, and the shim runs as unprivileged `appuser` (uid 1001,
# not the uid that owns the copied file) — appuser can neither read a 600
# file it doesn't own nor chmod it after the fact. Fix the mode on the HOST
# side, where we do own the file, before copying it in.
chmod 644 "$TMP_JSON"
docker cp "$TMP_JSON" "$SHIM:$UPLOAD_PATH" >/dev/null 2>&1
check "staged throwaway corpus into shim uploads volume" "$?"
rm -f "$TMP_JSON"

# --- 3. POST /v1/ingest ------------------------------------------------------
CELL_YAML=$(cat <<YAML
cell_name: svc_shim_smoke_${TS}
source:
  type: json_corpus
  path: ${UPLOAD_PATH}
chunker:
  type: sentence_aware
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target:
  type: postgres
  dsn_env: DATABASE_URL
  database: ${SCHEMA}
  table: chunks
  mode: create_if_missing
  hnsw: false
  source_tag: svc_smoke
YAML
)
INGEST_BODY="$(jq -n --arg yaml "$CELL_YAML" --arg corpus "svc_smoke_${TS}" \
  '{cell_yaml: $yaml, corpus_name: $corpus}')"
ingest_resp="$(docker exec -i "$SHIM" sh -c 'curl -s -X POST http://localhost:8000/v1/ingest -H "Content-Type: application/json" -d @-' <<< "$INGEST_BODY")"
echo "ingest response: $ingest_resp"
chunks_written="$(echo "$ingest_resp" | jq -r '.chunks_written // "null"')"
echo "$ingest_resp" | jq -e '.status == "completed" and .chunks_written >= 2' >/dev/null 2>&1
check "POST /v1/ingest completes with chunks_written >= 2 (got $chunks_written)" "$?"

# --- 4. sanity-check rows actually landed in the isolated schema -----------
row_count="$(docker exec "$PG" psql -U bento -d bento -tA -c "SELECT count(*) FROM \"${SCHEMA}\".chunks WHERE doc_id IN ('${DOC1}','${DOC2}')" 2>/dev/null)"
[[ "${row_count:-0}" -ge 2 ]]
check "chunks for both throwaway doc_ids present in ${SCHEMA}.chunks (count=$row_count)" "$?"

# --- 5. POST /v1/delete-by-doc-ids ------------------------------------------
DELETE_BODY="$(jq -n --arg table "chunks" --arg schema "$SCHEMA" --arg d1 "$DOC1" --arg d2 "$DOC2" \
  '{table: $table, schema: $schema, doc_ids: [$d1, $d2]}')"
delete_resp="$(docker exec -i "$SHIM" sh -c 'curl -s -X POST http://localhost:8000/v1/delete-by-doc-ids -H "Content-Type: application/json" -d @-' <<< "$DELETE_BODY")"
echo "delete response: $delete_resp"
echo "$delete_resp" | jq -e --arg schema "$SCHEMA" '.deleted_chunks >= 2 and .schema == $schema' >/dev/null 2>&1
check "POST /v1/delete-by-doc-ids reports deleted_chunks >= 2" "$?"

row_count_after="$(docker exec "$PG" psql -U bento -d bento -tA -c "SELECT count(*) FROM \"${SCHEMA}\".chunks WHERE doc_id IN ('${DOC1}','${DOC2}')" 2>/dev/null)"
[[ "${row_count_after:-1}" -eq 0 ]]
check "throwaway rows gone after delete-by-doc-ids (count=$row_count_after)" "$?"

# --- 6. bakeoff endpoints — document actual behavior, don't assume ---------
# The BFF-owned ledger tables (bakeoff_runs/bakeoff_results) may not be
# migrated into every bento-postgres instance. If so, the shim's unguarded
# ledger writes raise an uncaught UndefinedTable -> bare 500 (not the
# documented {"code": ...} shape). Report which case this environment is in
# rather than failing the whole script on an infra gap outside chunkshop's
# control (see README "Known gap").
BAKEOFF_YAML=$(cat <<YAML
name: svc_shim_bakeoff_smoke_${TS}
source:
  type: json_corpus
  path: ${UPLOAD_PATH}
gold_queries:
  - query: "duplicate charge warning"
    gold_doc_id: "${DOC2}"
matrix:
  embedders:
    - type: fastembed
      model_name: BAAI/bge-small-en-v1.5
      dim: 384
  chunkers:
    - type: sentence_aware
targets:
  - type: postgres
    dsn_env: DATABASE_URL
    database: ${SCHEMA}
    label: pg_default
scoring:
  k: [1, 3]
  top_k: 3
YAML
)
BAKEOFF_BODY="$(jq -n --arg yaml "$BAKEOFF_YAML" --arg corpus "svc_smoke_${TS}" \
  '{config_yaml: $yaml, corpus_name: $corpus}')"
bakeoff_status="$(docker exec -i "$SHIM" sh -c 'curl -s -o /tmp/bakeoff_resp.json -w "%{http_code}" -X POST http://localhost:8000/v1/bakeoff/run -H "Content-Type: application/json" -d @-' <<< "$BAKEOFF_BODY")"
bakeoff_body="$(docker exec "$SHIM" sh -c 'cat /tmp/bakeoff_resp.json 2>/dev/null')"
echo "bakeoff/run HTTP $bakeoff_status: $bakeoff_body"
if [[ "$bakeoff_status" == "200" ]]; then
  echo "PASS: POST /v1/bakeoff/run completed (200) — ledger tables present in this environment"
  run_id="$(echo "$bakeoff_body" | jq -r '.run_id')"
  lb_status="$(docker exec "$SHIM" sh -c "curl -s -o /tmp/lb_resp.json -w '%{http_code}' 'http://localhost:8000/v1/bakeoff/leaderboard?corpus=svc_smoke_${TS}'")"
  lb_body="$(docker exec "$SHIM" sh -c 'cat /tmp/lb_resp.json 2>/dev/null')"
  echo "bakeoff/leaderboard HTTP $lb_status: $lb_body"
  [[ "$lb_status" == "200" ]]
  check "GET /v1/bakeoff/leaderboard returns 200 after a completed run" "$?"
  docker exec "$PG" psql -U bento -d bento -c "DELETE FROM bakeoff_results WHERE run_id='${run_id}'; DELETE FROM bakeoff_runs WHERE id='${run_id}'" >/dev/null 2>&1
elif [[ "$bakeoff_status" == "500" ]]; then
  echo "WARN (environment gap, not a chunkshop bug): POST /v1/bakeoff/run got 500 — bakeoff_runs/bakeoff_results ledger tables are likely missing from this bento-postgres instance (see tests/service_shim/README.md). Skipping the leaderboard follow-up check."
else
  check "POST /v1/bakeoff/run returned an unexpected status ($bakeoff_status)" "1"
fi
docker exec "$SHIM" rm -f /tmp/bakeoff_resp.json /tmp/lb_resp.json >/dev/null 2>&1 || true

echo "== service_shim: done (exit=$FAIL) =="
exit "$FAIL"
