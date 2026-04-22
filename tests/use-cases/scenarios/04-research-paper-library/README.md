# 04 — Research paper library

## Persona

ML researcher curating a private library of published papers — a few hundred arXiv drops plus the occasional conference PDF converted to markdown.

## Goal

Support queries of the form *"show me all papers Google DeepMind authored on retrieval-augmented generation in 2024 or later"* — a filter-by-organization combined with a text similarity ranking. The researcher wants to scope to a lab or affiliation first, then rank by topical match inside that scope.

## Why these picks

- **Source: `json_corpus`.** The library is already structured — one JSON row per paper, with `{id, title, abstract, body, authors, year, affiliation}`. Whatever parsed the arXiv metadata + tex body lives upstream; chunkshop consumes the clean corpus.
- **Framer: `identity`.** One JSON row = one paper. The content field is already the paper body with markdown headings.
- **Chunker: `hierarchy`.** Papers have clean `# Abstract`, `# Introduction`, `# Methods`, `# Results` structure — sometimes `# Related Work`, `# Ablations`, `# Limitations`. The hierarchy chunker slices cleanly at those boundaries, and prefixing the section heading to `embedded_content` adds free framing context ("this is a methods paragraph, not an intro paragraph") that improves retrieval on queries like *"ablation on dropout rate"*.
- **Embedder: `Xenova/bge-base-en-v1.5-int8` (dim 768).** The reliable default — shipped winner in chunkshop's factorial bench at MRR 0.964. No exotic long-context need here; abstracts and sections fit well inside 512 tokens.
- **Extractor: `composite(spacy_entities + lang_detect)`.** spaCy extracts `[ORG, PERSON, GPE, DATE]` — the ORG list is what drives the filter-by-affiliation query. `lang_detect` because papers from non-English-first venues sometimes carry mixed-language sections we want to scope out.
- **Schema-flex: `mode: overwrite` + `promote_metadata`.** Promotes `entities.ORG` → `text[]` (column `entities__org`) and `language` → `text`. This is what makes the example query work without a query-time NER call — plain Postgres array membership + pgvector similarity in one SELECT.

## The trade-off we made

spaCy NER costs ~50 MB of model weight plus a few seconds per paper at ingest. For a library of 500+ papers, that's a one-afternoon cost that pays back every time a researcher writes a filter-by-lab query — which is most queries. Without the promote, the same filter requires walking jsonb metadata on every row or running NER at query time; both scale worse than a `text[]` column with a GIN index.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/04-research-paper-library/config.yaml
```

## What you'd query

```sql
-- "Google DeepMind papers on retrieval-augmented generation"
-- Filter-by-affiliation first (cheap, indexable) then similarity-rank inside that scope.
SELECT doc_id,
       metadata->>'heading' AS section,
       entities__org,
       left(original_content, 240) AS excerpt,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.research_paper_library
WHERE language = 'en'
  AND 'Google DeepMind' = ANY(entities__org)
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 10;
```
