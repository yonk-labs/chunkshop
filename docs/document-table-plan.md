# Document Table Plan

Chunkshop's default persisted model is still chunk rows directly. The pipeline
has a `Document` object and every chunk carries `doc_id`. The Python Postgres
sink now has an opt-in companion document table with one row per source
document via `target.documents.enabled: true`; non-Postgres targets and Rust
parity still need to catch up. Until then, non-Postgres Python targets and Rust
configs fail loudly when the document store is enabled.

The deep benchmark work needs document-level summaries, facts, TOC/headings,
metadata, and full-document context. Reconstructing those from repeated chunk
rows is wasteful and makes doc-summary retrieval a second-class path.

## Current State

Current canonical chunk table:

```sql
CREATE TABLE {schema}.{table} (
    id                text PRIMARY KEY,
    doc_id            text NOT NULL,
    seq_num           int NOT NULL,
    original_content  text NOT NULL,
    embedded_content  text NOT NULL,
    tags              text[] NOT NULL DEFAULT '{}',
    metadata          jsonb NOT NULL DEFAULT '{}',
    embedding         vector({dim}) NOT NULL,
    source            text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
```

Python/Postgres can now create a document table when
`target.documents.enabled: true`. It is disabled by default for compatibility.
SQLite has a two-table layout only because vectors live in a `vec0` virtual
table; that is not a document/chunk 1:M model.

## Target Model

Add a first-class document table beside the chunk table:

```text
documents 1 ──── * chunks
```

Current default names:

```text
{schema}.documents
{schema}.{table}
```

Example:

```sql
CREATE TABLE {schema}.documents (
    doc_id              text PRIMARY KEY,
    source              text,
    title               text,
    uri                 text,
    source_path         text,
    content_hash        text,
    full_content        text,
    metadata            jsonb NOT NULL DEFAULT '{}',
    lede_summary        text,
    lede_toc            jsonb NOT NULL DEFAULT '[]',
    lede_facts          jsonb NOT NULL DEFAULT '[]',
    lede_report         jsonb NOT NULL DEFAULT '{}',
    lede_search_text    text,
    char_count          int,
    token_count_est     int,
    chunk_count         int NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
```

The chunk table keeps its current shape, with optional foreign key support on
backends that can enforce it:

```sql
ALTER TABLE {schema}.{table}
  ADD CONSTRAINT {table}_doc_fk
  FOREIGN KEY (doc_id)
  REFERENCES {schema}.documents(doc_id)
  ON DELETE CASCADE;
```

Foreign keys should be optional for cross-backend parity. ClickHouse, MariaDB,
SQLite, and append-only modes have different capabilities and costs.

## Column Policy

### Document Table

Document-level fields:

| Column | Purpose |
|---|---|
| `doc_id` | Stable source document identity. |
| `source` | Same source tag/provenance concept as chunks. |
| `title` | Human display and FTS. |
| `uri` / `source_path` | Link back to source. |
| `content_hash` | Idempotence/change detection. |
| `full_content` | Optional original full document text for doc-level context. |
| `metadata` | Source/framer/extractor metadata that applies to the whole doc. |
| `lede_summary` | Compact document summary for doc-summary profiles. |
| `lede_toc` | Headings/TOC/case sections as structured JSON. |
| `lede_facts` | Key facts/fact records as structured JSON. |
| `lede_report` | Full lede JSON payload for machine ingest. |
| `lede_search_text` | Flattened summary/facts/attributes for FTS/embedding enrichment. |
| `char_count`, `token_count_est` | Cost/budget planning. |
| `chunk_count` | Diagnostics and query planning. |

### Chunk Table

Chunk rows should continue to store:

- `original_content`
- `embedded_content`
- `embedding`
- `metadata`
- `tags`

Chunk metadata can still contain local heading, offsets, and per-chunk extractor
results. Document-level summary/facts should not be duplicated into every chunk
except where explicitly needed for enriched embedding/search.

## Why This Matters

### Performance

- Run lede report once per document, not once per chunk/config.
- Avoid reconstructing full documents with `array_agg(original_content ORDER BY seq_num)`.
- Let doc-summary profiles fetch one document row after chunk retrieval.
- Cache doc-level artifacts across vector metrics and context strategies.

### Retrieval Quality

- Doc-summary-heavy profiles become first-class for SCOTUS/legal workloads.
- Chunk-summary-plus-raw profiles can still use the chunk table for MHR/news.
- Hybrid search can index both chunk text and document `lede_search_text`.
- Metadata filters can target promoted document columns rather than repeated
  chunk metadata.

### Evaluation

The benchmark harness can distinguish:

- retrieval miss: chunk hits did not identify the right document;
- document-summary loss: document row lacks required fact;
- chunk-summary loss: retrieved chunk summary dropped required fact;
- answer failure: context had the fact but model missed it.

## Config Shape

Add an optional document-store section under `target`:

```yaml
target:
  type: postgres
  database: chunkshop_scotus
  table: chunks
  documents:
    enabled: true
    table: documents
    store_full_content: true
    store_lede_report: true
    promote_metadata:
      - path: lede_report.attributes.term.value
        type: text
      - path: lede_report.attributes.docket_number.value
        type: text
      - path: lede_report.attributes.citation.value
        type: text
    fts:
      enabled: true
      language: english
```

Default policy:

- v1: `documents.enabled: false` for strict backward compatibility.
- New KB templates: `documents.enabled: true`.
- Later major/minor release: consider making document tables default for new
  targets while preserving existing table compatibility.

## Query Patterns

### Chunk-first, Document Context

```sql
WITH hits AS (
  SELECT doc_id, seq_num, score
  FROM chunkshop_scotus.chunks
  ORDER BY embedding <=> $1::vector
  LIMIT 25
)
SELECT DISTINCT d.doc_id, d.lede_summary, d.lede_toc, d.lede_facts
FROM hits h
JOIN chunkshop_scotus.documents d USING (doc_id);
```

Use for legal/doc corpora where top chunks identify the right parent document,
then the answer context should use document-level facts/summary.

### Chunk Context With Document Metadata

```sql
SELECT c.doc_id, c.seq_num, d.title, d.lede_summary, c.original_content
FROM chunkshop_news.chunks c
JOIN chunkshop_news.documents d USING (doc_id)
ORDER BY c.embedding <=> $1::vector
LIMIT 25;
```

Use for cross-document corpora where raw chunk evidence remains important.

### Metadata-First Retrieval

```sql
SELECT doc_id, lede_summary, lede_facts
FROM chunkshop_scotus.documents
WHERE metadata->'lede_report'->'attributes'->'term'->>'value' IN ('2022', '2023')
  AND lede_search_text @@ plainto_tsquery('english', 'Ketanji Brown Jackson');
```

Use for deterministic or metadata-shaped questions. RAG should explain/support
the result, not discover exhaustive counts by sampling top-k chunks.

## Implementation Steps

1. Add config models:
   - `target.documents.enabled`
   - `target.documents.table`
   - `target.documents.store_full_content`
   - `target.documents.store_lede_report`
   - `target.documents.promote_metadata`
   - `target.documents.fts`
2. Add backend DDL helpers for document tables.
3. Extend sinks with `write_document_record(...)` before chunk insert.
4. Compute document-level lede report once per input `Document`.
5. Store document row and update `chunk_count` after chunking.
6. Keep chunk-table-only mode unchanged.
7. Add query helpers that can fetch parent document rows from chunk hits.
8. Update `chunkshop search`/eval packers to use document rows for
   `doc_summary_facts` and mixed doc+chunk strategies.
9. Add Rust parity for config, DDL, sink writes, and tests.

Status:

- Implemented in Python/Postgres: 1-6.
- Remaining: query helper/search integration, eval packer use of document
  rows, Rust parity, and non-Postgres backend parity decisions.

## Tests

### Unit

- Document table DDL emits expected columns for Postgres.
- Config validation rejects document table name equal to chunk table name.
- Metadata promotion paths work against document-level `lede_report`.
- `store_full_content=false` stores summary/facts without full text.

### Integration

- Ingest two documents and verify:
  - two document rows;
  - N chunk rows;
  - `chunk_count` matches actual chunks;
  - document summaries/facts exist;
  - chunk rows join to document rows by `doc_id`.
- Re-ingest same doc and verify upsert replaces document metadata and chunks.
- Delete document and verify chunks are deleted or orphan cleanup remains
  correct, depending on backend capability.
- Search chunks, fetch parent document rows, and build `doc_summary_facts`.

### Benchmark

- Re-run SCOTUS doc-summary strategies using document table fields instead of
  reconstructing or repeating summaries from chunks.
- Compare token count, latency, and accuracy against the current chunk-only
  path.

## Compatibility Notes

- Existing tables keep working.
- Existing chunk-only configs keep working.
- `promote_metadata` on the chunk target remains valid.
- New document-level promotion is additive and explicitly scoped under
  `target.documents`.
- Backends that cannot enforce foreign keys still write `doc_id` and rely on
  query joins/cleanup logic.
