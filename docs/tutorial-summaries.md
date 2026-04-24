# Tutorial — summary-embed + hierarchical summaries with skimr and sumy

This walkthrough goes from zero to "fine+coarse rows in the same Postgres
table, queryable by granularity" using the four bundled sample docs in
`docs/samples/`. Expected time: 10-15 minutes on a fresh clone.

## Prerequisites

- Postgres 16 with pgvector installed and reachable. Set
  `CHUNKSHOP_DSN=postgresql://user:pass@host:port/db` in your shell.
- chunkshop cloned locally; skimr cloned as a sibling directory
  (`../extractive_summary/` relative to the chunkshop root).
- Python 3.12, `uv` installed.

```bash
cd chunkshop/python
uv sync --extra dev --extra extractors --extra skimr --extra sumy
```

`[skimr]` uses the sibling repo as an editable path dep — no PyPI install.
`[sumy]` installs sumy + NLTK from PyPI.

Verify both summarizer libraries are reachable through the chunkshop shims:

```bash
uv run python -c "
from chunkshop.summarizers.skimr import summarize as sk
from chunkshop.summarizers.sumy import summarize as su
print('skimr:', sk('The quick brown fox jumps over the lazy dog. The fox is quick and brown.', max_length=40))
print('sumy: ', su('The quick brown fox jumps. The dog slept. Nothing else happened that day.', algorithm='lex_rank', sentences_count=1))
"
```

If either import fails, your extras aren't in sync — re-run `uv sync`.

## Step 1 — ingest with `summary_embed` + skimr (callable mode)

The sample config (`docs/samples/sample-summary-embed.yaml`) uses the `hierarchy`
base chunker, wraps it with `summary_embed`, and hands chunks to skimr:

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: chunkshop.summarizers.skimr
    function: summarize
    kwargs:
      max_length: 300
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 4
```

Ingest the four sample markdown files:

```bash
cd chunkshop
uv --directory python run chunkshop ingest --config docs/samples/sample-summary-embed.yaml
```

First run downloads ~85 MB of model weights to `~/.cache/fastembed/`. Subsequent
runs are instant.

## Step 2 — inspect the rows

```bash
psql $CHUNKSHOP_DSN -c "
SELECT
  doc_id,
  substring(original_content for 80) AS orig_start,
  substring(embedded_content for 80) AS embedded_start,
  metadata->>'summarizer' AS summarizer,
  metadata->>'heading' AS section
FROM chunkshop_samples.summary_embed
ORDER BY doc_id, seq_num
LIMIT 5;
"
```

Expected shape:

- `metadata.summarizer` = `callable` on every row.
- `original_content` is the raw chunk body from the hierarchy chunker (long,
  paragraph-packed, prefixed by the heading via `prefix_heading: true`).
- `embedded_content` is shorter — skimr's extractive summary of that raw body.
- `len(embedded_content) <= len(original_content)` on every row (extractive =
  sentences selected from the source; never longer).

Quick sanity check:

```bash
psql $CHUNKSHOP_DSN -c "
SELECT
  COUNT(*) AS total_rows,
  ROUND(AVG(length(original_content))) AS avg_orig,
  ROUND(AVG(length(embedded_content))) AS avg_embed
FROM chunkshop_samples.summary_embed;
"
```

You should see `avg_embed < avg_orig` — summaries are smaller than originals.

## Step 3 — ingest hierarchical summaries (fine + coarse rows)

Now ingest a second cell into a different table, this time with
`hierarchical_summary` + sumy. Sample config
(`docs/samples/sample-hierarchical.yaml`):

```yaml
chunker:
  type: hierarchical_summary
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: chunkshop.summarizers.sumy
    function: summarize
    kwargs:
      algorithm: lex_rank
      sentences_count: 2
  grouping:
    strategy: section_aware     # one coarse row per heading
target:
  ...
  promote_metadata:
    - path: granularity
      type: text
    - path: group_id
      type: text
```

`section_aware` grouping requires `base.type: hierarchy` — enforced at
config-load.

```bash
uv --directory python run chunkshop ingest --config docs/samples/sample-hierarchical.yaml
```

## Step 4 — fine vs coarse row counts

```bash
psql $CHUNKSHOP_DSN -c "
SELECT granularity, COUNT(*)
FROM chunkshop_samples.hierarchical
GROUP BY granularity
ORDER BY granularity;
"
```

Shape you expect:

- `coarse` rows = number of distinct heading sections across the 4 sample docs
  (one per section).
- `fine` rows = number of base-chunker chunks (the hierarchy chunker splits
  long sections into multiple pieces).
- Every `group_id` in the `coarse` set appears in the `fine` set and vice versa.

Confirm the bijection:

```bash
psql $CHUNKSHOP_DSN -c "
SELECT
  (SELECT COUNT(DISTINCT group_id) FROM chunkshop_samples.hierarchical WHERE granularity='coarse') AS coarse_groups,
  (SELECT COUNT(DISTINCT group_id) FROM chunkshop_samples.hierarchical WHERE granularity='fine') AS fine_groups;
"
```

Both numbers should be equal.

## Step 5 — granularity-filtered semantic query

Embed a query (any tool works; here's a one-liner using chunkshop's embedder):

```bash
uv --directory python run python -c "
from chunkshop.embedders.fastembed import FastembedProvider
from chunkshop.config import FastembedEmbedder
emb = FastembedProvider(FastembedEmbedder(type='fastembed', model_name='Xenova/bge-base-en-v1.5-int8', dim=768, threads=4))
vec = emb.embed(['how does chunkshop handle embedder threads'])[0]
print('[' + ','.join(f'{x:.6f}' for x in vec) + ']')
" > /tmp/qvec.txt
```

Coarse-only query (fast topic match):

```bash
QVEC=$(cat /tmp/qvec.txt)
psql $CHUNKSHOP_DSN -c "
SELECT doc_id, metadata->>'heading' AS section, 1 - (embedding <=> '$QVEC') AS score
FROM chunkshop_samples.hierarchical
WHERE granularity = 'coarse'
ORDER BY embedding <=> '$QVEC'
LIMIT 3;
"
```

Coarse-then-fine (narrow to top-K groups, return fine spans inside them):

```bash
psql $CHUNKSHOP_DSN -c "
WITH top_groups AS (
  SELECT group_id
  FROM chunkshop_samples.hierarchical
  WHERE granularity = 'coarse'
  ORDER BY embedding <=> '$QVEC'
  LIMIT 2
)
SELECT h.doc_id, h.metadata->>'heading' AS section,
       substring(h.original_content for 120) AS excerpt,
       1 - (h.embedding <=> '$QVEC') AS score
FROM chunkshop_samples.hierarchical h
JOIN top_groups tg USING (group_id)
WHERE h.granularity = 'fine'
ORDER BY h.embedding <=> '$QVEC'
LIMIT 5;
"
```

Because `granularity` and `group_id` were promoted via `promote_metadata`, both
queries use real indexed columns instead of jsonb path extraction.

## Step 6 — swap skimr for sumy (one-line YAML diff)

```yaml
summarizer:
  mode: callable
  module: chunkshop.summarizers.sumy      # was: chunkshop.summarizers.skimr
  function: summarize
  kwargs:
    algorithm: text_rank                  # was: max_length: 300
    sentences_count: 3
```

Rerun the ingest into a separate table (`mode: create_if_missing` on a new
`table:` value) and query both side-by-side to A/B the algorithms.

## Step 7 — baseline comparison with passthrough

To prove summaries are helping your corpus, ingest a third table with
`summarizer: {mode: passthrough}` — embeds the raw chunks unchanged. Compare
top-K overlap on the same query against the `summary_embed` table:

```sql
WITH summarized AS (
  SELECT doc_id, seq_num, 1 - (embedding <=> $1) AS score, 1 AS rank_in_src
  FROM chunkshop_samples.summary_embed
  ORDER BY embedding <=> $1 LIMIT 10
),
raw AS (
  SELECT doc_id, seq_num, 1 - (embedding <=> $1) AS score
  FROM chunkshop_samples.baseline_passthrough
  ORDER BY embedding <=> $1 LIMIT 10
)
SELECT
  (SELECT COUNT(*) FROM summarized s JOIN raw r USING (doc_id, seq_num)) AS overlap_count,
  10 AS k;
```

Low overlap with a higher-subjective-quality summarized top-10 = summarization
is helping. If overlap is 10/10, summaries aren't moving the needle on your
corpus — stick with the simpler passthrough.

## Decision matrix recap

| Situation                                              | Mode                                                       |
|--------------------------------------------------------|------------------------------------------------------------|
| Reproducible, zero-dep, fast                           | `callable` with `chunkshop.summarizers.skimr`              |
| Want to A/B LexRank, TextRank, LSA, Luhn                | `callable` with `chunkshop.summarizers.sumy`               |
| Upstream (doctools, ETL) already wrote a `summary`     | `external` with `field: summary`                           |
| LLM-quality abstractive summaries, cost OK             | `callable` with your own module that hits the LLM          |
| Just want to prove summarization matters               | `passthrough`                                              |
| Abstractive without LLM cost (future)                  | `callable` with skimr-neural (when the sibling repo ships) |

## What to read next

- `docs/summaries.md` — reference for every mode, grouping strategy, and query
  pattern.
- `docs/chunkers.md` — base chunker choice matters; see which to wrap.
- `docs/samples/sample-summary-embed.yaml` and
  `docs/samples/sample-hierarchical.yaml` — runnable configs used in this
  tutorial.
