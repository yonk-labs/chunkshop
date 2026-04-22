# 03 — Dev API docs RAG

## Persona

DevTools PM building a RAG chatbot over the public API reference — the "ask the docs" box on the developer portal.

## Goal

User types a question — *"how do I paginate results from the /v2/orders endpoint?"*, *"what error code do I get for an expired API key?"* — the bot pulls the right doc page as context and the LLM answers from it. Landing on a **complete answer** matters more than landing on a **short snippet**; the LLM can summarize, it cannot un-truncate.

## Why these picks

- **Source: `files`.** API reference is static markdown checked into the docs repo. One file per concept (auth, pagination, errors). No database, no CMS.
- **Framer: `identity`.** One markdown file = one document. The page itself is the retrieval unit we want the LLM to reason over.
- **Chunker: `sentence_aware` with `doc_type: prose` and `max_chars: 5000`.** *The raised `max_chars` is the load-bearing knob.* Default is 2000. We bumped to 5000 because API doc pages routinely carry 2–3 KB of explanatory prose plus a 1–2 KB code example — splitting the code away from the explanation produces a chunk that looks retrievable but gives the LLM half the answer. With a long-context embedder (next pick) we have the token budget to keep them together.
- **Embedder: `nomic-ai/nomic-embed-text-v1.5-Q` (dim 768, 8192-token context).** *The other load-bearing pick.* bge variants max out at 512 tokens — roughly 2 KB of English. A full API doc page at that limit splits at a semantic boundary and retrieval lands the chatbot on a fragment. Nomic-Q accepts up to 8192 tokens, which covers a full page with room to spare; retrieval returns the complete section the LLM needs for a grounded answer.
- **Extractor: `rake_keywords` (top_k=10).** Keywords land in `tags[]` and double as navigation metadata ("show me all pages tagged `pagination`").
- **Schema-flex: `mode: overwrite`.** Docs re-ingest on every publish; one fresh table per publish.

## The trade-off we made

On chunkshop's own 772-doc legal QA bench, nomic-Q underperforms bge-base-int8 (MRR 0.911 vs 0.964). That bench uses short documents where bge's 512-token window is never the bottleneck. API doc retrieval is the opposite shape — long pages, structured context, few documents — so long-context pays off. **Run the bench on your own corpus before committing.** The recommendation here is: if your pages routinely exceed 512 tokens and the consumer is an LLM (not a human skimming snippets), nomic-Q is worth the MTEB delta.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/03-dev-api-docs-rag/config.yaml
```

## What you'd query

```sql
-- User query $1 from the chatbot; return the 2 most-relevant doc pages as LLM context
SELECT doc_id,
       original_content,
       tags,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.dev_api_docs_rag
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 2;
```
