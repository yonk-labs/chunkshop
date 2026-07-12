#!/usr/bin/env bash
# 3RD-PARTY E2E tier: drives bento-api's real /v1/ingest orchestration
# (chunkshop-shim -> doc reconstruction -> stele artifact -> pg-raggraph ->
# stele memory) through the published bento-api (:8000), the way a real BFF
# caller does. See README.md for the full chain + isolation rationale.
#
# Never restarts/recreates any container. All writes live under a freshly
# created, dedicated KB namespace + a uniquely-named chunkshop debug table;
# teardown runs on EXIT regardless of outcome.
#
# Invoke from repo root:
#   export LOCAL_ADMIN_PASSWORD=bento-local-dev
#   bash tests/e2e_bento_api/run-all.sh
set -uo pipefail

API=http://localhost:8000
TS="$(date +%s)"
TABLE="test_cs_${TS}_chunks"
DOC1="test_cs_${TS}_e2e_alpha"
DOC2="test_cs_${TS}_e2e_beta"
UPLOAD_PATH="/var/bento/uploads/test_cs_${TS}_e2e.json"
FAIL=0
DXP_KEY=""
KB_ID=""

if ! curl -s -o /dev/null -w '' --max-time 3 "$API/health"; then
  echo "SKIP: bento-api not reachable at $API — skipping tests/e2e_bento_api/run-all.sh"
  exit 0
fi

if [[ "$(curl -s "$API/v1/admin/local-login/status" | jq -r '.enabled // false')" != "true" ]]; then
  echo "SKIP: LOCAL_ADMIN_ENABLED dev bypass is off on this bento-api — no way to"
  echo "      reach an isolated per-KB ingest path without real OAuth creds. See README."
  exit 0
fi

LOCAL_ADMIN_PASSWORD="${LOCAL_ADMIN_PASSWORD:-}"
if [[ -z "$LOCAL_ADMIN_PASSWORD" ]]; then
  echo "SKIP: LOCAL_ADMIN_PASSWORD not set in this shell — skipping (see README)."
  exit 0
fi

check() {
  local label="$1" ok="$2"
  if [[ "$ok" == "0" ]]; then echo "PASS: $label"; else echo "FAIL: $label"; FAIL=1; fi
}

cleanup() {
  if [[ -n "$KB_ID" && -n "$DXP_KEY" ]]; then
    curl -s -o /dev/null -X DELETE "$API/v1/me/kbs/$KB_ID" -H "Authorization: Bearer $DXP_KEY"
    docker exec bento-postgres psql -U bento -d bento -c "
      DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE namespace = 'kb-${KB_ID}');
      DELETE FROM documents WHERE namespace = 'kb-${KB_ID}';
    " >/dev/null 2>&1
  fi
  docker exec bento-postgres psql -U bento -d bento -c "DROP TABLE IF EXISTS public.\"${TABLE}\" CASCADE" >/dev/null 2>&1
  docker exec bento-chunkshop-shim rm -f "$UPLOAD_PATH" >/dev/null 2>&1
  if [[ -n "$DXP_KEY" ]]; then
    key_id="$(curl -s "$API/v1/me/api-keys" -H "Authorization: Bearer $DXP_KEY" \
      | jq -r '.[] | select(.name=="local-admin-login") | .id' | head -1)"
    [[ -n "$key_id" ]] && curl -s -o /dev/null -X DELETE "$API/v1/me/api-keys/$key_id" -H "Authorization: Bearer $DXP_KEY"
  fi
}
trap cleanup EXIT

echo "== e2e_bento_api: table=$TABLE =="

# --- 1. auth: mint a bearer token via the local-admin dev bypass -----------
login_resp="$(curl -s -X POST "$API/v1/admin/local-login" -H "Content-Type: application/json" \
  -d "$(jq -n --arg p "$LOCAL_ADMIN_PASSWORD" '{password: $p}')")"
DXP_KEY="$(echo "$login_resp" | jq -r '.raw_key // empty')"
[[ -n "$DXP_KEY" ]]
check "local-admin login minted a bearer token" "$?"
[[ -n "$DXP_KEY" ]] || exit 1

# --- 2. create an isolated KB so ingest writes never hit the shared/legacy
#        "default" namespace ------------------------------------------------
kb_resp="$(curl -s -X POST "$API/v1/me/kbs" -H "Authorization: Bearer $DXP_KEY" \
  -H "Content-Type: application/json" -d "$(jq -n --arg n "test_cs_${TS}" '{name: $n}')")"
KB_ID="$(echo "$kb_resp" | jq -r '.id // empty')"
embed_model="$(echo "$kb_resp" | jq -r '.embedding_model // empty')"
embed_dim="$(echo "$kb_resp" | jq -r '.embedding_dim // empty')"
[[ -n "$KB_ID" ]]
check "created a throwaway KB (id=$KB_ID, embedder=$embed_model/$embed_dim)" "$?"
[[ -n "$KB_ID" ]] || exit 1

# --- 3. stage a throwaway corpus into the shim's uploads volume ------------
corpus_json="$(jq -n --arg d1 "$DOC1" --arg d2 "$DOC2" '{
  documents: [
    {id: $d1, content: "The recommendation engine cached stale embeddings after a deploy.", title: "e2e smoke doc 1"},
    {id: $d2, content: "The recommendation engine now invalidates the embedding cache on deploy.", title: "e2e smoke doc 2"}
  ]
}')"
tmp_json="$(mktemp)"
echo "$corpus_json" > "$tmp_json"
chmod 644 "$tmp_json"  # docker cp preserves mode; the shim runs as an unprivileged user
docker cp "$tmp_json" "bento-chunkshop-shim:$UPLOAD_PATH" >/dev/null 2>&1
check "staged throwaway corpus into shim uploads volume" "$?"
rm -f "$tmp_json"

# --- 4. drive the real 5-step orchestration through bento-api --------------
# database: public + a uniquely-named table, NOT a custom schema — bento-api's
# _fetch_documents_from_chunks queries `FROM {table}` unqualified, so it
# resolves against the DSN's default search_path (public). A custom schema
# silently reads whatever pre-existing `public.chunks` happens to look like,
# which surfaced as a real "column doc_id does not exist" error while
# building this test — see README "Known gaps".
cell_yaml=$(cat <<YAML
cell_name: e2e_smoke_${TS}
source:
  type: json_corpus
  path: ${UPLOAD_PATH}
chunker:
  type: sentence_aware
embedder:
  type: fastembed
  model_name: ${embed_model:-BAAI/bge-base-en-v1.5}
  dim: ${embed_dim:-768}
target:
  type: postgres
  dsn_env: DATABASE_URL
  database: public
  table: ${TABLE}
  mode: create_if_missing
  hnsw: false
  source_tag: e2e_smoke
YAML
)
ingest_body="$(jq -n --arg yaml "$cell_yaml" --arg corpus "e2e_smoke_${TS}" --arg kb "$KB_ID" \
  '{cell_yaml: $yaml, corpus_name: $corpus, kb_id: $kb}')"
ingest_resp="$(curl -s -X POST "$API/v1/ingest" -H "Authorization: Bearer $DXP_KEY" \
  -H "Content-Type: application/json" -d "$ingest_body")"
echo "ingest response: $ingest_resp"
echo "$ingest_resp" | jq -e '.status == "completed" and .chunks_written == 2 and .documents_scanned == 2 and .memories_created == 2' >/dev/null 2>&1
check "POST /v1/ingest completes full chunkshop->pg-raggraph->stele chain (2/2/2)" "$?"

# --- 5. verify pg-raggraph's public.documents/chunks under this KB's namespace
doc_count="$(docker exec bento-postgres psql -U bento -d bento -tA -c \
  "SELECT count(*) FROM documents WHERE namespace = 'kb-${KB_ID}'" 2>/dev/null)"
[[ "${doc_count:-0}" -eq 2 ]]
check "pg-raggraph public.documents has 2 rows under kb-${KB_ID} (got $doc_count)" "$?"

chunk_count="$(docker exec bento-postgres psql -U bento -d bento -tA -c \
  "SELECT count(*) FROM chunks c JOIN documents d ON c.document_id = d.id WHERE d.namespace = 'kb-${KB_ID}'" 2>/dev/null)"
[[ "${chunk_count:-0}" -ge 2 ]]
check "pg-raggraph public.chunks joins back to those documents (got $chunk_count)" "$?"

echo "== e2e_bento_api: done (exit=$FAIL) =="
exit "$FAIL"
