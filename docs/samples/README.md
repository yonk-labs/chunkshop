# Sample corpus + configs

Four tiny markdown docs plus three runnable configs, for kicking the tires on chunkshop
without hunting for a corpus.

## Contents

| File                              | What it is                                                  |
|-----------------------------------|-------------------------------------------------------------|
| `handbook-intro.md`               | Short doc with two `##` sections.                           |
| `handbook-engineering.md`         | Medium doc with four `##` sections — realistic prose.       |
| `handbook-security.md`            | Doc with `##` sections and one deliberately-small section.  |
| `release-notes.md`                | Headingless prose — exercises the hierarchy fallback path.  |
| `sample.yaml`                     | Default recipe: `hierarchy` + int8 `bge-small`.             |
| `sample-sentence-aware.yaml`      | Alternative: `sentence_aware` + fp32 `bge-small`.           |
| `sample-neighbor-expand.yaml`     | Alternative: `neighbor_expand` wrapping `hierarchy`.        |
| `sample-multi-source.yaml`        | Schema-flex demo: `mode: create_if_missing` + `source_tag` + `promote_metadata`. |

## Run it

From the chunkshop repo root:

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# One cell:
chunkshop ingest --config docs/samples/sample.yaml

# All three recipes in parallel (side-by-side tables in the same schema):
chunkshop orchestrate \
  -c docs/samples/sample.yaml \
  -c docs/samples/sample-sentence-aware.yaml \
  -c docs/samples/sample-neighbor-expand.yaml \
  --concurrency 3
```

Output tables live in schema `chunkshop_samples`:

```sql
SELECT doc_id, seq_num, metadata->>'heading' AS heading, length(embedded_content) AS embed_len
FROM chunkshop_samples.handbook
ORDER BY doc_id, seq_num;
```

All three YAMLs set `overwrite: true` so re-runs are safe; `hnsw: false` because 4 docs
is well under the point where HNSW beats a sequential scan.

## What each recipe demonstrates

### `sample.yaml` — `hierarchy` + int8

Splits on `#`/`##` headings. Each chunk's `embedded_content` is prefixed with the section
heading, so `handbook-security.md`'s "Secrets management" section gets embedded as
`"Secrets management\n\n<body>"`. You'll see one tiny section dropped from
`handbook-security.md` ("See also", ~86 chars < `min_section_chars: 100`).

`release-notes.md` has no real headings, so hierarchy emits one chunk for the whole doc,
prefixed with the document title.

### `sample-sentence-aware.yaml` — paragraph-respecting

Splits on markdown headings first (same as hierarchy), then falls back to paragraph-packing
up to 3000 chars. Unlike hierarchy, this does **not** prepend the heading to the embedded
content. Good baseline for "what does hierarchy's heading prefix actually buy me".

`release-notes.md` (no headings) gets split on blank lines, packed into ≤3000-char chunks.

### `sample-neighbor-expand.yaml` — hierarchy + ±1 context

Runs hierarchy first, then rebuilds each row's `embedded_content` by joining the previous,
current, and next sections. `original_content` stays as the single section (clean for grep),
but the vector sees more context. Useful when retrieval misses answers that span section
boundaries.

### `sample-multi-source.yaml` — schema-flex shape

Same corpus, same chunker, but written via the new `mode: create_if_missing` +
`source_tag: handbook_markdown` + `promote_metadata: [{path: strategy, type: text}]`
target shape. Demonstrates the multi-source fields on a guaranteed-populated promoted
column (`HierarchyChunker` writes `metadata.strategy = "hierarchy"` on every chunk).
Layer a second cell on top with `mode: append` to see two sources in one table —
full walkthrough in [`../tutorial-multi-source.md`](../tutorial-multi-source.md).

## Comparing results

After running all three, query the same search text against each table and compare the
top-k results. A quick approximation using the Postgres CLI:

```sql
-- Replace the vector literal with the embedding of your query string,
-- produced by the same model the cell used (bge-small = 384 dims).
SELECT
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook                 ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS hierarchy_top1,
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook_sentence_aware  ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS sentence_aware_top1,
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook_neighbor_expand ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS neighbor_expand_top1;
```

For a structured bake-off harness, see the factorial configs in
`python/src/chunkshop/configs/factorial-int8/` — they're the same pattern at 12-cell scale
against a real benchmark corpus.
