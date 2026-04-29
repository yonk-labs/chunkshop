# Inline (library) mode — runnable Python + Rust samples

These two demos prove the same thing: chunkshop can be embedded in your
service and driven document-by-document, with no YAML-defined source.
Same `sample-inline.yaml`, same `rag.inline_chunks` target table, same
`(doc_id, seq_num)` primary key — vectors are interchangeable across the
two implementations.

## What both demos exercise

1. **Build** a `Pipeline` from `sample-inline.yaml`.
2. **Insert** three sales notes via `ingest_text(doc_id, text, metadata)`.
3. **Update** one with a longer body — verifies upsert and new-chunk insertion.
4. **Update** one with a shorter body — verifies `target.delete_orphans` drops the now-stale chunks within the same write transaction.
5. **Delete** one explicitly via `delete_document(doc_id)` — scoped to the pipeline's `source_tag` so it can't reach across cells.
6. **Inspect** the result by re-querying the chunk table.

Both produce the same per-step chunk counts:

| Step                  | note-001 | note-002 | note-003 |
|-----------------------|----------|----------|----------|
| After initial ingest  |   2      |   3      |   1      |
| After grow            |   4      |   3      |   1      |
| After shrink + orphans cleaned |   1 |   3      |   1      |
| After delete note-002 |   1      |   —      |   1      |

## Prerequisites

- A reachable Postgres with the `vector` extension. The demos default to:
  ```
  postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
  ```
- Set `VECTORS_DB_DSN` before running.
- The first run downloads the int8 BGE-small model (~25 MB, cached after).

## Run the Python demo

```bash
export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd python
uv sync --extra dev --extra extractors
uv run python ../docs/samples/inline-mode/python_demo.py
```

## Run the Rust demo

```bash
export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd docs/samples/inline-mode/rust_demo
cargo run
```

The Rust demo is a separate Cargo project that path-depends on
`../../../../rust/chunkshop`. In a downstream service you'd pin to a
published version of `chunkshop-rs` once it lands on crates.io.

## Inspect results from psql

```bash
psql "$VECTORS_DB_DSN" -c "
  SELECT doc_id, seq_num, account, source,
         left(original_content, 60) || '...' AS preview
  FROM rag.inline_chunks
  ORDER BY doc_id, seq_num
"
```

## Resetting between runs

The demos write into the same target table. Drop the schema before re-running
to start clean:

```bash
psql "$VECTORS_DB_DSN" -c "DROP SCHEMA IF EXISTS rag CASCADE"
```

## What this gives you for your own service

- **Webhook handler** receives a payload → call `pipeline.ingest_text(...)`.
- **Queue consumer** pops a message → call `pipeline.ingest_text(...)`.
- **CRUD app** updates a record → call `pipeline.ingest_text(...)`.
- **CRUD app** deletes a record → call `pipeline.delete_document(...)`.

You bring the change-detection. chunkshop handles the chunk → embed → store
loop with the same YAML you'd run from the CLI for batch corpora.
