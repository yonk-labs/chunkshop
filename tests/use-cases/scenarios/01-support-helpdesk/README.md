# 01 — Support helpdesk KB search

## Persona

Support engineer at a B2B SaaS company, triaging tickets alongside three other agents on a shared rotation.

## Goal

When a ticket lands, surface the top three KB articles that probably answer it so the agent can paste a link (or the excerpt) back inside thirty seconds. The agent skims — recall@3 is what matters, not MRR@1.

## Why these picks

- **Source: `files`.** KB articles are plain markdown in a docs repo. One file, one article, headings are editorial.
- **Framer: `identity`.** One markdown file = one document; no fan-out needed.
- **Chunker: `hierarchy`.** Every article has an H1 title plus H2 subsections ("Clearing the lockout counter", "Raising the limit"). Prepending the heading to `embedded_content` turns the section title into free framing context — the embedder now sees "API rate limits / Raising the limit / Self-serve up to 2000 req/min ..." instead of a bare paragraph.
- **Embedder: `Xenova/bge-small-en-v1.5-int8` (dim 384).** *This is the load-bearing pick.* Latency dominates retrieval quality in this use case — agents need sub-50ms lookup so suggestions appear while they're still reading the ticket. The ~35 MB model fits comfortably on every agent workstation; 384-dim vectors keep the pgvector index small enough that seq-scan or IVFFlat both stay cheap. You give up ~1 MTEB point versus bge-base, but agents scan three results and pick one, so recall@3 eats the miss.
- **Extractor: `rake_keywords` (top_k=8).** Cheap, zero-ML-dep keyword extraction. The tags end up in the `tags[]` column and double as analytics fodder — "what topics are hot in support this week?" is a `SELECT unnest(tags), count(*) GROUP BY 1` away.
- **Schema-flex: `mode: overwrite`.** The KB is re-ingested on every docs publish; no multi-cell append needed.

## The trade-off we made

Optimized for **speed and cost per query**. Gave up about a point of MTEB score versus bge-base — acceptable because the agent is the second stage and compensates by scanning. If we were running a customer-facing self-service chatbot (no human in the loop) we'd flip to bge-base.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/01-support-helpdesk/config.yaml
```

## What you'd query

```sql
-- Given a ticket subject+body as $1, return top 3 KB articles with headings
SELECT doc_id,
       metadata->>'heading' AS section,
       tags,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.support_helpdesk
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 3;
```
