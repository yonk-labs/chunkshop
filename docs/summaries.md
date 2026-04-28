# Summary-embed + hierarchical summary

chunkshop ships two chunker wrappers that improve retrieval quality by changing
what gets embedded without changing what gets stored.

- **`summary_embed`** — wrap any base chunker; embed a summary of each chunk
  (what matters), keep the raw chunk (what you return).
- **`hierarchical_summary`** — emit base rows (`granularity = "fine"`) AND coarse
  summary rows (`granularity = "coarse"`) in the same table, linked by a shared
  `group_id` for fine-plus-coarse retrieval.

Both consume an origin-agnostic summarizer contract — external source-column,
callable module (lede, sumy via shim, any user-wired Python function), or
passthrough baseline. chunkshop core imports **zero** summarizers at module
load; the callable path only imports what your YAML asks for.

## Why summaries at embedding time

Retrieval quality comes from what the embedder sees. Raw chunks often include
filler, boilerplate, and off-topic paragraphs that dilute the vector. Embedding
a focused summary instead:

- **Denser signal.** The vector represents what the chunk is about, not every
  filler sentence it happens to contain.
- **Works with any embedder.** No fine-tuning or special model needed.
- **Original stays queryable.** `original_content` still holds the raw text for
  grep, fact-match, audit, citation, and model-returned text.

The trade-off: summaries take time and can drop useful detail. The decision
matrix below covers when it's worth it.

## `summary_embed` — one row per base chunk, with summary embedded

Drop-in wrapper over any base chunker. `original_content` keeps the raw chunk;
`embedded_content` becomes the summary.

```yaml
chunker:
  type: summary_embed
  base:
    type: hierarchy          # or sentence_aware / fixed_overlap / neighbor_expand
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    function: summarize
    kwargs:
      max_length: 300
```

What you get in each row:

| Field               | Contents                                                    |
|---------------------|-------------------------------------------------------------|
| `original_content`  | Raw chunk from the base chunker (unchanged)                 |
| `embedded_content`  | Summary returned by the summarizer                          |
| `embedding`         | Vector of `embedded_content` (the summary)                  |
| `metadata.summarizer` | `"external" \| "callable" \| "passthrough"` (traceability) |

## `hierarchical_summary` — fine + coarse rows in one table

Emits (1) every base chunk with `granularity = "fine"` AND (2) one extra row
per group with `granularity = "coarse"` whose `embedded_content` is the summary
of the concatenated group. Both share a `group_id` like `{doc_id}::g{N}`.

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
    strategy: section_aware
```

Query patterns:

```sql
-- Coarse-only retrieval (fast topic match)
SELECT doc_id, original_content, 1 - (embedding <=> $1) AS score
FROM chunkshop_samples.hierarchical
WHERE metadata->>'granularity' = 'coarse'
ORDER BY embedding <=> $1
LIMIT 10;

-- Fine-only retrieval (precise span match)
SELECT doc_id, original_content, 1 - (embedding <=> $1) AS score
FROM chunkshop_samples.hierarchical
WHERE metadata->>'granularity' = 'fine'
ORDER BY embedding <=> $1
LIMIT 10;

-- Coarse-then-fine: find top-K groups by coarse match, then return fine rows
WITH top_groups AS (
  SELECT metadata->>'group_id' AS gid
  FROM chunkshop_samples.hierarchical
  WHERE metadata->>'granularity' = 'coarse'
  ORDER BY embedding <=> $1
  LIMIT 5
)
SELECT h.doc_id, h.original_content
FROM chunkshop_samples.hierarchical h
JOIN top_groups tg ON h.metadata->>'group_id' = tg.gid
WHERE h.metadata->>'granularity' = 'fine'
ORDER BY h.embedding <=> $1;
```

For fast filtering, promote `granularity` and `group_id` to real columns via
`promote_metadata` on the target — see the schema-flexibility docs. The
sample config (`docs/samples/sample-hierarchical.yaml`) does exactly this.

## Summarizer modes

chunkshop's summarizer config is a discriminated union with three modes.

### `external` — pull from source-document metadata

Best when upstream (JSON corpus, preprocessor, human editor) already computed
the summary.

```yaml
summarizer:
  mode: external
  field: summary        # name of the metadata field on each Document
```

Fails loudly on any document missing the field — no silent fallback to raw.

### `callable` — import a user-specified module

Best for programmatic summarizers. The contract is
`summarize(text: str, **kwargs) -> str`. Import happens lazily on first chunk;
failures raise a clear "install and retry" message.

```yaml
summarizer:
  mode: callable
  module: chunkshop.summarizers.lede   # or sumy, or your own module
  function: summarize
  kwargs:
    max_length: 300
```

Any module exposing a function matching the contract works without code
changes in chunkshop.

### `passthrough` — baseline for A/B

Summary = original chunk. Use this to A/B test whether summarization is
actually helping your corpus.

```yaml
summarizer:
  mode: passthrough
```

## Grouping strategies (hierarchical_summary only)

### `fixed_n` — every N base chunks form one group (default, N=5)

Simplest. Good when chunks are roughly equal in size.

```yaml
grouping:
  strategy: fixed_n
  n: 5
```

### `word_budget` — accumulate chunks up to M words per group

Best when base-chunk sizes vary. Produces groups of roughly equal word count.

```yaml
grouping:
  strategy: word_budget
  max_words: 2000
```

### `section_aware` — one group per heading section

Requires `base.type: hierarchy`. Each original heading becomes one group, so
the coarse row represents the whole section. Enforced at config-load — wrong
base type fails with a clear error before you run the cell.

```yaml
base:
  type: hierarchy
grouping:
  strategy: section_aware
```

## Summarizer decision matrix

| Library / mode   | Determinism  | Speed        | Install cost  | Quality                             | When to pick                                                                 |
|------------------|--------------|--------------|---------------|-------------------------------------|------------------------------------------------------------------------------|
| **lede**        | Deterministic | sub-ms/chunk | zero-dep, sibling repo | Solid extractive (TF-IDF + position + length) | Default for reproducibility, speed, or air-gapped runs                       |
| **sumy**         | Deterministic per algo | ms/chunk    | `pip install chunkshop[sumy]` (pulls NLTK) | Multiple algorithms (LexRank, TextRank, LSA, Luhn, KL, Edmundson) | Want to A/B algorithms, or LexRank/TextRank specifically                     |
| **lede-neural** | Deterministic (seed)  | tens-ms/chunk | ONNX model download (when released) | Abstractive; can rephrase, compress harder | Want abstractive quality without LLM cost (available when sibling repo ships) |
| **external**     | Whatever upstream produced | free at ingest | upstream tool owns this | Whatever your pipeline wrote | Upstream already summarized (e.g., yonk-doctools adds a `summary` field)     |
| **callable (LLM)** | Non-deterministic | 100 ms – seconds | API credentials + cost | Highest — abstractive with world knowledge | Quality matters more than cost; small corpus; batch one-time ingest          |
| **passthrough**  | n/a          | free         | zero           | Baseline                            | A/B test — is summarization helping at all?                                  |

Rules of thumb:

- Start with `lede` + `hierarchy` base. Ship. Measure.
- If retrieval quality is short, try `sumy` with `algorithm: text_rank` or
  `lex_rank` before reaching for an LLM.
- If upstream (doctools, ETL) already produces summaries, use `external` — zero
  ingest-time cost.
- Use `passthrough` to prove summaries help on your corpus before investing in
  the tuning.

## Retrieval-side considerations

chunkshop writes the rows; how you query them is yours. Some patterns that
work well with these wrappers:

- **`summary_embed` alone** is the simplest win. The embedded vector is cleaner;
  you retrieve the `original_content` for the model prompt or display. No
  query-side changes.
- **`hierarchical_summary` coarse-only** — for "what's this corpus about?"
  questions or cross-document topic matches, query with
  `WHERE granularity = 'coarse'`. Smaller index, higher recall for topical
  queries.
- **`hierarchical_summary` coarse-then-fine** — top-K coarse match narrows to
  promising sections; then fine query within those sections returns precise
  spans. Two SQL calls, much better precision than naive fine-only.
- **Fusion** — combine coarse + fine scores (e.g., re-rank fine hits by their
  coarse match score). Out of chunkshop's scope; see retrieval-layer libraries.

### Promoting `granularity` and `group_id` to columns

For large tables, filtering on `metadata->>'granularity' = 'coarse'` is fine but
slow. Promote both fields via `promote_metadata` to get real indexed columns:

```yaml
target:
  promote_metadata:
    - path: granularity
      type: text
    - path: group_id
      type: text
```

Now `WHERE granularity = 'coarse'` hits a btree index rather than parsing
jsonb per row.

## Quickstart recipes (SC-010)

Drop-in YAML snippets pointing at `docs/samples/*-*.md`. All three assume an
existing Postgres reachable via `CHUNKSHOP_DSN`.

### External (zero ingest-time cost, upstream-computed summaries)

```yaml
cell_name: quickstart_external
source: {type: json_corpus, path: my-corpus-with-summaries.json}
chunker:
  type: summary_embed
  base: {type: sentence_aware}
  summarizer: {mode: external, field: summary}
embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
target: {dsn_env: CHUNKSHOP_DSN, schema: qs, table: ext_sum, mode: create_if_missing}
```

### Callable (lede, zero-dep, sub-ms/chunk)

```yaml
cell_name: quickstart_lede
source: {type: files, glob: docs/samples/*-*.md, id_from: stem}
chunker:
  type: summary_embed
  base: {type: hierarchy}
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    function: summarize
    kwargs: {max_length: 300}
embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
target: {dsn_env: CHUNKSHOP_DSN, schema: qs, table: lede_sum, mode: create_if_missing}
```

### Passthrough (baseline — embed raw chunks)

```yaml
cell_name: quickstart_baseline
source: {type: files, glob: docs/samples/*-*.md, id_from: stem}
chunker:
  type: summary_embed
  base: {type: hierarchy}
  summarizer: {mode: passthrough}
embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
target: {dsn_env: CHUNKSHOP_DSN, schema: qs, table: baseline, mode: create_if_missing}
```

Run any of these via `chunkshop ingest --config <file>.yaml`. A/B by querying
both tables with the same query vector and comparing top-K overlap.

## Install

```bash
# base install + lede sibling path dep + sumy
cd chunkshop/python
uv sync --extra dev --extra extractors --extra lede --extra sumy

# minimal — lede only (fast path)
uv sync --extra lede
```

Both extras are opt-in; the base chunkshop install has no summarizer libraries.

## See also

- `docs/tutorial-summaries.md` — walkthrough from install to live Postgres query.
- `docs/samples/sample-summary-embed.yaml` — runnable `summary_embed` config.
- `docs/samples/sample-hierarchical.yaml` — runnable `hierarchical_summary` config.
- `docs/chunkers.md` — base chunker reference (hierarchy / sentence_aware / fixed_overlap / neighbor_expand).
