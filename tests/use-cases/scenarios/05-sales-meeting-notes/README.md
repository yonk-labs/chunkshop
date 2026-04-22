# 05 — Sales meeting notes

## Persona

RevOps analyst at a mid-market SaaS company selling into both LATAM and North America. Reps drop their meeting notes into a shared system after every call.

## Goal

Support queries of the form *"find last quarter's pricing-objection conversations across all reps and all languages"* — surface the moments when a customer pushed back on cost, regardless of which rep was on the call or what language the objection was raised in.

## Why these picks

- **Source: `json_corpus`.** Meeting notes land in a structured store — `{id, rep, customer, content, language_hint}`. One row per call.
- **Framer: `identity`.** One row = one meeting. The content field is the rep's free-text note.
- **Chunker: `neighbor_expand` (window=1) wrapping `sentence_aware`.** *This is the load-bearing pick.* Meeting notes lack headings — sentence-aware is the right base chunker because it splits on sentence boundaries and packs paragraphs. But a bare sentence-aware chunk can strand a key phrase without its context: *"that's too expensive"* is meaningless alone, it only matches the "pricing objection" query when the turn before (what was being priced) and after (the rep's response) travel with it. `neighbor_expand` with window=1 splices each chunk together with its ±1 neighbors into `embedded_content` before embedding, so the embedder sees the framed context; `original_content` stays the bare chunk for grep-match and audit.
- **Embedder: `Xenova/bge-base-en-v1.5-int8` (dim 768).** The reliable default. No long-context need — neighbor_expand chunks stay well under 512 tokens at the configured `max_chars: 600` (base chunk) × 3 (self + 2 neighbors).
- **Extractor: `composite(lang_detect + rake_keywords)`.** `lang_detect` so queries can scope to English-only, Spanish-only, or all. `rake_keywords` surfaces terms that double as analytics pivots ("most-mentioned competitor names this quarter").
- **Schema-flex: `mode: overwrite` + `promote_metadata`.** Promotes `language` → `text` (column `language`) so the analyst's filter doesn't traverse jsonb on every row.

## The trade-off we made

`embedded_content` is roughly 2–3× the size of the bare chunk because neighbors are glued in. That doubles embedding storage and costs a little more embedding compute. **In exchange**: context-grounded retrieval on messy prose without switching to a long-context embedder. If the meeting notes were 10 KB each we'd reach for nomic-Q instead; at a few paragraphs per call, neighbor_expand is the cheaper win.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/05-sales-meeting-notes/config.yaml
```

## What you'd query

```sql
-- "Pricing objections in the last quarter, any language" — multilingual fleet query
SELECT doc_id,
       language,
       left(original_content, 300) AS bare_chunk,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.sales_meeting_notes
WHERE language IN ('es', 'en')
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 10;
```
