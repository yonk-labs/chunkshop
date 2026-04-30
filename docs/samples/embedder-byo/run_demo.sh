#!/usr/bin/env bash
# run_demo.sh — verify the BYO embedder path end-to-end from both languages.
#
# Uses `intfloat/e5-small-v2` (mean-pooled, 384-dim) — a real HuggingFace
# embedder that is NOT in chunkshop's hardcoded registry in either Python
# (_INT8_VARIANTS) or Rust (resolve_model_name / user_defined_source). If
# this script PASSes, the YAML-driven HF pointer feature is working: a
# user can adopt a new embedder by editing YAML alone.
#
# Run from repo root:
#   bash docs/samples/embedder-byo/run_demo.sh
#
# Steps:
#   1. Drop the demo schema
#   2. chunkshop ingest (Python) against byo.yaml — must produce 384-dim chunks
#   3. Drop, re-run as chunkshop-rs ingest (Rust) — same outcome
#   4. Print per-language chunk counts + dim from the chunks table
#
# Pre-conditions:
#   - $CHUNKSHOP_TEST_DSN points at a pgvector-enabled Postgres
#   - python/.venv exists (run `cd python && uv sync --extra dev` if not)
#   - rust/target/release/chunkshop-rs exists (run `cargo build --release` if not)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DSN="${CHUNKSHOP_TEST_DSN:-postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg}"
export CHUNKSHOP_TEST_DSN="$DSN"

PY_BIN="$REPO_ROOT/python/.venv/bin/chunkshop"
RUST_BIN="$REPO_ROOT/rust/target/release/chunkshop-rs"

if [[ ! -x "$PY_BIN" ]]; then
  echo "Python chunkshop not found at $PY_BIN" >&2
  echo "  Run: cd python && uv sync --extra dev" >&2
  exit 2
fi
if [[ ! -x "$RUST_BIN" ]]; then
  echo "Rust chunkshop-rs not found at $RUST_BIN" >&2
  echo "  Run: (cd rust && cargo build --release)" >&2
  exit 2
fi

heading() { echo; echo "==== $* ===="; }

drop_demo_schema() {
  psql "$DSN" -c "DROP SCHEMA IF EXISTS chunkshop_byo_demo CASCADE" >/dev/null
}

verify_chunks() {
  local label="$1"
  local n_chunks dim_str
  n_chunks=$(psql "$DSN" -At -c "SELECT COUNT(*) FROM chunkshop_byo_demo.chunks")
  dim_str=$(psql "$DSN" -At -c "SELECT vector_dims(embedding) FROM chunkshop_byo_demo.chunks LIMIT 1")
  echo "  [$label] $n_chunks chunks written; vector dim = $dim_str"
  if [[ "$n_chunks" -lt 1 ]]; then
    echo "  FAIL: expected ≥1 chunks for $label" >&2
    exit 3
  fi
  if [[ "$dim_str" != "384" ]]; then
    echo "  FAIL: expected dim=384, got $dim_str for $label" >&2
    exit 3
  fi
}

heading "step 1: Python ingest (BYO embedder via YAML)"
drop_demo_schema
"$PY_BIN" ingest --config docs/samples/embedder-byo/byo.yaml
verify_chunks "python"

heading "step 2: Rust ingest (same YAML)"
drop_demo_schema
"$RUST_BIN" ingest --config docs/samples/embedder-byo/byo.yaml
verify_chunks "rust"

heading "step 3: cleanup"
drop_demo_schema
echo "  schema dropped"

echo
echo "PASS — both languages successfully ingested via YAML-only BYO embedder."
echo "       Model: byo-demo-minilm-mean (mean-pooled, 384-dim, made-up name"
echo "       pointing at Xenova/all-MiniLM-L6-v2/onnx/model_quantized.onnx — NOT"
echo "       in either registry, so the BYO path is provably what loaded it."
echo "       Exercises Rust's mean-pool branch + Python's BatchLongest fix end-to-end.)"
