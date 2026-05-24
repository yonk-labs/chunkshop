# Chunkshop tutorial — zero to retrieval in 15 minutes

This is the end-to-end walkthrough: install chunkshop, stand up a Postgres with pgvector,
ingest a sample corpus, inspect the results, run a semantic query, then point it at your own
docs. Copy-paste friendly.

If you want the field-by-field reference instead, see [`../python/README.md`](../python/README.md).
For the architecture / chunker / embedder deep-dives, see
[`architecture.md`](architecture.md), [`chunkers.md`](chunkers.md), and
[`embedders.md`](embedders.md).

## What you'll have at the end

- A Postgres database with the pgvector extension enabled.
- A `chunkshop_samples.handbook` table holding ~8 chunks of embedded markdown.
- A Python snippet that takes a plain-English query and returns the top-k matching chunks.
- A working config you can re-point at your own corpus.

Roughly 15 minutes start to finish, most of which is the first model download.

## Prerequisites

| You need                   | Minimum           | How to check                                      |
|----------------------------|-------------------|---------------------------------------------------|
| Python                     | 3.12              | `python3 --version`                               |
| [`uv`](https://docs.astral.sh/uv/) | any recent | `uv --version`                                    |
| Docker OR a local Postgres | any recent        | `docker --version` or `psql --version`            |
| Disk space                 | ~1 GB             | ~85 MB for int8 bge-base + ~500 MB for Postgres   |
| Network for first run      | yes               | Downloads ONNX model from HuggingFace             |

`uv` is the fastest path. If you'd rather use pip, substitute `pip install -e .` for
`uv sync` below — everything else works identically.

## Step 1 — Start Postgres with pgvector

The easiest option is the prebuilt `pgvector/pgvector` image:

```bash
docker run -d \
  --name chunkshop-pg \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Wait ~5 seconds for it to boot, then verify:

```bash
docker exec -it chunkshop-pg psql -U postgres -c "SELECT version();"
docker exec -it chunkshop-pg psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector';"
```

You should see the Postgres version and a pgvector extversion like `0.7.4`.

### Already have a Postgres?

Skip the docker step and just make sure pgvector is installed. On Debian/Ubuntu:

```bash
sudo apt install postgresql-16-pgvector   # or -15, -14 depending on your server
```

Then as a DB superuser:

```sql
CREATE DATABASE chunkshop_tutorial;
\c chunkshop_tutorial
CREATE EXTENSION vector;
```

chunkshop's sink will run `CREATE EXTENSION IF NOT EXISTS vector` on every call, but that
requires the role chunkshop connects as to have extension-creation privileges. If that role
is not a superuser, run the `CREATE EXTENSION` above once yourself and chunkshop's call will
be a no-op.

## Step 2 — Install chunkshop

```bash
git clone https://github.com/yonk-labs/chunkshop.git
cd chunkshop/python
uv sync --extra dev
```

This creates `.venv/` in `chunkshop/python/`, installs all runtime deps plus pytest, and
registers the `chunkshop` console script inside that venv.

Activate the venv (or prefix every command with `uv run`):

```bash
source .venv/bin/activate
chunkshop --version
```

From here on, the tutorial assumes you're inside the venv and sitting at the
`chunkshop/` (repo root) directory.

## Step 3 — Point chunkshop at your database

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/postgres"
```

The name of the env var is arbitrary — the YAML config references it by name (`dsn_env:
CHUNKSHOP_DSN`), not by value. The shipped `docs/samples/sample.yaml` uses `CHUNKSHOP_DSN`.

Sanity check:

```bash
psql "$CHUNKSHOP_DSN" -c "SELECT 1"
```

## Step 4 — Run your first ingest

From the `chunkshop/` repo root:

```bash
cd ..   # if you're still in python/
chunkshop ingest --config docs/samples/sample.yaml
```

What happens, in order:

1. chunkshop reads the YAML and validates it with pydantic.
2. `FastembedProvider.__init__` triggers — first run downloads
   `Xenova/bge-base-en-v1.5-int8` (~85 MB) to `~/.cache/fastembed/`. You'll see download
   progress on stderr.
3. The sink runs `CREATE EXTENSION IF NOT EXISTS vector`, creates schema
   `chunkshop_samples`, drops and recreates `handbook` (because `overwrite: true`).
4. Each of the four sample markdown files is read, chunked with `hierarchy`, embedded,
   and inserted.
5. A JSON summary lands on stdout.

Expected output (times will vary):

```json
{
  "cell_name": "samples_hierarchy_int8",
  "docs_processed": 4,
  "chunks_written": 8,
  "wall_seconds": 12.8,
  "error": null
}
```

If you see `"error": "..."` instead, jump to [Troubleshooting](#troubleshooting) below.

## Step 5 — Inspect what landed

Open a psql session:

```bash
psql "$CHUNKSHOP_DSN"
```

Check the schema and row counts:

```sql
\dt chunkshop_samples.*
-- handbook table should appear.

SELECT COUNT(*), COUNT(DISTINCT doc_id) FROM chunkshop_samples.handbook;
--  count | count
-- -------+-------
--      8 |     4
```

Peek at a few rows:

```sql
SELECT
  doc_id,
  seq_num,
  metadata->>'heading' AS heading,
  length(original_content) AS raw_len,
  length(embedded_content) AS emb_len
FROM chunkshop_samples.handbook
ORDER BY doc_id, seq_num;
```

You should see something like:

```
      doc_id         | seq_num |       heading        | raw_len | emb_len
---------------------+---------+----------------------+---------+---------
 handbook-engineering|       0 | Code review          |     647 |     660
 handbook-engineering|       1 | Testing              |     820 |     829
 handbook-engineering|       2 | Deployment           |     637 |     649
 handbook-engineering|       3 | On-call              |     572 |     581
 handbook-intro      |       0 | What we do           |     361 |     374
 handbook-intro      |       1 | How this handbook... |     347 |     367
 handbook-security   |       0 | Secrets management   |     849 |     870
 handbook-security   |       1 | Least privilege      |     476 |     489
 release-notes       |       0 |                      |    1511 |    1525
```

Notice: `emb_len > raw_len` because the hierarchy chunker prefixes the heading into
`embedded_content`. The "See also" section of `handbook-security.md` was dropped because it
was under the 100-char `min_section_chars` threshold.

Check the embedding dimension:

```sql
SELECT vector_dims(embedding) FROM chunkshop_samples.handbook LIMIT 1;
-- Should return 768 (bge-base).
```

## Step 6 — Your first semantic query

This is the payoff: given a plain-English question, find the chunks whose embeddings are
closest. You need to produce a query vector with the **same model** the cell used
(`Xenova/bge-base-en-v1.5-int8`), then use pgvector's `<=>` cosine-distance operator.

Create `query.py` anywhere:

```python
# query.py — run from the chunkshop repo root, with the venv active
import os

import psycopg
from fastembed import TextEmbedding

# chunkshop registers the int8 variant on import.
import chunkshop.embedders  # noqa: F401

MODEL = "Xenova/bge-base-en-v1.5-int8"
DSN = os.environ["CHUNKSHOP_DSN"]
QUERY = "how do we handle customer credentials"
TOP_K = 3

embedder = TextEmbedding(model_name=MODEL, threads=4)
(qvec,) = list(embedder.embed([QUERY]))
qlit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute(
        """
        SELECT doc_id, seq_num, metadata->>'heading', original_content,
               embedding <=> %s::vector AS distance
        FROM chunkshop_samples.handbook
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (qlit, qlit, TOP_K),
    )
    for doc_id, seq, heading, content, distance in cur.fetchall():
        print(f"[{distance:.4f}] {doc_id}::{seq}  {heading or '(no heading)'}")
        print(f"    {content[:160].strip()}...")
        print()
```

Run it:

```bash
python query.py
```

You should see the "Secrets management" section from `handbook-security.md` as the top
result, followed by related sections:

```
[0.1821] handbook-security::0  Secrets management
    Secrets live in our secrets manager. They do not live in code, in config files checked into git, in Slack messages, in pastebins, in screenshots attached to Jira ti...

[0.2734] handbook-security::1  Least privilege
    Every service account, human user, and automated job gets the minimum permission set needed to do its work...

[0.3915] handbook-engineering::3  On-call
    On-call rotation is one week, handed off on Wednesdays at 10am...
```

The `<=>` operator returns cosine distance: lower is more similar. bge-small embeddings are
already normalized, so `<=>` and `<#>` (negative inner product) give equivalent rankings.

Try swapping in other queries:

- `"how long is the code review SLA"` → returns "Code review".
- `"what do we do after an outage"` → returns "Deployment" or "On-call".
- `"shipment search changes this month"` → returns `release-notes`.

## Step 7 — Point chunkshop at your own docs

Copy the sample config and edit three things:

```bash
cp docs/samples/sample.yaml my-cell.yaml
```

Edit `my-cell.yaml`:

```yaml
cell_name: my_docs

source:
  type: files
  glob: /absolute/path/to/your/docs/**/*.md   # ← CHANGE THIS
  id_from: stem
  encoding: utf-8

chunker:
  type: hierarchy
  prefix_heading: true
  min_section_chars: 100

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 4
  batch_size: 64

extractor:
  type: none

target:
  dsn_env: CHUNKSHOP_DSN
  schema: my_docs                             # ← CHANGE THIS
  table: chunks                               # ← CHANGE THIS (or keep)
  overwrite: false                            # safer default for real data
  hnsw: true                                  # turn on once you have >1k chunks

runtime:
  omp_num_threads: 4
  heartbeat_every: 25
```

Test the glob first — chunkshop errors out cleanly if nothing matches:

```bash
ls /absolute/path/to/your/docs/**/*.md | head   # zsh
find /absolute/path/to/your/docs -name '*.md' | head   # portable
```

Smoke-test with 3 docs:

```bash
chunkshop ingest --config my-cell.yaml --doc-limit 3
```

If that works, remove the `--doc-limit` and run for real. `query.py` from step 6 still
works — just swap the table name.

## Step 8 — Compare chunking strategies

The `docs/samples/` directory ships three configs against the same corpus. Run them all
in parallel:

```bash
chunkshop orchestrate \
  -c docs/samples/sample.yaml \
  -c docs/samples/sample-sentence-aware.yaml \
  -c docs/samples/sample-neighbor-expand.yaml \
  --concurrency 3
```

You now have three tables in `chunkshop_samples` holding the same source data chunked three
different ways. Re-run `query.py` against each by changing the table name:

```python
# in query.py
for table in ("handbook", "handbook_sentence_aware", "handbook_neighbor_expand"):
    print(f"=== {table} ===")
    # ... same cursor query, f-string in table name ...
```

Compare the top-1 result across all three. On a corpus this small, the results will
usually agree; the differences show up on larger corpora with >100 docs.

For a proper bake-off harness — multiple embedders × multiple chunkers, with the same
source writing to 12-24 tables — see `python/src/chunkshop/configs/factorial-int8/` and
the guidance in [`embedders.md`](embedders.md#ab-testing-two-embedders).

## Step 9 — Next steps

### Switch embedders

Edit `embedder.model_name` and `embedder.dim` in your YAML. Popular options:

| `model_name`                             | `dim` | Precision | Trade-off                                |
|------------------------------------------|-------|-----------|------------------------------------------|
| `Xenova/bge-base-en-v1.5-int8`           | 768   | int8      | **Default.** Best quality-for-size.      |
| `Xenova/bge-small-en-v1.5-int8`          | 384   | int8      | Smaller & faster; ~3–5 fewer MTEB pts.   |
| `BAAI/bge-base-en-v1.5`                  | 768   | fp32      | +0–2 points recall over int8; 2× ingest. |
| `nomic-ai/nomic-embed-text-v1.5-Q`       | 768   | int8      | 8k-token context; long docs.             |

After changing the embedder, use `overwrite: true` (or a different table) — you can't mix
vectors from different models in one table, they're not comparable.

### Tune chunk size

Both `hierarchy` and `sentence_aware` accept a `max_chars` field (default `2000`, ≈500
tokens — safe for bge-small/bge-base's 512-token limit). If your corpus has long sections
(a 134 KB "About topic X" block, say), the chunker now hard-splits on
paragraph → sentence → character boundaries instead of silently feeding the embedder a
truncated chunk. Split children from one hierarchy section share `metadata.heading` and
carry `metadata.section_part: 0, 1, ...` so you can reconstruct order.

Raise `max_chars` if you swap to a larger-context embedder:

```yaml
chunker:
  type: hierarchy
  max_chars: 6000   # ~1500 tokens; safe for text-embedding-3-small
```

Full tuning table per embedder in [`chunkers.md`](chunkers.md#tuning-max_chars-for-your-embedder).

### Switch chunkers

Edit `chunker.type` and its fields. Decision tree lives in
[`chunkers.md`](chunkers.md#quick-pick). The short version:

- Markdown with real headings → `hierarchy` (default).
- Generic prose → `sentence_aware`.
- Short QA-style items → `fixed_overlap`.
- Want extra recall on any of the above → wrap with `neighbor_expand`.

### Turn on keyword tagging

```yaml
extractor:
  type: rake_keywords
  top_k: 10
  min_chars: 3
```

First run downloads NLTK corpora (`stopwords`, `punkt`). The `tags` column on each row gets
a `text[]` of extracted phrases you can GIN-index and filter on alongside the vector search.

### Add HNSW once you have volume

The default `hnsw: true` creates `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`.
Below ~1k rows HNSW is slower than a sequential scan. Above ~10k rows it's dramatically
faster. The sample config turns it off because 8 rows don't need it; your real corpus
probably does.

Postgres also supports `target.vector_metric: cosine` (default),
`inner_product`, or `l2`. chunkshop maps those to pgvector's `<=>`, `<#>`, and
`<->` operators and picks the matching HNSW opclass.

## Troubleshooting

### "no files matched glob: ..."

Python's `glob.glob(..., recursive=True)` treats `**` as "any depth" **only** when it's its
own path component. `/foo/**/*.md` works; `/foo/**.md` does not.

### "model X produced dim Y, config says dim=Z"

`embedder.dim` in your YAML doesn't match the model's actual output. The table above has
the right numbers. The check runs at first embed, so no bad data lands in Postgres.

### "CREATE EXTENSION ... permission denied"

Your DB role can't create extensions. Have a superuser run `CREATE EXTENSION vector` once
in the target database, then retry — chunkshop's `IF NOT EXISTS` call becomes a no-op.

### First run hangs forever

Fastembed is downloading the ONNX file. Check `curl -sI https://huggingface.co/`. If you're
behind a corporate proxy, set `HF_HUB_ENABLE_HF_TRANSFER=0` and retry. The file lands in
`~/.cache/fastembed/<model-name>/` once it completes.

### Empty rows / no chunks written

Hierarchy silently drops sections below `min_section_chars` (default 100). If your docs are
very short sections, lower the threshold or switch to `sentence_aware`.

### "relation already exists" on second run

`target.overwrite` is `false` by default. Either flip to `true` (drops + recreates the
table) or let the `ON CONFLICT DO UPDATE` path upsert into the existing table. Different
chunkings will just overwrite each row by `{doc_id}::{seq_num}`.

More troubleshooting in [`../python/README.md`](../python/README.md#troubleshooting).

## What chunkshop deliberately doesn't do

- No retrieval layer — you bring the query side. The Python snippet in Step 6 is typical.
- No streaming / incremental ingest — runs to completion, exits, writes a summary.
- No LLM in the ingest path (the optional `rake_keywords` extractor is purely local).
- No cross-model table — one table = one embedding model.

For the "why" behind those choices, see [`architecture.md`](architecture.md#what-chunkshop-is-not).
