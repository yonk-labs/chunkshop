#!/usr/bin/env bash
# Brief SC-010 — runs the if_oversize demo from both Python and Rust.
# Verifies:
#   - Without if_oversize: ≥1 chunk has length(embedded_content) > 1500.
#     One WARN line in stderr (per chunker instance).
#   - With if_oversize:    no chunk > 1500.  No WARN.
set -euo pipefail

: "${CHUNKSHOP_DSN:?Set CHUNKSHOP_DSN to a Postgres DSN with pgvector enabled.}"

cd "$(git rev-parse --show-toplevel)"

echo "=== Python: no fallback ==="
warn_count=$(uv --directory python run chunkshop ingest \
    --config docs/samples/if-oversize/no-fallback.yaml 2>&1 \
    | tee /tmp/no-fallback.log \
    | grep -c "emitted oversize chunk" || true)
echo "  WARN lines: $warn_count (expect ≥1)"
oversize_rows=$(psql "$CHUNKSHOP_DSN" -At -c \
    "SELECT count(*) FROM chunkshop_if_oversize_demo.no_fallback WHERE length(embedded_content) > 1500")
echo "  Rows with embedded_content > 1500: $oversize_rows (expect ≥1)"

echo "=== Python: with fallback ==="
warn_count=$(uv --directory python run chunkshop ingest \
    --config docs/samples/if-oversize/with-fallback.yaml 2>&1 \
    | tee /tmp/with-fallback.log \
    | grep -c "emitted oversize chunk" || true)
echo "  WARN lines: $warn_count (expect 0)"
oversize_rows=$(psql "$CHUNKSHOP_DSN" -At -c \
    "SELECT count(*) FROM chunkshop_if_oversize_demo.with_fallback WHERE length(embedded_content) > 1500")
echo "  Rows with embedded_content > 1500: $oversize_rows (expect 0)"

echo "=== Rust: with fallback ==="
RUST_LOG=warn ./rust/target/release/chunkshop-rs ingest \
    --config docs/samples/if-oversize/with-fallback.yaml 2>&1 | tail -10
