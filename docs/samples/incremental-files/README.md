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

Run `./run_demo.sh` (needs `$VECTORS_DB_DSN`) to see a 3-run delta walkthrough.
