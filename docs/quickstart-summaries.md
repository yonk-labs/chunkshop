# Quickstart: `summary_embed` + `hierarchical_summary`

Recipe card for summary-layer chunkers. Full walkthrough in
[`tutorial-summaries.md`](tutorial-summaries.md); detailed reference in
[`summaries.md`](summaries.md).

## When to pick which

| Your goal | Use |
|---|---|
| Embed cleaner, more topical text while keeping raw for audit | `summary_embed` |
| Match on coarse summaries but return fine-grained chunks | `hierarchical_summary` |
| Avoid an LLM-in-the-ingest-path | `summary_embed` + lede (extractive) or `passthrough` baseline |
| Pre-computed summaries in your source metadata | `summary_embed` with `external` mode |

## Summarizer modes

The `summarizer` field is a discriminated union. Pick one of three modes
depending on where the summary comes from:

### `external` — pre-computed on the source side

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: external
    field: summary   # pulls from doc.metadata["summary"] per source doc
```

Use when your upstream (another service, an LLM batch, a human editor) has
already written a summary into the source metadata. chunkshop's ingest
never calls a summarizer in this mode — it just pulls the field.

### `callable` — import a summarizer module

```yaml
chunker:
  type: summary_embed
  base:
    type: sentence_aware
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    function: summarize
    kwargs:
      max_length: 300
```

Lazy-imports `chunkshop.summarizers.lede.summarize` at first use. Requires
the `[lede]` pip extra (which pulls the sibling `extractive_summary`
repo as a path dep).

For sumy (pluggable algorithms — LexRank, TextRank, LSA):

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: chunkshop.summarizers.sumy
    function: summarize
    kwargs:
      algorithm: lex_rank     # lex_rank | text_rank | lsa | luhn | kl | edmundson
      sentences_count: 3
      language: english
```

Requires the `[sumy]` extra.

For any user-wired module exposing `summarize(text, **kwargs) -> str`
(e.g., an LLM API wrapper):

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: my_project.summarizers
    function: gpt4_summarize
    kwargs:
      model: gpt-4o-mini
      max_tokens: 200
```

chunkshop never imports `my_project.summarizers` at module level — the
dispatch happens only when the chunker runs.

### `passthrough` — baseline (raw chunk as the summary)

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: passthrough
```

No actual summarization. Both `original_content` and `embedded_content`
hold the same text. Useful as a baseline cell when running a bakeoff
against "real" summarizer modes to quantify their lift.

## `hierarchical_summary` — fine + coarse rows

Emits base rows (`metadata.granularity = "fine"`) plus extra coarse
summary rows (`metadata.granularity = "coarse"`) linked by
`metadata.group_id`. Retrieval can match on coarse, then return all fine
rows in the group.

### `fixed_n` grouping (default — every N consecutive base chunks)

```yaml
chunker:
  type: hierarchical_summary
  base:
    type: hierarchy
  grouping:
    strategy: fixed_n
    n: 5
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    function: summarize
```

### `word_budget` grouping (group up to N words)

```yaml
chunker:
  type: hierarchical_summary
  base:
    type: sentence_aware
  grouping:
    strategy: word_budget
    max_words: 2000
  summarizer:
    mode: passthrough
```

### `section_aware` grouping (one coarse row per heading section)

```yaml
chunker:
  type: hierarchical_summary
  base:
    type: hierarchy   # REQUIRED for section_aware
  grouping:
    strategy: section_aware
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    function: summarize
```

Config-time validation refuses this if `base.type` isn't `hierarchy`.

## Query patterns

Pair with [schema-flex `promote_metadata`](tutorial-multi-source.md) to
lift `granularity` and `group_id` into indexable columns:

```yaml
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: hierarchical
  mode: overwrite
  promote_metadata:
    - {path: granularity, type: text}
    - {path: group_id, type: text}
```

Then at query time:

```sql
-- Match on coarse, return all fine chunks in the winning group
WITH top_coarse AS (
  SELECT group_id FROM mydata.hierarchical
  WHERE granularity = 'coarse'
  ORDER BY embedding <=> $query_vec
  LIMIT 1
)
SELECT * FROM mydata.hierarchical
WHERE granularity = 'fine' AND group_id = (SELECT group_id FROM top_coarse);
```

## What this replaces

Before `summary_embed`, wiring a summarizer into ingest meant pre-processing
the corpus with your own script, writing rows with already-summarized text,
and losing the raw chunk for audit. `summary_embed` keeps both columns
(`original_content` for grep / fact-match, `embedded_content` for
similarity) and makes the summarizer a YAML choice, not a fork in your
ingest code.

## See also

- [`tutorial-summaries.md`](tutorial-summaries.md) — end-to-end walkthrough
  with lede + sumy, fine+coarse query patterns.
- [`summaries.md`](summaries.md) — full reference: all three modes, all
  three grouping strategies, decision matrix across lede / sumy / external
  / callable.
- [`samples/sample-summary-embed.yaml`](samples/sample-summary-embed.yaml)
  and [`samples/sample-hierarchical.yaml`](samples/sample-hierarchical.yaml) —
  runnable cells.
