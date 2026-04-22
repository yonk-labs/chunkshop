# Tutorial: DocFramer — re-slice raw sources into logical documents

Chunkshop's source loaders give you one `Document` per file (or one per row in a JSON
corpus). That's the right shape when the file *is* the logical unit. It's the wrong
shape when one file is a concatenation of unrelated topics, or when a JSON payload
nests the real items under a key you can't control.

DocFramer is the stage that sits between source and chunker. It takes a raw `Document`
and yields N framed `Document`s. Every downstream stage — chunker, embedder, target —
operates on the framed docs, so "one logical document" means whatever the framer says
it means.

This tutorial walks two realistic scenarios end-to-end:

- **Scenario A:** a giant markdown dump that should be one framed doc per `##` heading.
- **Scenario B:** a JSON corpus that nests items under `items[*]` instead of the
  default `documents[*]`.

Both scenarios share the same prereqs and assume you've worked through
[`tutorial.md`](tutorial.md) at least once.

## Prereqs

- chunkshop installed and `CHUNKSHOP_DSN` exported. See
  [`tutorial.md`](tutorial.md#step-1--start-postgres-with-pgvector) for the docker
  one-liner if you don't already have a Postgres with pgvector reachable.
- `chunkshop --version` works from your shell (venv active, or prefix commands with
  `uv run`).
- Run every command in this tutorial from the chunkshop repo root.

## Scenario A — giant markdown dump split by H2

### The fixture

`docs/samples/framer_demo_handbook.md` ships with chunkshop. It concatenates four unrelated
internal-handbook topics — onboarding, code review, incident response, benefits — into
one markdown file separated only by `##` headings. It's a deliberate shape: real
exports from Notion, Confluence, or a "dump our wiki to a file" script look exactly
like this.

Quick look:

```bash
grep '^## ' docs/samples/framer_demo_handbook.md
```

```
## Onboarding
## Code review expectations
## Incident response
## Benefits and PTO
```

Four sections. For retrieval purposes, they should act like four documents.

### The problem — without a framer

If you point chunkshop at this file with no framer config, it treats the whole file as
one document. The hierarchy chunker splits it into sections, but every chunk still
carries `doc_id = 'framer_demo_handbook'`. A query for "what's the code review SLA" can pull
back a chunk that happens to mention PTO because it all lives inside the same raw doc
and the chunker's heading boundaries don't travel through to retrieval as a hard
filter.

You can see this directly — skip the YAML for a second and think about the SQL. The
`doc_id` column is your document grouping. With one raw source file, you get one
`doc_id`. With four logical documents you should get four. A framer is the stage that
makes that true.

### The fix — a `heading_boundary` framer

Create `handbook-framed.yaml`:

```yaml
cell_name: handbook_framed

source:
  type: files
  glob: docs/samples/framer_demo_handbook.md
  id_from: stem
  encoding: utf-8

framer:
  type: heading_boundary
  pattern: '^##\s'
  title_from_heading: true

chunker:
  type: hierarchy
  prefix_heading: true
  min_section_chars: 100

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4

extractor:
  type: none

target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: framed_handbook
  mode: create_if_missing
  source_tag: framed_demo
  hnsw: false
```

The only new block is `framer:`. Everything else matches the patterns you already know
from [`tutorial.md`](tutorial.md).

The framer config says: find every line that starts with `## ` and use each as a
boundary. The text above the first `##` becomes frame 0 (if non-empty). Each `##`
section becomes its own framed document whose `title` is the heading text.

### Run it

```bash
chunkshop ingest --config handbook-framed.yaml
```

You should see something like:

```json
{
  "cell_name": "handbook_framed",
  "docs_processed": 1,
  "chunks_written": 5,
  "wall_seconds": 3.4,
  "error": null
}
```

Notice `docs_processed: 1`. That's the count of **raw** source documents — one file was
read. Framing is invisible to that counter. The framing is visible in the database
(chunk count depends on how aggressively hierarchy decides to split each frame; on this
fixture you'll see one chunk per frame because none of the frames have sub-headings).

### Inspect what changed

```bash
psql "$CHUNKSHOP_DSN"
```

Count distinct doc IDs:

```sql
SELECT COUNT(DISTINCT doc_id) FROM chunkshop_samples.framed_handbook;
-- count
-- -----
--     5
```

Five, not one. (Four `##` sections plus the preamble before the first `##`.) If the
framer did nothing, this would be 1.

Look at the IDs and titles:

```sql
SELECT DISTINCT doc_id, metadata->>'framer' AS framer,
       metadata->>'frame_seq' AS seq
FROM chunkshop_samples.framed_handbook
ORDER BY doc_id;
```

```
         doc_id          |     framer       | seq
-------------------------+------------------+-----
 framer_demo_handbook#0  | heading_boundary | 0
 framer_demo_handbook#1  | heading_boundary | 1
 framer_demo_handbook#2  | heading_boundary | 2
 framer_demo_handbook#3  | heading_boundary | 3
 framer_demo_handbook#4  | heading_boundary | 4
```

Every framed doc gets `<raw_id>#<frame_seq>` as its new ID, stamped with
`metadata.framer = 'heading_boundary'` and a 0-indexed `metadata.frame_seq`. These two
keys are written by every framer, so a downstream consumer can always tell "was this
re-sliced, and by what".

Peek at the chunks for the code-review frame:

```sql
SELECT seq_num, metadata->>'heading' AS heading, length(original_content) AS len
FROM chunkshop_samples.framed_handbook
WHERE doc_id = 'framer_demo_handbook#2'
ORDER BY seq_num;
```

```
 seq_num |           heading           | len
---------+-----------------------------+------
       0 | Code review expectations    | 1974
```

One chunk, belonging entirely to the code-review frame. A retrieval query filtered by
`doc_id = 'framer_demo_handbook#2'` now returns only code-review content. Without the framer
you'd have to filter by `metadata->>'heading' LIKE 'Code review%'` and hope the
chunker's heading tracking was stable.

### Retrieval improvement

This is the point of the exercise. Reuse `query.py` from
[`tutorial.md`](tutorial.md#step-6--your-first-semantic-query), swap the table name to
`framed_handbook`, and run:

```python
QUERY = "what's the code review SLA"
```

```bash
python query.py
```

The top hit is now definitively from `framer_demo_handbook#2` — the code-review frame — with
its chunks ranked by semantic similarity *within* that frame. No cross-contamination
from the PTO section that happens to share tokens like "hours" and "manager". If you
rerun the same query against a non-framed ingest of the same file, you'll typically see
the code-review chunk win too — but other top-5 slots will mix frames, because
retrieval has no doc-boundary signal beyond token similarity.

The framer gives you that signal. What you do with it — filter by `doc_id`, GROUP BY
for MMR, rerank per-frame — is application-level. The framer just makes the grouping
exist.

## Scenario B — nested JSON corpus

### The fixture

`docs/samples/framer_demo_news.json` ships with chunkshop. It wraps its actual items under
`items[*]` rather than the default `documents[*]` that `type: json_corpus` expects:

```json
{
  "meta": {"source": "internal-crawler", "fetched": "2026-04-20"},
  "items": [
    {"id": "news_001", "title": "Q1 earnings beat expectations", "body": "..."},
    {"id": "news_002", "title": "New product launch", "body": "..."}
  ]
}
```

Four items total. The goal is one framed document per item, each with its own title.

### Option A — `json_corpus` with `documents_key`

The `json_corpus` source already accepts a `documents_key` parameter, so if your JSON
is exactly one level deep you can point it at `items` and be done:

```yaml
source:
  type: json_corpus
  path: docs/samples/framer_demo_news.json
  documents_key: items
  id_field: id
  title_field: title
  content_field: body
```

This is the simplest fix and you should prefer it when your JSON shape matches. No
framer needed — identity is still the right default.

### Option B — `type: files` + `JSONPathFramer`

Option A fails when the real items are two or more levels deep, or when you want to
treat the whole JSON blob as one "raw document" and let the framer expand it. This is
also the pattern when the same JSON shape is occasionally delivered as a file drop and
occasionally as an inline string — you move the expansion logic into the framer stage
and keep the source loader trivial.

```yaml
cell_name: news_framed

source:
  type: files
  glob: docs/samples/framer_demo_news.json
  id_from: stem
  encoding: utf-8

framer:
  type: jsonpath
  row_path: items.*
  title_path: title
  body_path: body

chunker:
  type: hierarchy
  prefix_heading: false
  min_section_chars: 40

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4

extractor:
  type: none

target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: framed_news
  mode: create_if_missing
  source_tag: framed_demo
  hnsw: false
```

Key distinction: `type: files`, not `type: json_corpus`. `json_corpus` already iterates
rows, so stacking a framer on top of it would be doing the same job twice. With
`type: files` the source loads the entire JSON blob into `raw.content` as one string,
and the framer iterates.

This is the teaching point: **pick the stage that iterates.** For one-level-deep JSON
with your schema, let the source do it. For anything weirder, load the file as a blob
and let the framer do it.

### Run it

```bash
chunkshop ingest --config news-framed.yaml
```

```json
{
  "cell_name": "news_framed",
  "docs_processed": 1,
  "chunks_written": 4,
  "wall_seconds": 2.1,
  "error": null
}
```

One raw doc in, four chunks out — one per item (each item body is short enough that
hierarchy emits a single chunk per frame with `min_section_chars: 40`).

### Verify

```sql
SELECT doc_id, metadata->>'framer' AS framer,
       substring(original_content, 1, 50) AS preview
FROM chunkshop_samples.framed_news
ORDER BY doc_id;
```

```
       doc_id        |  framer  |                      preview
---------------------+----------+----------------------------------------------------
 framer_demo_news#0  | jsonpath | The company reported revenue of $1.2B, up 15% yea
 framer_demo_news#1  | jsonpath | The data platform beta opened to 100 enterprise c
 framer_demo_news#2  | jsonpath | VP of Engineering Priya Shah announced her depart
 framer_demo_news#3  | jsonpath | The company completed its annual SOC 2 Type II au
```

Four distinct framed documents, each with content pulled from the `body` field of the
corresponding JSON item. The titles — "Q1 earnings beat expectations", etc. — are
stored on each framed `Document` and flow through to the chunker, which uses them as
the logical doc title. You can surface them via `promote_metadata` or by querying the
raw metadata in the chunks table.

## What DocFramer replaces

Before framers existed, people solved this with bespoke Python in their ingest scripts.
The canonical example is `pg-raggraph`, which has a `_split_medical_topics()` function
that splits a medical reference text on a boundary phrase ("About …"):

**Before — a custom splitter glued onto your ingest script:**

```python
import re

def split_medical_topics(text: str) -> list[dict]:
    parts = re.split(r"(?:^|(?<=[.?!]\s))About\s+", text)
    return [
        {"id": f"med_{i}", "content": p}
        for i, p in enumerate(parts)
        if p.strip()
    ]

# ...then feed docs into chunkshop somehow. Either write your own Source, or
# pre-process into individual files, or pickle a list and pass it around.
```

Every project grows its own version of this function. Every version has its own bugs
around trailing whitespace, empty splits, title extraction, and how to thread the
output into whatever ingest tool the team uses.

**After — the same logic as YAML config only:**

```yaml
framer:
  type: regex_boundary
  split_pattern: '(?:^|(?<=[.?!]\s))About\s+'
  title_pattern: 'About\s+([^.?]{3,80})'
```

That's it. No Python. The framer runs inline during ingest, each regex-delimited slice
becomes one framed document, and the first capture group of `title_pattern` becomes
the title.

`pg-raggraph` is the real-world motivator for this feature. Anywhere you see a
`_split_*()` helper function feeding a chunking pipeline, that's a candidate for a
framer.

## Where to go next

- Framer reference with all config fields: see the `HeadingBoundaryFramerConfig`,
  `RegexBoundaryFramerConfig`, and `JSONPathFramerConfig` classes in
  `python/src/chunkshop/config.py`.
- Quickstart with just the copy-paste recipes: [`quickstart-framers.md`](quickstart-framers.md).
- Multi-source retrieval (frame each source differently, union into one table):
  [`tutorial-multi-source.md`](tutorial-multi-source.md).
