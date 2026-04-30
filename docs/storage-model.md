# Storage model — what chunkshop writes to your table

Every row chunkshop writes carries **three text payloads**, not one. Knowing
which one to query for which job is the difference between solid retrieval and
mysterious mismatches.

## The three payloads

```
                     +-----------------------------+
                     |  one row per chunk          |
                     +-----------------------------+
   raw input  ─────► | original_content (text)     | ← grep / fact-match / audit
                     |                             |
                     | embedded_content (text)     | ← what hit the embedder
                     |                             |
                     | embedding (vector(dim))     | ← what vector search uses
                     +-----------------------------+
```

| Column            | Type                | What it holds                                                  | What you query it for                                            |
|-------------------|---------------------|----------------------------------------------------------------|-------------------------------------------------------------------|
| `original_content`| `text NOT NULL`     | The raw chunk body, exactly as the chunker emitted it          | Display in UI, full-text search, fact-check, audit trail          |
| `embedded_content`| `text NOT NULL`     | The text that was passed to the embedder to produce `embedding`| Debug retrieval ("why did this chunk match?"), reproduce vectors  |
| `embedding`       | `vector({dim})`     | The vector itself, pgvector-typed                              | Cosine similarity (`<=>`), HNSW index, top-k                      |

**Both text columns are always populated and always written.** No flag turns
either one off. If you're asking "is the original text stored next to the
vector?" — yes, by default and unconditionally.

## Why two text columns

Several chunkers deliberately make `embedded_content` *different* from
`original_content` to improve retrieval, while keeping the raw chunk available
for downstream display and audit:

| Chunker                | `embedded_content` ≠ `original_content`?                                                                          |
|------------------------|--------------------------------------------------------------------------------------------------------------------|
| `sentence_aware`       | identical                                                                                                          |
| `fixed_overlap`        | identical                                                                                                          |
| `hierarchy`            | **prepends the section heading** to `embedded_content` — so `"Secrets management\n\n<body>"` gets embedded but `"<body>"` is what you'd display |
| `neighbor_expand`      | **joins ±N adjacent chunks** into `embedded_content`; `original_content` stays the single chunk                    |
| `semantic`             | identical                                                                                                          |
| `summary_embed`        | **replaces `embedded_content` with a summary** of the original; `original_content` stays full-fidelity            |
| `hierarchical_summary` | fine rows: identical. coarse rows: `original_content` = concat of grouped chunks, `embedded_content` = summary    |

This is the load-bearing detail that makes "match-summary, return-raw"
patterns work — the summary chunkers embed the gist, but the raw text is right
there in the same row when you fetch the result.

See [`chunkers.md`](chunkers.md) for the per-chunker behavior in detail.

## The full row

```sql
CREATE TABLE {schema}.{table} (
    id                text PRIMARY KEY,        -- "{doc_id}::{seq_num}"
    doc_id            text NOT NULL,
    seq_num           int  NOT NULL,
    original_content  text NOT NULL,           -- raw chunk body
    embedded_content  text NOT NULL,           -- what was embedded
    tags              text[] NOT NULL DEFAULT '{}',
    metadata          jsonb NOT NULL DEFAULT '{}',
    embedding         vector({dim}) NOT NULL,
    source            text,                    -- multi-source provenance
    created_at        timestamptz NOT NULL DEFAULT now()
);
-- + (doc_id, seq_num) btree index
-- + HNSW index on embedding when target.hnsw: true
-- + any promoted-metadata columns (target.promote_metadata)
```

Source: `python/src/chunkshop/sink.py::_create_base_ddl` (Python is the
schema authority — Rust mirrors).

### Field-by-field

- **`id`** — the natural primary key, `"{doc_id}::{seq_num}"`. Pick a stable
  `doc_id` strategy on the source side (filename stem, database PK, URL hash)
  and your re-runs upsert cleanly.
- **`doc_id` + `seq_num`** — split out as their own columns + btree index so
  you can `ORDER BY doc_id, seq_num` to reconstruct a doc, or filter
  `WHERE doc_id = '...'` cheaply.
- **`tags`** — extractor output. `rake_keywords`, `keybert_phrases`,
  `spacy_entities`, etc. Useful for `WHERE 'foo' = ANY(tags)` filters layered
  on top of vector search.
- **`metadata`** — `jsonb`. Chunker-emitted (`heading`, `strategy`,
  `start_word`, `group_id`), framer-emitted (the framer's section path), and
  extractor-emitted keys, merged with **chunker-wins** semantics on
  collisions. `pg_table` source can also lift selected source columns into
  here via `metadata_columns`.
- **`embedding`** — pgvector-typed. The `{dim}` is fixed at table creation
  from the embedder's output dim; `mode: append` cells must match it
  exactly (pre-flight check).
- **`source`** — multi-source provenance. Set from `target.source_tag` on
  insert. `ON CONFLICT` deliberately excludes this column — first writer
  wins forever for a given `(doc_id, seq_num)` pair.
- **`created_at`** — server-side `now()`. Useful for incremental ingest
  watermarks (see [`incremental.md`](incremental.md)).

## Querying the right column for the right job

### Vector search — use `embedding`

```sql
SELECT id, original_content
FROM chunkshop_samples.handbook
ORDER BY embedding <=> '[0.123, ...]'::vector
LIMIT 10;
```

Return `original_content` to the user — that's the clean text. The
`embedded_content` may have a heading prefix or neighbor splice that's just
noise outside the retrieval context.

### Display in UI — use `original_content`

```sql
SELECT original_content, metadata->>'heading' AS section
FROM chunkshop_samples.handbook
WHERE doc_id = 'handbook-engineering';
```

### Debug "why did this chunk match?" — compare both

```sql
SELECT
    length(original_content)              AS orig_len,
    length(embedded_content)              AS embed_len,
    embedded_content                      AS what_the_model_saw,
    embedding <=> '[...]'::vector         AS dist
FROM chunkshop_samples.handbook
ORDER BY dist
LIMIT 5;
```

Useful when retrieval ranks something unexpected high — eyeballing
`embedded_content` reveals heading-prefix tricks, neighbor splices, or
summarizer output that the vector "saw" but the user wouldn't.

### Reproduce a vector — re-embed `embedded_content`

If you ever need to recompute a vector outside chunkshop (different model,
sanity check), re-embed exactly the bytes in `embedded_content`. Re-embedding
`original_content` will give a *different* vector for any chunker that
modifies the embedded text.

## See also

- [`chunkers.md`](chunkers.md) — which chunker produces which divergence between the two text columns
- [`incremental.md`](incremental.md) — how `created_at` powers cursor-based incremental ingest
- [`extractors.md`](extractors.md) — how `tags` and `metadata` get populated
- [`query-clients.md`](query-clients.md) — copy-paste query templates in Python / JS / Rust / Go
- [`tutorial-multi-source.md`](tutorial-multi-source.md) — how `source` + `promote_metadata` enable multi-source tables
