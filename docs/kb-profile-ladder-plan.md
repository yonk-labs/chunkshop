# KB-Specific Retrieval Profile Ladder Plan

This plan adapts the pg-raggraph Phase D/E learning for Chunkshop: a
knowledge base should be a retrieval contract, not just an arbitrary bucket of
text. SCOTUS-shaped legal corpora and MHR-shaped news/multi-hop corpora have
different winning recipes, metadata, indexes, and evaluation expectations. They
should normally live in separate KBs/namespaces, and each KB should carry its
own retrieval/profile defaults.

For the concrete Phase D numbers, recommendations, and next-step checklist,
see `docs/phase-d-findings-next-steps.md`.

For target KB template samples, see `docs/samples/kb/`.

For the proposed 1:M document/chunk storage model that makes doc-summary
profiles first-class, see `docs/document-table-plan.md`.

Mixed-corpus namespaces are still possible, but they are the exception. If a
team puts SCOTUS and MHR in the same KB, they are choosing a compromise profile
and accepting that what helps one corpus shape may hurt another.

## Why This Exists

The pg-raggraph validation exposed a corpus-shape dependency:

- **SCOTUS/legal**: full-document summaries with facts won decisively.
  `doc_summary_facts` beat classic chunks on accuracy and tokens.
- **MHR/news**: pure summaries lost at larger n; the best compact contender was
  chunk summary + facts + raw top chunks.
- **Raw-only is not the accurate end.** Summary/fact scaffolding remains useful
  even when raw chunks are included. Higher rungs should stack summaries,
  facts, headings/TOC, and raw chunks, not simply increase raw `top_k`.

Conclusion: the profile should be a property of the KB/corpus, with a per-call
override for callers that know better. More importantly, Chunkshop should make
it easy to create separate KBs with separate schemas/indexes/settings instead
of encouraging unrelated corpora to share one namespace.

## Design Principle

**A KB/namespace is a retrieval contract.**

It defines:

- corpus shape and expected question types;
- chunking strategy;
- metadata promotion;
- FTS/search-text fields;
- vector metric/index choices;
- default retrieval profile;
- context-packing strategy;
- evaluation workload and acceptable baselines.

SCOTUS and MHR can technically be stored together, but that should not be the
recommended path. Separate KB structures let each corpus optimize for its own
shape:

| KB shape | Example | Likely structure |
|---|---|---|
| self-contained legal/doc corpus | SCOTUS | doc-summary-heavy, promoted legal metadata, document-level support |
| cross-document news/multi-hop corpus | MHR | chunk-summary + raw evidence, broader doc spread, source/date/entity metadata |
| code/documentation corpus | PG code base | code-aware chunks, path/symbol metadata, raw snippets plus summaries |

The profile ladder is therefore not "one ladder to rule all corpora." It is a
shared API and config vocabulary that each KB can bind to different rung
recipes and defaults.

## Product Shape

Expose an ordered N-rung ladder from cheap to accurate.

Default:

- 7 rungs.
- Configurable between 5 and 10 rungs.
- No-profile default resolves to `balanced`.
- Simple API accepts named tiers, integer rung index, or a `0.0-1.0` slider
  value.
- Advanced API accepts explicit rung definitions.

Profile resolution:

```text
per-call profile
  > per-KB/namespace profile
  > global default profile
  > auto source selector
```

The KB/namespace profile can be stored in config for file-based/local use and
in a metadata/settings table for database-backed deployments. Chunkshop should
not assume one backend has a namespace registry today; the interface should
work for Postgres, SQLite, ClickHouse, MariaDB, and file output even if
persistence details differ.

## KB Structure Recommendations

Chunkshop should document and support KB templates rather than forcing one
generic structure.

### Legal/SCOTUS-like KB

Recommended traits:

- separate namespace/schema/table for legal opinions;
- document IDs based on case/opinion identity;
- promoted metadata such as term, docket, citation, case name, justice names,
  author, opinion type;
- FTS over original text plus `lede_report.search_text`;
- default profile: `legal_doc` or a doc-summary-heavy rung;
- use RAG for explanation/support, not exhaustive corpus aggregation.

### News/MHR-like KB

Recommended traits:

- separate namespace/schema/table for news or multi-hop article collections;
- document IDs based on article/source identity;
- promoted metadata such as source, publication date, entities, topic, URL;
- FTS over article text plus entity/fact search text;
- default profile: `cross_doc` or chunk-summary + raw evidence rung;
- expect top chunks to span many documents.

### Code/Docs KB

Recommended traits:

- separate namespace/table per repository or coherent codebase;
- code-aware chunking where available;
- promoted metadata such as path, symbol, language, package/module, heading;
- default profile tuned separately from prose corpora;
- raw snippets remain important even when summaries are present.

### Mixed KBs

Mixed KBs are allowed for convenience, but should be marked as compromise
deployments:

- profile `auto` should be enabled by default;
- calibration should report per-corpus or per-source-family performance, not
  only aggregate performance;
- documentation should warn that global defaults may hide regressions for one
  corpus type;
- serious production deployments should split the mixed KB once stable access
  patterns are known.

## Rung Model

Each rung is a bundle of retrieval and context-packing levers:

```yaml
name: balanced
level: 0.5
packing: chunk_summary_toc_facts_plus_top5
top_k: 25
top_n_raw: 5
doc_coverage: 5
summary_density: 1.0
summary_source: auto
include_facts: true
include_headings: true
include_metadata: true
estimates:
  aggregate:
    tokens: 5200
    accuracy: 0.67
    latency_ms: 38000
  corpus:
    scotus:
      tokens: 3285
      accuracy: 0.725
    mhr:
      tokens: 5258
      accuracy: 0.671
```

Required rung fields:

- `name`
- `level`
- `packing`
- `top_k`
- `top_n_raw`
- `doc_coverage`
- `summary_density`
- `summary_source`
- `include_facts`
- `include_headings`
- `include_metadata`
- `estimates`

## Provisional Seven-Rung Ladder

These are intentionally provisional. Final values should come from
`profile_calibration.json`, generated from Chunkshop's own benchmark results.

| Rung | Tier | Packing | Intent |
|---:|---|---|---|
| L0 | cheapest | `summary_facts` | Minimum context; accepts accuracy loss. |
| L1 | cheap | `summary_toc_facts` | Adds headings/TOC for little token cost. |
| L2 | cheap+ | `summary_toc_facts_plus_top3` | Adds a small raw evidence anchor. |
| L3 | balanced | `summary_toc_facts_plus_top5` with `summary_source=auto` | Default tradeoff. |
| L4 | balanced+ | `doc_and_chunk_summary_toc_facts_plus_top5` | Cross-corpus hedge. |
| L5 | accurate | `doc_and_chunk_summary_toc_facts_plus_top10` | More raw support plus both summary surfaces. |
| L6 | max | `doc_and_chunk_summary_toc_facts_plus_topN` with wider doc coverage | Burn tokens for answer quality; not raw-only. |

Important: the higher rungs should preserve summary/fact scaffolding. They add
raw chunks and coverage; they do not replace summaries with raw text.

## Corpus/KB Profiles

The profile object should support explicit per-corpus defaults:

```yaml
profiles:
  default: balanced
  corpora:
    scotus:
      default_profile: legal_doc
      summary_source: doc
    mhr:
      default_profile: cross_doc_balanced
      summary_source: chunk
```

Suggested preset aliases:

- `raw`: classic raw chunks escape hatch.
- `cheap`: low-token summary/facts.
- `balanced`: default middle rung.
- `accurate`: stacked summaries/facts/raw chunks.
- `legal_doc`: doc-summary-first profile for self-contained legal/doc corpora.
- `cross_doc`: chunk-summary + raw profile for news/multi-hop corpora.

The alias maps to rung indices and can be overridden by calibration.

## Auto Source Selector

`summary_source=auto` chooses document summary vs chunk summary from the
retrieval result shape.

Heuristic:

- If top chunks cluster in one or two parent documents, use doc summaries.
- If top chunks spread across many parent documents, use chunk summaries.
- If clustering is ambiguous, use the KB profile's preferred source.

Inputs:

- `doc_id` distribution among retrieved hits.
- top-k entropy or distinct-doc ratio.
- presence of strong metadata filters.
- corpus-specific default when configured.

Example:

```text
distinct_docs / top_k <= 0.20 -> doc
distinct_docs / top_k >= 0.50 -> chunk
otherwise -> KB default, then global default
```

This is a routing heuristic, not domain classification. It should not try to
detect "legal" or "news" from text.

## Calibration Output

The benchmark harness should emit `profile_calibration.json`:

```json
{
  "version": 1,
  "generated_at": "2026-05-24T00:00:00Z",
  "rungs": [
    {
      "name": "balanced",
      "level": 0.5,
      "packing": "summary_toc_facts_plus_top5",
      "aggregate": {
        "tokens": 5258,
        "accuracy": 0.671,
        "score": 0.671,
        "latency_ms": 38269
      },
      "corpus": {
        "mhr": {"tokens": 5258, "accuracy": 0.600, "score": 0.671},
        "scotus": {"tokens": 3285, "accuracy": 0.667, "score": 0.725}
      }
    }
  ]
}
```

Rules:

- Estimates are measured, not hardcoded.
- Include aggregate and per-corpus estimates.
- Include `ERROR` counts and do not publish estimates from invalid runs.
- Preserve the source run paths used to generate the calibration.
- Calibration should be optional; if absent, use conservative static defaults.

## Chunkshop Implementation Plan

### 1. KB Template Model

Add a lightweight KB template concept that can be used by docs, eval plans, and
eventually ingest/search commands:

- `legal_doc`
- `news_multihop`
- `codebase`
- `generic`

A template is not magic. It is a named bundle of defaults:

- chunker defaults;
- extractor defaults;
- metadata promotion paths;
- FTS metadata paths;
- vector metric/index defaults;
- default profile/rung;
- recommended evaluation workload.

Example:

```yaml
kb:
  id: scotus
  template: legal_doc
  target:
    type: postgres
    schema: chunkshop_scotus
    table: chunks
  profiles:
    default: legal_doc
```

The first version can be config-only. Later versions can expose
`chunkshop kb init --template legal_doc --id scotus`.

### 2. Data Model

Add profile config types under `chunkshop.eval` or a new
`chunkshop.profiles` module:

- `ProfileLadder`
- `ProfileRung`
- `ProfileEstimate`
- `CorpusProfile`
- `ProfileResolver`

Resolution API:

```python
resolve_profile(
    requested: str | int | float | None,
    *,
    corpus_id: str | None,
    config: ProfileConfig,
    retrieval_hits: list[Hit] | None = None,
) -> ProfileRung
```

### 3. Context Packers

Move strategy assembly into reusable code:

- `classic_chunks`
- `summary_facts`
- `summary_toc_facts`
- `summary_toc_facts_plus_topN`
- `doc_summary_facts`
- `doc_and_chunk_summary_toc_facts_plus_topN`

The deep benchmark scripts should import these packers instead of having their
own copy. This prevents benchmark/product drift.

### 4. Config Surface

Add optional config fields:

```yaml
profiles:
  default: balanced
  ladder: docs/samples/eval/profile-ladder.yaml
  calibration: profile_calibration.json
  corpora:
    scotus:
      default: legal_doc
      summary_source: doc
    mhr:
      default: cross_doc
      summary_source: chunk
```

For CLI/search-time overrides:

```bash
chunkshop search "question" --profile balanced
chunkshop search "question" --profile 0.75
chunkshop search "question" --profile-index 5
```

### 5. Backend Persistence

For database-backed targets, support storing KB/corpus profile settings as
metadata, not as process-only state.

Backend options:

- Postgres/SQLite: `chunkshop_kb_settings` table.
- ClickHouse/MariaDB: equivalent settings table where supported.
- File/dump output: include settings in the run manifest.

Suggested fields:

- `corpus_id`
- `profile`
- `summary_source`
- `ladder_version`
- `calibration_id`
- `updated_at`

Absence of a row means fall through to global default and auto selector.

### 6. Benchmark Analyzer

Extend the benchmark analyzer to emit:

- `profile_calibration.json`
- `profile_recommendations.md`
- per-corpus best rung table
- aggregate best rung table
- invalid-run warning when `ERROR` rows exceed threshold

Recommendation logic:

- identify Pareto frontier by score, tokens, latency;
- label per-corpus default;
- label aggregate default;
- ensure aliases like `cheap`, `balanced`, `accurate` map to monotonic token
  rungs;
- avoid picking pure summary-only as `balanced` if it loses heavily on a corpus.

### 7. Documentation and Examples

Add sample configs:

- `docs/samples/kb/scotus-legal-doc.yaml`
- `docs/samples/kb/mhr-news-multihop.yaml`
- `docs/samples/kb/codebase.yaml`
- `docs/samples/eval/kb-profile-calibration.yaml`

Each sample should show a separate namespace/table. Do not present mixed SCOTUS
+ MHR as the default pattern.

## Tests

### Unit Tests

- `ProfileResolver` maps:
  - `cheap`, `balanced`, `accurate`
  - integer rung indices
  - `0.0-1.0` slider values
  - unknown names with clear errors
- precedence:
  - per-call profile > per-corpus profile > global default
- auto source selector:
  - clustered hits -> doc summary
  - spread hits -> chunk summary
  - ambiguous hits -> corpus/global default
- rung validation:
  - default ladder has 5-10 rungs
  - levels are ordered
  - token estimates are monotonic when estimates exist
  - accurate rungs include summary/fact scaffolding and raw chunks

### Integration Tests

- Create two corpora with different profile settings:
  - `scotus_like` -> doc-summary profile
  - `mhr_like` -> chunk+raw profile
- Run the same search question with no per-call profile and verify different
  context-packing strategies are used.
- Override per-call profile and verify it wins over corpus config.
- Save settings, reload config/backend, and verify persistence.
- Validate KB template expansion:
  - `legal_doc` promotes legal metadata and defaults to a doc-summary profile.
  - `news_multihop` promotes source/date/entity metadata and defaults to a
    chunk+raw profile.
- Build two separate namespaces/tables and assert each receives its own FTS
  metadata paths and profile defaults.
- Mixed-KB warning path: a config that declares multiple corpus families in one
  KB should validate but emit a warning that profile defaults are compromise
  defaults.

### Benchmark Tests

- Run the summary-density validation set.
- Emit `profile_calibration.json`.
- Assert every default rung has:
  - tokens
  - score
  - accuracy
  - latency
  - per-corpus estimates
- Assert `ERROR` count is zero or explicitly marked invalid.

## Acceptance Criteria

- Users can choose simple profiles without understanding packer internals.
- Advanced users can define 5-10 rung ladders.
- Different KBs can use different defaults.
- Docs and samples recommend separate KB structures for different corpus
  shapes.
- KB templates exist for at least legal/doc, news/multi-hop, and generic/code
  use cases.
- Per-call overrides work.
- Calibration output exposes both aggregate and per-corpus estimates.
- Accurate profiles stack summaries/facts/raw chunks; they are not raw-only.
- The benchmark harness and product code use the same packer implementation.
- Documentation explains when to use `legal_doc`, `cross_doc`, `balanced`,
  `accurate`, and `raw`.

## Open Questions

- Should KB profile persistence live in each backend target schema or in a
  separate project-local manifest for non-Postgres backends?
- Should KB templates be config-only in the first release, or should there be a
  first-class `chunkshop kb init` CLI immediately?
- Should `chunkshop search` expose profile controls now, or should this start
  as `chunkshop eval`/benchmark-only until the packer stabilizes?
- Should calibration be explicitly versioned by embedder/chunker/operator, or
  only by corpus/workload and context strategy?
- Should corpus recommendations be manually accepted before becoming defaults,
  or can benchmark calibration write active settings automatically?
