# `cooccurrence` extractor

**Module**: `chunkshop.extractors.cooccurrence`
**Type**: Extractor
**Ship status**: verified (Tier-1)
**Optional extra**: `chunkshop[extractors]` (rake-nltk) + `chunkshop[lede]` — both imported lazily
**Since**: 0.7.0

## Purpose

Extract weak, undirected, untyped co-occurrence edges from prose chunks —
no spaCy, no LLM, no GPU. The pipeline is two cheap deterministic passes
over the chunk text:

- **rake** extracts the top-K salient keyphrases — these are the **nodes**.
- **lede** selects the salient sentences — these are the co-occurrence
  **windows**.

Any two keyphrases that both appear (word-boundary matched) in the same
salient sentence emit a `co_occurs` candidate. The edge is **weak**
(co-occurrence is not causation), **undirected** (`a`/`b` are
interchangeable, stored canonical `a < b`), and **untyped** (no relation
label — just "these two phrases were near each other").

The `ExtractResult` contract has no edge surface, so the edges ride along
in `metadata["cooccur"]` for a downstream graph consumer (e.g.
pg-raggraph) to materialize into real graph edges. The keyphrases also
surface as `tags` so the node set is queryable directly.

## Config schema

`chunkshop.config.CooccurrenceExtractor` (pydantic v2, `extra="forbid"`):

| Field               | Type                       | Default      | Notes |
|---------------------|----------------------------|--------------|-------|
| `type`              | `Literal["cooccurrence"]`  | **Required** | Discriminator. |
| `top_k`             | `int` (`>= 1`)             | `15`         | Number of rake keyphrases kept as nodes (ranked by RAKE score). |
| `min_chars`         | `int` (`>= 1`)             | `3`          | Drop keyphrases shorter than this many characters. |
| `max_summary_chars` | `int` (`>= 50`)            | `1000`       | Character budget for the lede summary — i.e. the size of the salient-sentence window set. |
| `min_pair_count`    | `int` (`>= 1`)             | `1`          | Drop pairs seen in fewer than N salient sentences. Raise to suppress one-off co-occurrences. |

## Public API

```python
from chunkshop.extractors.cooccurrence import CooccurrenceExtractor
from chunkshop.extractors.result import ExtractResult

class CooccurrenceExtractor:
    def __init__(self, cfg: CooccurrenceExtractorCfg) -> None: ...
    def extract(self, text: str) -> ExtractResult: ...
```

Plain `Extractor` protocol — `extract(text: str) -> ExtractResult`. No
chunk-context, no corpus-level `finalize` phase: edges are computed
per-chunk and the whole node/edge set lives in that one chunk's
`ExtractResult`.

## Behavior contract

`extract(text)`:

1. **Empty / whitespace-only input** returns
   `ExtractResult(tags=[], metadata={"cooccur": []})` — never raises.
2. **Nodes** — rake (`rake_nltk.Rake(min_length=1)`) ranks candidate
   phrases; the extractor keeps phrases with `len(p) >= min_chars`,
   capped to the top `top_k`.
3. **Windows** — lede's `summarize(text, max_length=max_summary_chars)`
   picks salient sentences; the result is split on sentence boundaries
   (`[.!?]` + whitespace).
4. **Matching is word-boundary, not substring.** Each phrase is compiled
   to a `\b<phrase>\b` regex over lowercased text. So the phrase `data`
   does **not** match inside `database`. `\b` still lets multi-word
   phrases match. Substring matching was rejected because it inflates
   co-occurrence noise with false positives.
5. **Pair counting** — for each salient sentence, take the set of
   keyphrases present, and increment a counter for every unordered pair.
   `weight` is the number of salient sentences the pair shares.
6. **Filtering + ordering** — drop pairs with `weight < min_pair_count`;
   sort strongest-first, then alphabetically by `a`, then `b`.
7. **Deterministic, CPU-only.** Same text in → same edges out. No LLM, no
   spaCy, no network after the one-time NLTK corpora download.

### NLTK bootstrap

On construction, the extractor mirrors `rake_keywords`' idempotent NLTK
bootstrap — it ensures `corpora/stopwords`, `tokenizers/punkt`, and
`tokenizers/punkt_tab` are present, downloading any missing ones quietly
(cached to `~/nltk_data/`).

## Outputs

- `tags`: the keyphrase node set (`list[str]`, the kept rake phrases).
- `metadata["cooccur"]`: a list of `{"a", "b", "weight"}` dicts, canonical
  `a < b`, sorted strongest-first.

### Sample output

```yaml
# Input chunk text:
#   "Vector databases index embeddings for nearest neighbor search.
#    pgvector adds HNSW indexes to Postgres for fast vector search."

tags:
  - "vector databases index embeddings"
  - "nearest neighbor search"
  - "fast vector search"
  - "hnsw indexes"
  - "pgvector adds"
```

```json
{
  "cooccur": [
    {"a": "nearest neighbor search", "b": "vector databases index embeddings", "weight": 1},
    {"a": "fast vector search", "b": "hnsw indexes", "weight": 1}
  ]
}
```

(`a`/`b` are alphabetically canonical within each edge; the list is sorted
by descending `weight` then `a`, `b`. Exact phrasing of nodes depends on
rake's ranking over your text.)

## Inputs

- Chunk text (post-chunker, pre-embed). Prose, not code — for code edges
  use [`code_relationships`](extractor-code-relationships.md).

## Errors

| Exception | When |
|-----------|------|
| (none at extract) | Empty/whitespace input returns an empty result rather than raising. |
| `ModuleNotFoundError` | At construction — `rake_nltk` (`[extractors]`) or lede (`[lede]`) not installed. |

## The candidate-edge caveat

These are **candidate** edges, and deliberately the weakest tier:

- **Undirected** — there's no "A causes B" or "A precedes B"; only
  "A and B appeared together."
- **Untyped** — no relation label. The edge type is the literal
  `co_occurs`; the consumer must decide what (if anything) it means.
- **Noisy by nature** — co-occurrence in a salient sentence is a weak
  signal. Two phrases sharing a sentence does not make them related.

Tiering — pair `cooccurrence` with stronger extractors when you need
richer edges:

| Tier | Extractor | Edges |
|------|-----------|-------|
| 1 (this) | `cooccurrence` extractor | undirected, untyped — cheapest, spaCy-free |
| 2 | `lede_spacy` SVO triples (a consolidator `mode`) | directional subject-verb-object |
| 3 | an LLM relation extractor | typed, directional, validated |

**Validate on your own corpus before trusting these as real edges.** Raise
`min_pair_count` to cut one-off pairs, and treat the output as a candidate
set for a downstream consumer to filter, weight, or discard — not as
ground-truth relationships.

## Example: minimal

```yaml
extractor:
  type: cooccurrence
```

The runner stamps `metadata.cooccur` (and `tags`) on each chunk. The edges
live in the `metadata jsonb` column for a downstream graph consumer to read.

## Example: tuned

```yaml
extractor:
  type: cooccurrence
  top_k: 20            # more candidate nodes
  min_chars: 4         # drop short / noisy phrases
  max_summary_chars: 1500
  min_pair_count: 2    # only keep pairs seen in 2+ salient sentences
```

## How it integrates with the pipeline

`CooccurrenceExtractor` is loaded via
`chunkshop.extractors.load_extractor(cfg)` on the discriminator
`type: cooccurrence`. The runner feeds it chunk text (post-chunker,
pre-embed) and merges `r.metadata` into each chunk's `metadata jsonb` —
remember chunker keys win on collision, but `cooccur` is namespaced so it
never collides. To read the edges back out for a graph, query
`metadata->'cooccur'` from the sink table.

## Tests proving the contract

- `tests/chunkshop/test_cooccurrence_extractor.py`:
  - pairs emitted within a salient sentence
  - `weight` counts repeated co-occurrence across sentences
  - `min_pair_count` filters weak edges
  - empty text yields no edges
  - edges sorted by weight then name
  - word-boundary matching avoids substring false positives (`data` vs `database`)
  - word-boundary matching keeps real multi-word co-occurrence
  - real rake + lede path emits pair edges (end-to-end)

## See also

- [`extractors.md`](../extractors.md) — full extractor inventory
- [`quickstart-extractors.md`](../quickstart-extractors.md) — copy-paste recipes
- Reference: [`extractor-code-relationships.md`](extractor-code-relationships.md) — code edges (the directed/typed cousin)
