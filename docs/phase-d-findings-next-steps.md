# Phase D Findings and Next Steps

This note records the pg-raggraph Phase D benchmark findings that should guide
Chunkshop evaluation and product planning. Phase D was not a Chunkshop-native
run; it is upstream evidence to reproduce with Chunkshop-only retrieval,
metadata, lede reports, and `llm-judge`.

Related docs:

- `docs/testing-benchmarking-tools.md`
- `docs/summary-density-validation-plan.md`
- `docs/kb-profile-ladder-plan.md`

## Run Summary

Source run:

```text
/home/yonk/yonk-tools/pg-raggraph/.matrix-runs/deep-d-summary-validation/
```

Judge summary:

| Metric | Value |
|---|---:|
| Judged rows | 450 |
| Correct | 241 |
| Partial | 28 |
| Errors | 0 |
| Passed | 259 |
| Pass rate | 57.6% |
| Average score | 0.662 |

The run is clean enough to use for directional product decisions: provider
error count was zero, per-case audits were preserved, and the same question
sets were tested across multiple context strategies.

## Strategies Tested

Phase D compared these context strategies on a shared retrieval setup:

| Strategy | Meaning |
|---|---|
| `classic_chunks` | Raw retrieved chunks, `top_k=25`; familiar RAG baseline. |
| `chunk_summary_facts` | Summary/facts over retrieved chunks only. |
| `doc_summary_facts` | Summary/facts over selected whole parent documents. |
| `chunk_summary_toc_facts_plus_top5` | Chunk summary/facts/headings plus top five raw chunks. |
| `doc_and_chunk_summary_toc_facts_plus_top5` | Both document and chunk summaries/facts/headings plus top five raw chunks. |

## Results

### SCOTUS / Legal Corpus

| Strategy | Pass | Score | Avg Context Tokens | Token Savings vs Classic |
|---|---:|---:|---:|---:|
| `doc_summary_facts` | 27/30, 90.0% | 0.908 | 2,017 | 82.5% |
| `doc_and_chunk_summary_toc_facts_plus_top5` | 26/30, 86.7% | 0.892 | 5,396 | 53.2% |
| `classic_chunks` | 21/30, 70.0% | 0.750 | 5,686 | 50.7% |
| `chunk_summary_toc_facts_plus_top5` | 20/30, 66.7% | 0.725 | 3,285 | 71.5% |
| `chunk_summary_facts` | 16/30, 53.3% | 0.600 | 2,210 | 80.8% |

Finding: for self-contained legal documents, document-level summaries and facts
are the right shape. They were both cheaper and more accurate than classic raw
chunks. Chunk-only summaries fragmented the answer surface and underperformed.

### MHR / News Multi-Hop Corpus

| Strategy | Pass | Score | Avg Context Tokens | Token Savings vs Classic |
|---|---:|---:|---:|---:|
| `chunk_summary_toc_facts_plus_top5` | 36/60, 60.0% | 0.671 | 5,258 | 54.4% |
| `classic_chunks` | 35/60, 58.3% | 0.667 | 11,535 | 0.0% |
| `doc_and_chunk_summary_toc_facts_plus_top5` | 29/60, 48.3% | 0.600 | 7,750 | 32.8% |
| `doc_summary_facts` | 25/60, 41.7% | 0.554 | 2,419 | 79.0% |
| `chunk_summary_facts` | 24/60, 40.0% | 0.537 | 2,758 | 76.1% |

Finding: for cross-document news/multi-hop questions, pure summaries are too
lossy. The best tested compact strategy kept a chunk-level summary/facts surface
but added raw top-five evidence. That slightly beat classic chunks while using
about half the context tokens.

## Findings

### 1. The Winning Context Shape Is Corpus-Dependent

SCOTUS and MHR invert each other:

- SCOTUS: `doc_summary_facts` wins by a wide margin.
- MHR: `doc_summary_facts` loses badly; `chunk_summary_toc_facts_plus_top5`
  is the best tested recipe.

This means a single global default cannot be optimal across corpora. The
default should be attached to a KB/namespace/profile, with a per-call override
when callers know the query needs a different tradeoff.

### 2. A Namespace Should Be a Retrieval Contract

Although unrelated corpora can technically be mixed in one table, doing so
forces one set of chunking, metadata, FTS, vector, and context-packing defaults
onto different data shapes. That creates predictable regressions.

Recommended default:

- SCOTUS-like legal corpus: separate KB/namespace/table with legal metadata and
  a doc-summary-first profile.
- MHR/news multi-hop corpus: separate KB/namespace/table with source/date/entity
  metadata and a chunk-summary-plus-raw profile.
- Code/docs corpus: separate KB/namespace/table with path/symbol/language
  metadata and raw-code-friendly context packing.

Mixed KBs should be allowed, but documented as compromise deployments.

### 3. Summary-Only Is a Real Cheap Mode, Not a Balanced Default

Summary-only strategies can cut tokens by roughly 76-82%, but MHR shows that
they can also drop accuracy sharply. They should be exposed as `cheap` or
`low_cost`, not silently used as the balanced default for all corpora.

### 4. Accurate Mode Should Stack Signals, Not Drop Summaries

The accurate end of the ladder should not be raw chunks only. The data supports
keeping summaries, facts, headings/TOC, and raw evidence together at higher
rungs.

Practical shape:

```text
cheap      -> summary + facts
balanced   -> summary + facts + headings + small raw evidence
accurate   -> doc summary + chunk summary + facts + headings + wider raw evidence
raw        -> explicit escape hatch for classic chunks
```

### 5. Auto Routing Should Be Based on Retrieval Shape

Do not try to infer "legal" or "news" from prose. The cheap and robust signal is
how retrieved chunks cluster:

- chunks concentrated in one or two parent documents -> prefer doc summaries;
- chunks spread across many parent documents -> prefer chunk summaries plus raw
  evidence;
- ambiguous distribution -> use the KB's configured default.

This routing belongs behind `summary_source=auto`, but explicit KB/profile
settings should win over auto.

## Suggestions for Chunkshop

### Product Surface

Add a first-class profile concept:

```yaml
profiles:
  default: balanced
  corpora:
    scotus:
      template: legal_doc
      default_profile: legal_doc
      summary_source: doc
    mhr:
      template: news_multihop
      default_profile: cross_doc
      summary_source: chunk
```

Expose simple and advanced modes:

- simple: `cheap`, `balanced`, `accurate`, `raw`, `legal_doc`, `cross_doc`;
- advanced: 5-10 configurable rungs with explicit packing settings;
- per-call override: `chunkshop search ... --profile accurate`;
- persisted KB default: config or backend metadata table.

### KB Templates

Add sample KB templates:

| Template | Default Profile | Main Metadata |
|---|---|---|
| `legal_doc` | `legal_doc` | term, docket, citation, case name, justice, opinion type |
| `news_multihop` | `cross_doc` | source, date, URL, entities, topic |
| `codebase` | `code_context` | path, symbol, language, package/module |
| `generic` | `balanced` | title, source, date, entities |

The templates should create separate namespaces/tables by default. A mixed-KB
template can exist later, but it should warn that defaults are compromises.

### Benchmark Analyzer

The analyzer should emit:

- `profile_calibration.json`
- `profile_recommendations.md`
- per-KB best strategy table
- aggregate Pareto frontier by score, tokens, and latency
- invalid-run warning when provider `ERROR` rows are nonzero above threshold

Calibration must preserve both aggregate and per-corpus numbers. The aggregate
can guide marketing and demos, but the per-corpus numbers should drive KB
defaults.

## Next Steps

### Immediate

1. Let the current Chunkshop deep sweep finish SCOTUS + LongBench collection and
   `llm-judge` scoring.
2. Treat the run as invalid for accuracy claims if provider `ERROR` rows are
   nonzero above the agreed threshold.
3. Compare Chunkshop results against the Phase D hypotheses:
   - legal/doc corpora favor doc-summary/facts;
   - multi-hop/cross-document corpora favor chunk summary + raw evidence;
   - summary-only is cheap but risky;
   - accurate mode should stack summaries/facts/raw chunks.
4. Produce a run report with top configs by workload, token reduction, latency,
   and pass rate.

### Implementation Planning

1. Lift context-packing strategies out of benchmark scripts into reusable
   library code.
2. Add profile/rung data models and validation.
3. Add profile resolution precedence:

   ```text
   per-call profile > per-KB profile > global default > auto
   ```

4. Add KB template sample configs for legal, news/multi-hop, codebase, and
   generic corpora.
5. Add backend persistence for KB settings where available, with file manifest
   fallback for dump/file outputs.
6. Add `chunkshop eval` support for generating `profile_calibration.json` and
   recommendations.

### Tests

1. Unit-test profile resolver aliases, rung indexes, slider values, and errors.
2. Unit-test auto source selection using clustered and spread retrieval-hit
   fixtures.
3. Integration-test two KBs with different defaults:
   - `scotus_like` uses doc summary;
   - `mhr_like` uses chunk summary plus raw top chunks.
4. Verify per-call overrides beat KB defaults.
5. Verify profile settings persist for database-backed targets and serialize in
   manifests for file/dump output.
6. Add benchmark smoke tests that run a tiny matrix through `llm-judge` quick
   mode and preserve audits.

### Documentation

1. Add KB template examples under `docs/samples/kb/`.
2. Document "namespace as retrieval contract" in search, ingest, and benchmark
   docs.
3. Add a "when to split corpora" section:
   - different metadata;
   - different question types;
   - different chunking;
   - different context-packing winner;
   - different latency/token budget.
4. Keep mixed-corpus support documented, but not recommended as the default
   pattern.

## Recommendation

For v1, ship a conservative product shape:

- `balanced` remains the no-profile default.
- Separate KBs/namespaces are recommended for different corpus shapes.
- KB templates provide sane defaults.
- `raw` remains an escape hatch.
- `auto` chooses doc vs chunk summary only when the KB has no explicit
  preference.
- Calibration output is required before claiming a profile is the best default
  for a workload.
