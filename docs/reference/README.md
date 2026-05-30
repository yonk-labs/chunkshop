# Reference docs — index

Per-surface reference documentation for everything chunkshop's
2026-05-25 session shipped. One file per public surface; each follows
the same template (purpose / config / API / contract / examples /
tests / see also).

For a single-document agent-shaped digest, see
[`docs/AGENT_REFERENCE.md`](../AGENT_REFERENCE.md).

For task-oriented walkthroughs, see [`docs/cookbook/`](../cookbook/).

## Sources (connectors + core ingest paths)

| Surface | Tier | One-liner |
|---|---|---|
| [`source-github`](source-github.md) | verified connector | Walk a GitHub repo via REST + PAT; cursor sync via `/compare`. |
| [`source-gdrive`](source-gdrive.md) | verified connector | Ingest text-shaped Drive files via OAuth + httpx; cursor sync via Changes API. |
| [`source-blob`](source-blob.md) | verified connector | S3-compatible blob store (R2, GCS, MinIO, OCI) via access key + ETag fingerprint. |
| [`source-rss`](source-rss.md) | verified connector | RSS / Atom feed via feedparser; GUID fingerprint. |
| [`source-http`](source-http.md) | core | Depth-bounded URL crawl with ETag/Last-Modified conditional GETs. |
| [`source-pg-table`](source-pg-table.md) | core | Postgres table reader with tuple cursor `(after_ts, after_id)`. |
| [`source-s3-core`](source-s3-core.md) | core | Lightweight S3 reader; `{key:etag}` cursor. |
| [`experimental-connectors`](experimental-connectors.md) | experimental stubs | 23 connectors registered but not yet implemented. |

## Chunkers

| Surface | One-liner |
|---|---|
| [`chunker-code-aware`](chunker-code-aware.md) | Python AST chunker (stdlib `ast`). One chunk per top-level def/class. |
| [`chunker-symbol-aware`](chunker-symbol-aware.md) | Multi-language (Python, Java, Go, TypeScript, JavaScript, Rust, C, C++, C#, Ruby) — all via real tree-sitter grammars in the `[code]` extra (regex fallback when the extra is absent). |

## Embedders

| Surface | One-liner |
|---|---|
| [`embedder-openai`](embedder-openai.md) | Opt-in `type: openai` remote embedder for any OpenAI-compatible `/v1/embeddings` endpoint (OpenAI / Voyage / Mistral / Together / local TEI/vLLM/Ollama). `fastembed` stays the default. |

## Extractors

| Surface | One-liner |
|---|---|
| [`extractor-code-summary`](extractor-code-summary.md) | Per-chunk natural-language summary. Backends: `lede`, BYO `callable`, `first_n_sentences`. |
| [`extractor-code-relationships`](extractor-code-relationships.md) | Per-chunk callees + corpus-level `CALLS`/`INHERITS`/`IMPLEMENTS` edges via `finalize()`. |
| [`extractor-cooccurrence`](extractor-cooccurrence.md) | Tier-1 spaCy-free `co_occurs` edge candidates: rake keyphrases co-occurring in lede-salient sentences → `metadata["cooccur"]`. |

## Consolidators (agent-memory fact extraction)

| Surface | One-liner |
|---|---|
| [`consolidator-fact-extractors`](consolidator-fact-extractors.md) | Bundled `lede` (salient propositions) + `lede_spacy` (SVO triples) consolidator modes for the `consolidation` chunker; `confidence_floor` + summarizer slot. |

## File parsers

| Surface | One-liner |
|---|---|
| [`parsers`](parsers.md) | Per-extension parser layer for `FilesSource` (PDF/DOCX/PPTX/XLSX/HTML behind optional extras). |

## OAuth

| Surface | One-liner |
|---|---|
| [`oauth-google`](oauth-google.md) | Concrete Google OAuth 2.0 provider with refresh-token preservation. |
| [`oauth-protocols`](oauth-protocols.md) | `OAuthProvider` + `OAuthTokenStorage` Protocols, `OAuthTokens` dataclass, `proactive_refresh` helper, `MockOAuthProvider`. |

## Utilities

| Surface | One-liner |
|---|---|
| [`utility-codeparse`](utility-codeparse.md) | `parse_file` / `parse_text` / `Symbol` / `CallSite` / `ParseResult` / `build_fqn` / `code_symbol_node_id`. |
| [`utility-testing`](utility-testing.md) | `merge_cursor` / `assert_cursor_advances` / `assert_idempotent_on_re_emit` / `mock_oauth_provider` fixture. |

## CLI

| Surface | One-liner |
|---|---|
| [`cli-admin`](cli-admin.md) | `chunkshop init` / `validate` / `prefetch` — scaffold a cell, check a config, warm the embedder cache. |
| [`cli-search`](cli-search.md) | `chunkshop search --query …` (hybrid retrieval) with `--by-symbol`, `--compress`, `--include-facts`. |
| [`cli-fact-search`](cli-fact-search.md) | `chunkshop fact-search --query …` returns `kind='fact'` rows with their chunk→doc breadcrumb. |
| [`cli-impact-of`](cli-impact-of.md) | `chunkshop impact-of --fqn …` walks the `code_edges` table for callers/callees. |

## Related reading

- [`docs/AGENT_REFERENCE.md`](../AGENT_REFERENCE.md) — single self-contained doc for LLM agents.
- [`docs/CHANGES-2026-05-25.md`](../CHANGES-2026-05-25.md) — the 74-commit session changelog this reference set documents.
- [`docs/cookbook/`](../cookbook/) — task-oriented walkthroughs (file-parsing, incremental-sources, authoring-connectors, code-search, code-and-docs-kbs, code-aware-chunking).
- [`docs/connectors/`](../connectors/) — connector tier table, gdrive + github auth guides, FOLLOWUPS list.
- [`docs/tutorial-code-repo-ingest.md`](../tutorial-code-repo-ingest.md) — zero-to-hero walkthrough of ingesting a code repo.
