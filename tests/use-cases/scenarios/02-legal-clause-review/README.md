# 02 — Legal clause review

## Persona

Paralegal on a corporate legal team, reviewing a 500-contract archive that has grown by acquisition and integration over the last decade.

## Goal

Given a legal concept in plain English — *"force majeure language that explicitly covers pandemics"*, *"Delaware choice-of-law provisions in vendor MSAs"* — return the handful of contracts and specific clauses that match, with the counterparty names as a filterable column so the paralegal can narrow to one organization without an LLM in the query path.

## Why these picks

- **Source: `json_corpus`.** The contract archive already lives in a structured export — one row per contract, with `{id, title, content, parties}`. No need to re-parse from PDFs at ingest time.
- **Framer: `identity`.** One JSON row = one document; the content is already the contract body.
- **Chunker: `sentence_aware`.** Contracts rarely have reliable markdown heading structure — they have numbered sections, bolded inline headers, or nothing. Sentence-aware with the default 2000-char max packs paragraphs naturally; most clauses fit in one chunk, long ones split at sentence boundaries.
- **Embedder: `Xenova/bge-base-en-v1.5-int8` (dim 768).** *This is the load-bearing pick.* Quality dominates cost in this use case — miss-at-rank-1 wastes billable time and risks missing a clause that matters. Contract prose is dense, formal, and vocabulary-heavy; the +3–5 MTEB points versus bge-small translate directly to correct-clause retrieval on jargon-rich queries.
- **Extractor: `composite(spacy_entities + lang_detect)`.** spaCy runs NER once at ingest with the `[ORG, PERSON, DATE, GPE]` label whitelist — a few seconds per contract, paid once. `lang_detect` catches stray non-English clauses (subsidiary agreements, translated exhibits) so queries can scope to English-language chunks without exploding on tokenization of a French addendum.
- **Schema-flex: `mode: overwrite` + `promote_metadata`.** Promotes `entities.ORG` to a `text[]` column (`entities__org`) and `language` to `text`. This is the whole point: the paralegal can now write `WHERE 'Acme Corporation' = ANY(entities__org)` alongside a similarity search, with a plain Postgres index, and no NER at query time.

## The trade-off we made

Optimized for **precision and filterability**. Spent ~50 MB of extra model weight (bge-base over bge-small) plus a one-time NER pass at ingest (~seconds per contract). In exchange: precise clause retrieval on formal prose, and first-class SQL filters on organization name and language without any query-time ML. For an archive that's queried hundreds of times a day by billable staff, the ingest cost pays back inside the first afternoon.

## How to run it

```bash
chunkshop ingest --config tests/use-cases/scenarios/02-legal-clause-review/config.yaml
```

## What you'd query

```sql
-- "Force majeure clauses that mention pandemics" — filtered to contracts with Acme as a party
SELECT doc_id,
       entities__org,
       left(original_content, 280) AS excerpt,
       embedding <=> chunkshop_embed($1) AS distance
FROM chunkshop_use_cases.legal_clause_review
WHERE language = 'en'
  AND 'Acme Corporation' = ANY(entities__org)
ORDER BY embedding <=> chunkshop_embed($1)
LIMIT 10;
```
