#!/usr/bin/env bash
# Sync the canonical extras + spaCy model so the full pytest suite passes.
# This is the one-shot equivalent of the two-step recipe in CLAUDE.md's
# "Install" section. Idempotent — safe to re-run.
#
# Surfaced by chunkshop#49: under-syncing extras produces ~22 pytest failures
# (missing bs4 / lede / spaCy model) that look like real regressions but aren't.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/python"

uv sync \
  --extra dev \
  --extra extractors \
  --extra nlp \
  --extra all-backends \
  --extra lede \
  --extra lede-spacy \
  --extra code \
  --extra all-parsers

uv run --no-sync python -m spacy download en_core_web_sm

echo "OK: chunkshop dev environment ready. Run tests with:"
echo "  cd python && uv run --no-sync pytest -q"
