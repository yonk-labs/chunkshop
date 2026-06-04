#!/usr/bin/env bash
# Demonstrates files-source incremental: ingest a dir, edit + delete a file,
# re-ingest, and show that only the changed file is reprocessed.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p corpus
echo "First note about cats." > corpus/a.md
echo "Second note about dogs." > corpus/b.md

echo "=== run 1: full ingest (expect docs_processed=2) ==="
chunkshop ingest --config sample.yaml

echo "=== run 2: no changes (expect docs_processed=0) ==="
chunkshop ingest --config sample.yaml

echo "=== edit a.md, delete b.md ==="
echo "First note, now about elephants." > corpus/a.md
rm corpus/b.md

echo "=== run 3: 1 changed + 1 deleted (expect docs_processed=1, b.md pruned) ==="
chunkshop ingest --config sample.yaml
echo "cursor:"; cat .chunkshop/files-cursor.json
