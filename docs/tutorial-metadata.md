# Tutorial: metadata extraction with the composite pipeline

## Why metadata extractors?

Retrieval that only matches on text embeddings can't distinguish "Apple's Q3
earnings" from "apple pie recipes" — both words land near each other in vector
space. And pure vector search can't filter "show me only chunks mentioning
Microsoft" without reading every row and substring-matching, which doesn't
scale past a few thousand chunks.

The fix is to extract **structured metadata at ingest time** — named entities,
language codes, topic keyphrases — and either filter by or weight by that
metadata at query time. Vector search becomes the last-mile ranker over a
set the SQL engine has already narrowed for you.

This tutorial builds a composite extractor pipeline that lands
`entities.ORG` as a `text[]` column and `language` as a `text` column on a
real Postgres table. Then it runs two queries that would be impossible
without the metadata: "find chunks that mention Microsoft *and* pin to the
English slice" and "rank chunks about Apple by vector similarity to a
natural-language query."

End state: one table `chunkshop_meta_tut.news_chunks` with typed columns
for entities and language, a GIN index on `entities__org` for fast filtering,
and two queries that couldn't work on the raw embedding alone.

## Prereqs

- chunkshop v0.3.0+ (schema-flexibility features — `promote_metadata` with
  dotted paths).
- A Postgres 14+ with `pgvector`. Quick local option:

  ```bash
  docker run --rm -d --name chunkshop-pg \
      -e POSTGRES_PASSWORD=postgres -p 5432:5432 \
      pgvector/pgvector:pg16
  psql "postgresql://postgres:postgres@localhost:5432/postgres" \
      -c "CREATE DATABASE chunkshop;" \
      -c "\c chunkshop" \
      -c "CREATE EXTENSION IF NOT EXISTS vector;" \
      -c "CREATE SCHEMA IF NOT EXISTS chunkshop_meta_tut;"
  ```

- `export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/chunkshop"`.
- The `[nlp]` umbrella extra: `uv sync --extra dev --extra extractors --extra nlp`
  (installs keybert, spacy, and langdetect in one step). First spaCy run will
  auto-download the `en_core_web_sm` model (~50 MB, cached under
  `~/.cache/spacy/`).

## Step 1 — Prepare a small news corpus

A realistic demo needs recognizable entities. Create a fixture file:

```bash
mkdir -p /tmp/chunkshop-tut
cat > /tmp/chunkshop-tut/news.json <<'EOF'
{"documents": [
  {"id": "apple_q3",
   "title": "Apple Q3 earnings",
   "content": "# Apple Q3\n\nApple Inc. reported record earnings this quarter. Tim Cook spoke at the event in Cupertino about product strategy and services growth. Analysts highlighted installed-base expansion as a durable tailwind."},
  {"id": "msft_openai",
   "title": "Microsoft OpenAI deal",
   "content": "# Microsoft deal\n\nMicrosoft announced a new partnership with OpenAI in Redmond. Satya Nadella described the deal as strategic for Azure customers and laid out an enterprise AI adoption agenda."},
  {"id": "eu_reg",
   "title": "EU AI Act update",
   "content": "# EU AI Act\n\nThe European Commission in Brussels finalized implementation guidance for the AI Act. Ursula von der Leyen emphasized that high-risk systems face stricter oversight."}
]}
EOF
```

## Step 2 — Write the composite extractor YAML

```yaml
# /tmp/chunkshop-tut/cell.yaml
cell_name: news_metadata
source:
  type: json_corpus
  path: /tmp/chunkshop-tut/news.json
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: composite
  extractors:
    - type: spacy_entities
      label_whitelist: [ORG, PERSON, GPE]
    - type: lang_detect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_meta_tut
  table: news_chunks
  mode: create_if_missing
  source_tag: news
  promote_metadata:
    - { path: entities.ORG, type: "text[]" }
    - { path: entities.PERSON, type: "text[]" }
    - { path: entities.GPE, type: "text[]" }
    - { path: language, type: text }
  hnsw: true
```

Three things to notice:

1. **`composite` chains two children.** spaCy extracts entities, lang_detect
   adds the language code. Both write into the same chunk's `metadata` dict
   (different keys — no collision).
2. **`promote_metadata` uses dotted paths.** `entities.ORG` lifts the nested
   jsonb path to a typed `text[]` column named `entities__org`. Same for
   `.PERSON`, `.GPE`, and the top-level `language`.
3. **The chunker is `hierarchy`** — markdown sections become chunks. With
   short docs, the three stories produce 3 chunks; longer docs would split.

## Step 3 — Ingest

```bash
chunkshop ingest --config /tmp/chunkshop-tut/cell.yaml
```

What happens, in order:

1. Source: loads three documents from the JSON file.
2. Framer: default `IdentityFramer` (no-op).
3. Chunker: `hierarchy` splits each doc on `#` headings.
4. Embedder: fastembed embeds each chunk's `embedded_content`.
5. Extractor: `composite` runs spaCy NER then `lang_detect` on each chunk's
   text. spaCy model auto-downloads on first run if not cached.
6. Sink: `create_if_missing` mode builds the table if absent, adding
   typed columns for each `promote_metadata` entry. Writes rows.

You'll see per-cell timing in the log — something like:

```
[15:42:10] cell news_metadata starting
[15:42:10] cell news_metadata: loaded 3 docs
[15:42:10] cell news_metadata: chunker -> 3 chunks
[15:42:11] cell news_metadata: embedder -> 3 vectors
[15:42:11] cell news_metadata: extractor -> 3 results
[15:42:11] cell news_metadata: sink wrote 3 rows
[15:42:11] cell news_metadata done in 0.85s
```

## Step 4 — Verify the schema and data

```sql
\d chunkshop_meta_tut.news_chunks
```

You should see columns including:

- `embedding vector(384)` — the chunk embedding.
- `tags text[]` — empty (neither child writes `tags`).
- `metadata jsonb` — the full structured metadata dict.
- `entities__org text[]`, `entities__person text[]`, `entities__gpe text[]` —
  promoted from `metadata.entities.ORG/PERSON/GPE`.
- `language text` — promoted from `metadata.language`.

```sql
-- Who got detected as an ORG across the corpus?
SELECT DISTINCT unnest("entities__org") AS org FROM chunkshop_meta_tut.news_chunks;
-- Expected (subset): Apple Inc., Microsoft, OpenAI, Azure,
--                     European Commission, ...
```

```sql
-- Per-language row counts (should all be 'en'):
SELECT language, COUNT(*) FROM chunkshop_meta_tut.news_chunks GROUP BY language;
--  language | count
-- ----------+-------
--  en       |     3
```

```sql
-- Top 10 orgs by mention count:
SELECT org, COUNT(*) AS mentions
FROM chunkshop_meta_tut.news_chunks, unnest("entities__org") AS org
GROUP BY org ORDER BY mentions DESC LIMIT 10;
```

## Step 5 — Add a GIN index for entity filtering

Without an index, `array_contains` on `entities__org` does a seq scan. With
three chunks that's fine; at a million-chunk scale it isn't. Add a GIN index:

```sql
CREATE INDEX news_chunks_entities_org_gin
  ON chunkshop_meta_tut.news_chunks USING gin ("entities__org");

CREATE INDEX news_chunks_language_btree
  ON chunkshop_meta_tut.news_chunks (language);
```

GIN turns `entities__org @> ARRAY['Microsoft']` into an index lookup. B-tree
on `language` handles equality filters. `EXPLAIN ANALYZE` on any of the
queries below will now show `Bitmap Index Scan` instead of `Seq Scan`.

## Step 6 — Two queries that need the metadata

### Query A — filter-first, then similarity

**Goal:** "find English chunks mentioning Microsoft, ranked by vector
similarity to 'enterprise AI strategy'."

Without metadata: impossible with SQL alone; you'd have to embed the query,
fetch top-K vectors, then substring-filter in the app — slow and error-prone.

With metadata: SQL filters first, pgvector ranks the surviving rows.

```python
# /tmp/chunkshop-tut/query_a.py
import os, psycopg
from fastembed import TextEmbedding
import chunkshop.embedders  # register int8 variant

q = "enterprise AI strategy"
qvec = list(TextEmbedding(model_name="Xenova/bge-small-en-v1.5-int8").embed([q]))[0]
qlit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

with psycopg.connect(os.environ["CHUNKSHOP_DSN"]) as conn, conn.cursor() as cur:
    cur.execute(
        """
        SELECT doc_id, seq_num, language, "entities__org",
               embedding <=> %s::vector AS distance,
               left(original_content, 120) AS preview
        FROM chunkshop_meta_tut.news_chunks
        WHERE language = 'en'
          AND "entities__org" @> ARRAY['Microsoft']
        ORDER BY embedding <=> %s::vector
        LIMIT 3
        """,
        (qlit, qlit),
    )
    for row in cur.fetchall():
        print(row)
```

Expected: the Microsoft/OpenAI chunk wins — it mentions both "Microsoft" (SQL
filter) and has the strongest cosine similarity to "enterprise AI strategy"
(vector rank).

### Query B — entity facet counts

**Goal:** "how many chunks mention each person?"

```sql
SELECT person, COUNT(*) AS mentions
FROM chunkshop_meta_tut.news_chunks, unnest("entities__person") AS person
GROUP BY person ORDER BY mentions DESC;
--   person               | mentions
-- ----------------------+----------
--  Satya Nadella        |        1
--  Tim Cook             |        1
--  Ursula von der Leyen |        1
```

This is the building block for a faceted search UI — show users a left-nav
list of entities and let them click to filter.

## Step 7 — Measure: timings and entity counts

Composite extractor cost is the sum of its children. Typical per-chunk wall
time (warm cache, laptop CPU):

| Child extractor   | Per-chunk ms (approx.)  | Notes                                |
|-------------------|--------------------------|---------------------------------------|
| `lang_detect`     | <1 ms                    | Pure Python, no network, no model    |
| `spacy_entities`  | 5–20 ms (sm model)       | CPU-bound; `en_core_web_trf` is ~10x |
| `keybert_phrases` | 50–200 ms                | One MiniLM embed + n-gram scoring    |
| `composite`       | sum of children          | Sequential; no parallelism today      |

For a 10k-chunk corpus, `spacy_entities + lang_detect` adds roughly 1–4
minutes of wall time to the cell total. `keybert_phrases` on top of that adds
another 10–30 minutes depending on chunk size.

Count of unique orgs (corpus-wide):

```sql
SELECT COUNT(DISTINCT org) AS unique_orgs
FROM chunkshop_meta_tut.news_chunks, unnest("entities__org") AS org;
-- For the 3-doc demo above: typically 4-6 depending on spaCy's model version.
```

## What this demonstrates

- **SC-001 (keybert):** optional — swap `spacy_entities` for
  `keybert_phrases` in the YAML to see phrase-based tags instead of entity
  metadata.
- **SC-002 (spacy_entities):** every row has `entities__org` populated;
  query by org works.
- **SC-003 (lang_detect):** `language = 'en'` on every row; multilingual
  corpora would split the group-by count.
- **SC-004 (composite):** both children's metadata merged onto the same
  chunk without collision; a failure in either child would have raised with
  the child name in the message (no silent swallowing).
- **SC-007 (this tutorial):** the end-to-end walkthrough above — ingest,
  promote, filter, rank — is the narrative proof that extractor metadata →
  typed columns → SQL-gated pgvector queries works on a real Postgres.

## Next steps

- Swap `en_core_web_sm` for `en_core_web_trf` (transformer-based spaCy model,
  ~500 MB) for higher entity recall on noisy text.
- Add `keybert_phrases` as a third child and promote `tags` for a UI tag
  cloud.
- Try a multilingual corpus — mix English and French docs. `lang_detect`
  populates `language` accordingly; queries can pin to one language.
- See [`extractors.md`](extractors.md) for config details on each child
  extractor, and [`quickstart-extractors.md`](quickstart-extractors.md) for
  single-extractor recipes.
