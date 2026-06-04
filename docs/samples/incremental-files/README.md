# Incremental local files

Point the `files` source at a directory and reprocess only what changed.

- **Cursor:** a JSON file at `source.incremental.cursor_path` mapping each file
  path → `{hash, mtime, size}`. chunkshop writes it only after a fully
  successful run (atomic temp-file + rename), so a crash leaves the prior cursor
  intact and the next run safely re-upserts.
- **Detection:** `detect: hash` (default) compares a sha256 of file bytes —
  reliable across `git checkout`. `detect: mtime` skips unchanged files by
  `(mtime, size)` without reading them (faster, but unreliable on git work-trees).
- **Deletions:** files removed from disk have their chunks pruned (scoped to the
  cell's `source_tag`).
- **Use `id_from: path`** (or `sha1`) with `incremental:`, as this sample does.
  `id_from: stem` derives the doc id from the filename only, so two files with
  the same stem in different directories collide on one doc id — and pruning one
  would drop the other's chunks. `path`/`sha1` are unique per file.
- **Code or prose:** identical behavior — local source code ingests through this
  same source (`type: files` + a code chunker like `symbol_aware`/`code_aware`).

## Quickstart (no database — SQLite)

`sample.yaml` targets Postgres. To try the whole loop with **no database
server**, use a local SQLite file instead.

1. **Install** the SQLite backend (fastembed is included in the base install):

   ```bash
   pip install "chunkshop[sqlite]"     # or: uv add "chunkshop[sqlite]"
   ```

2. **Make a corpus** of a couple of files:

   ```bash
   mkdir -p corpus
   echo "First note about cats."  > corpus/a.md
   echo "Second note about dogs." > corpus/b.md
   ```

3. **Write `cell.yaml`** (SQLite target, incremental on):

   ```yaml
   cell_name: files_incremental
   source:
     type: files
     glob: ./corpus/**/*.md
     id_from: path                 # path or sha1 — not stem — with incremental
     incremental:
       cursor_path: ./.chunkshop/files-cursor.json
       detect: hash
   chunker:
     type: sentence_aware
   embedder:
     type: fastembed
     model_name: BAAI/bge-small-en-v1.5
     dim: 384
   target:
     type: sqlite
     dsn: ./vecs.db
     database: main
     table: notes_chunks
     mode: create_if_missing
     source_tag: files_incremental
     hnsw: false
   ```

4. **First ingest** — both files are chunked and embedded:

   ```bash
   chunkshop ingest --config cell.yaml        # → docs_processed=2
   ```

5. **Re-run with no changes** — nothing is reprocessed:

   ```bash
   chunkshop ingest --config cell.yaml        # → docs_processed=0
   ```

6. **Edit one file, re-run** — only that file is reprocessed:

   ```bash
   echo "First note, now about elephants." > corpus/a.md
   chunkshop ingest --config cell.yaml        # → docs_processed=1
   ```

7. **Delete one file, re-run** — its chunks are pruned from the table:

   ```bash
   rm corpus/b.md
   chunkshop ingest --config cell.yaml        # b.md's rows removed
   ```

8. **Inspect the cursor** — one entry per current file:

   ```bash
   cat ./.chunkshop/files-cursor.json
   ```

The same `cell.yaml` works for source code — swap the glob to `**/*.py` and the
chunker to `symbol_aware` (needs `chunkshop[code]`) or `code_aware` (stdlib).

For the Postgres path, run `./run_demo.sh` (needs `$VECTORS_DB_DSN`) — the same
3-run delta walkthrough against `sample.yaml`.
