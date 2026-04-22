# 06 — E-commerce catalog search

## Persona

E-commerce merchandiser running product search on an outdoor-gear storefront.

## Goal

Customer types in the search box — *"waterproof hiking boots men size 12"*, *"ultralight two person tent"* — and the site returns the ten most-similar products. Each hit renders with one or two UI badges (*"waterproof"*, *"backpacking"*) so the customer can tell at a glance what makes each result a match.

## Why these picks

- **Source: `files`.** One JSON file holds the catalog export — the nightly CMS dump lands on disk and chunkshop re-ingests.
- **Framer: `jsonpath` with `row_path: products.*`, `title_path: name`, `body_path: description`.** *This is the load-bearing pick on the framer axis.* One catalog file contains many products; without a framer, chunkshop would see one giant document. The jsonpath framer walks `products.*` and fans out into 8 framed documents, each with its own `title` (the product name) and `body` (the description). Downstream chunker and embedder treat them as independent documents.
- **Chunker: `sentence_aware` with the default `max_chars: 2000`.** Product descriptions are short (a paragraph or two), so typically one chunk per product. The sentence-aware packer handles the occasional long description cleanly without needing hierarchy.
- **Embedder: `Xenova/bge-small-en-v1.5-int8` (dim 384).** *This is the load-bearing pick.* Search-as-you-type means sub-50ms latency is table stakes. ~35 MB model, 384-dim vectors keep the pgvector HNSW index small enough to stay fast even at catalog scale (100k+ products), and the int8 weights keep embedding compute cheap when the catalog refreshes nightly.
- **Extractor: `keybert_phrases` (top_k=5).** The extracted phrases land in the `tags[]` column and drive the UI badge rendering. KeyBERT (over RAKE) because phrases like "waterproof hiking boot" and "internal frame backpack" hold together as bigrams — RAKE would split them into lower-signal unigrams.
- **Schema-flex: `mode: overwrite`.** Catalog reloads nightly; fresh table each time is the simplest pattern.

## The trade-off we made

On our bench, bge-small-int8 lands ~3 MTEB points behind bge-base. For product search the embedder is rarely the bottleneck — catalog queries are keyword-heavy ("boots", "size 12", "waterproof") and a BM25 or pgvector-plus-tsvector-hybrid typically wins regardless of embedder. The small-int8 pick optimizes for **cheap vectors that don't slow down the hot path**. **Bench on your own catalog before committing** — if your top queries are semantic ("gift for someone who hikes alone in cold weather"), bge-base earns its keep.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/06-ecommerce-catalog/config.yaml
```

## What you'd query

```sql
-- User search $1 from the product search box — return 10 results with badges
SELECT doc_id,
       original_content,
       tags AS badges,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.ecommerce_catalog
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 10;
```
