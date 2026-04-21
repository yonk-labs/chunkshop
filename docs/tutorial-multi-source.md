# Tutorial: unify multiple sources in one retrieval table

This tutorial walks through ingesting two different sources — markdown files (representing output from `yonk-doctools` PDF prep) and a JSON corpus (representing API-exported support tickets) — into a single pgvector table with a `source` discriminator column and a promoted `language` column.

End state: one table `mydata.all_docs` containing rows from both sources, filterable by `source`, with `language` indexable as a first-class column.

## Prereqs

- chunkshop v0.3.0+ (schema-flexibility features).
- A Postgres 14+ with the `pgvector` extension. A quick local option:

  ```bash
  docker run --rm -d --name chunkshop-pg \
      -e POSTGRES_PASSWORD=postgres \
      -p 5432:5432 \
      pgvector/pgvector:pg16
  psql "postgresql://postgres:postgres@localhost:5432/postgres" \
      -c "CREATE DATABASE mydb;" \
      -c "\c mydb" \
      -c "CREATE EXTENSION IF NOT EXISTS vector;" \
      -c "CREATE SCHEMA IF NOT EXISTS mydata;"
  ```

- `export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"`.
- A directory of markdown files you want to ingest (we'll refer to it as `/path/to/your/docs/`). Any README-style `.md` files will do — point at your own corpus.
- Optional: the `[lang]` extra (`uv sync --extra lang`) to populate the `language` metadata. Without it, the promoted column will be NULL; the tutorial still works.

## Step 1 — Cell A: ingest markdown files, create the unified table

```yaml
# cell-a-markdown.yaml
cell_name: docs_markdown
source:
  type: files
  glob: /path/to/your/docs/**/*.md   # point at your own markdown corpus
  id_from: stem
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: lang_detect          # optional — populates metadata.language if [lang] extra installed
  backend: langdetect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: all_docs
  mode: create_if_missing    # first cell creates the table if not present
  source_tag: docs_markdown
  promote_metadata:
    - path: language
      type: text
```

Run it:

```bash
chunkshop ingest --config cell-a-markdown.yaml
```

Verify:

```bash
psql "$CHUNKSHOP_DSN" -c "SELECT COUNT(*), COUNT(DISTINCT source) FROM mydata.all_docs"
```

You should see a row count matching the number of chunks your corpus produced and `COUNT(DISTINCT source) = 1` with value `docs_markdown`.

## Step 2 — Cell B: ingest a JSON corpus, append to the same table

Fabricate a small JSON corpus at `tickets.json`:

```json
{"documents": [
  {"id": "t1", "title": "Login", "content": "# Login issues\n\nUsers report intermittent login failures."},
  {"id": "t2", "title": "Export", "content": "# Export failing\n\nCSV export times out on large datasets."}
]}
```

Cell B config:

```yaml
# cell-b-tickets.yaml
cell_name: support_tickets
source:
  type: json_corpus
  path: ./tickets.json
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: lang_detect
  backend: langdetect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: all_docs
  mode: append               # second cell appends — pre-flight verifies dim match
  source_tag: support_tickets
  promote_metadata:
    - path: language
      type: text
```

Run:

```bash
chunkshop ingest --config cell-b-tickets.yaml
```

Before any rows are written, chunkshop's **append pre-flight** runs:

1. The target table `mydata.all_docs` exists — **pass**.
2. The table's `embedding` column dim matches this cell's embedder `dim` (both 384) — **pass**.
3. The `source` column exists on the table (auto-added in Cell A; `ADD COLUMN IF NOT EXISTS` is idempotent on this cell too) — **pass**.
4. Every `promote_metadata` column exists or is addable (`language text`) — **pass**.

If any check fails, chunkshop raises a clear error and inserts nothing. Try it: change `dim: 384` to `dim: 768` in `cell-b-tickets.yaml` and re-run — the pre-flight will refuse before writing.

## Step 3 — Verify the unification

```sql
-- Total rows across sources
SELECT source, COUNT(*) FROM mydata.all_docs GROUP BY source;
--       source       | count
-- -------------------+-------
--  docs_markdown     |    N1
--  support_tickets   |     2

-- Language is a promoted column — you can filter / GROUP BY it
SELECT source, language, COUNT(*) FROM mydata.all_docs GROUP BY source, language;

-- Ingest times from the orchestrator output will differ per cell;
-- record them before moving on so you can set SLAs on future runs.
```

## Step 4 — A cross-source retrieval query

```python
# query.py
import os, psycopg
from fastembed import TextEmbedding
import chunkshop.embedders  # register int8 variant

qvec = list(TextEmbedding(model_name="Xenova/bge-small-en-v1.5-int8").embed(["why are logins failing"]))[0]
qlit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

with psycopg.connect(os.environ["CHUNKSHOP_DSN"]) as conn, conn.cursor() as cur:
    # Search everything
    cur.execute(
        """
        SELECT source, doc_id, seq_num, original_content,
               embedding <=> %s::vector AS distance
        FROM mydata.all_docs
        ORDER BY embedding <=> %s::vector
        LIMIT 3
        """, (qlit, qlit),
    )
    for row in cur.fetchall():
        print(row[:4], f"dist={row[4]:.4f}")

    # Restrict to tickets only
    cur.execute(
        """
        SELECT source, doc_id, seq_num
        FROM mydata.all_docs
        WHERE source = 'support_tickets'
        ORDER BY embedding <=> %s::vector LIMIT 3
        """, (qlit,),
    )
    print("---filtered---")
    for row in cur.fetchall():
        print(row)
```

The unfiltered query should return the login-issues chunk as top-1. The filtered query returns only ticket rows regardless of score.

## Step 5 — Clean up or iterate

- Add another cell with `mode: append` and a new `source_tag` to layer a third source in.
- If you want to wipe a cell's rows only: `DELETE FROM mydata.all_docs WHERE source = 'docs_markdown'` — chunkshop does not provide this as a CLI operation on purpose (too destructive to bake in).
- To overwrite the entire table when it contains foreign-source rows, set `target.force_overwrite: true` in YAML — chunkshop refuses the implicit case.

## What this demonstrates

- **SC-001 / SC-003:** `mode: append` + `source_tag` populate the `source` column.
- **SC-002:** pre-flight verifies dim match and auto-adds missing `source`/promoted columns.
- **SC-004:** `promote_metadata` lifts `metadata.language` into a typed column.
- **SC-006:** two cells, one table, filter by source works.
- **SC-007:** switching Cell A to `mode: overwrite` without `force_overwrite` would fail after Cell B has loaded (try it to see the error).
