# Benchmark: grep + load vs chunkshop hybrid search

_Generated: 2026-05-25 17:50:15 EDT_

## Setup

- Corpus repo: `/Users/matt.yonkovit/yonk-tools/chunkshop-track3`
- chunkshop table: `chunkshop_code_and_docs_demo.kb_code` (2496 chunks, 489 docs)
- Tokenizer: `tiktoken cl100k_base`
- Queries: 10 (see `python/examples/benchmark_queries.yaml`)
- Retrieval k: 5 for B and C; impact depth 2 for D
- Total ingest cost (build kb_code): 0.0s

## Approaches

| Tag | Name | What it does |
|---|---|---|
| A | grep + load | grep -rln <terms>, load every matched file WHOLE into context. Models the 'copilot without RAG' baseline. |
| B | chunkshop hybrid_search | semantic + FTS fused via RRF, top-5 chunks. |
| C | chunkshop --by-symbol | hybrid_search filtered to `symbol_name`. Only runs when the query has a symbol target. |
| D | chunkshop impact-of | walks `code_edges` for callers/callees of an FQN. Only runs when the query is an impact question. |

## Executive summary

Across 10 engineering queries against the chunkshop repo (2,496 indexed chunks, 489 files), chunkshop hybrid search consumes **32,121 tokens total vs grep+load's 1,765,752 — a 98.2% reduction**. Average precision@5: hybrid 0.42 vs grep 0.38. Both approaches surface the expected answer location on 8/10 and 10/10 queries respectively. When `--by-symbol` is applicable (5 queries), it further reduces tokens to 3,452 with precision 0.80. impact-of (D) excels at the one pure call-graph question — see the per-query table.

## Per-query results

| Query | A: grep+load | B: hybrid | C: --by-symbol | D: impact-of | A/B ratio |
|---|---:|---:|---:|---:|---:|
| `q01_incrementalsource_definition` | 88,900 | 1,208 | 232 | — | 73.6x |
| `q02_pg_table_iter_changes_since` | 252,345 | 3,254 | 1,135 | — | 77.5x |
| `q03_calls_to_proactive_refresh` | 41,411 | 608 | — | 2,036 | 68.1x |
| `q04_implement_a_new_connector` | 346,527 | 2,636 | — | — | 131.5x |
| `q05_why_tuple_cursor` | 23,059 | 6,204 | — | — | 3.7x |
| `q06_pipeline_architecture` | 713,241 | 2,551 | 1,772 | — | 279.6x |
| `q07_rename_fingerprint_impact` | 90,029 | 3,515 | 258 | — | 25.6x |
| `q08_add_oauth_to_connector` | 79,066 | 4,942 | — | — | 16.0x |
| `q09_stalecursorerror` | 38,929 | 2,193 | 55 | — | 17.8x |
| `q10_code_vs_symbol_chunker` | 92,245 | 5,010 | — | — | 18.4x |

## Aggregate (totals, means, medians)

| Approach | n queries | total tokens | mean tokens | median tokens | mean precision@5 | mean noise | mean wall (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| A: grep+load | 10 | 1,765,752 | 176,575 | 89,464 | 0.380 | 0.608 | 0.222 |
| B: hybrid | 10 | 32,121 | 3,212 | 2,945 | 0.420 | 0.582 | 0.034 |
| C: --by-symbol | 5 | 3,452 | 690 | 258 | 0.800 | 0.208 | 0.019 |
| D: impact-of | 1 | 2,036 | 2,036 | 2,036 | 1.000 | 0.000 | 0.070 |

## Win/loss per query

| Query | Lowest-token winner | Highest precision@5 winner | Hit expected answer? |
|---|---|---|---|
| `q01_incrementalsource_definition` | C_by_symbol (232 tok) | C_by_symbol (1.00) | A, B, C |
| `q02_pg_table_iter_changes_since` | C_by_symbol (1,135 tok) | C_by_symbol (0.50) | A, B, C |
| `q03_calls_to_proactive_refresh` | B_hybrid_search (608 tok) | B_hybrid_search (1.00) | A, B, D |
| `q04_implement_a_new_connector` | B_hybrid_search (2,636 tok) | B_hybrid_search (1.00) | A, B |
| `q05_why_tuple_cursor` | B_hybrid_search (6,204 tok) | A_grep_load (0.20) | A |
| `q06_pipeline_architecture` | C_by_symbol (1,772 tok) | C_by_symbol (1.00) | A, C |
| `q07_rename_fingerprint_impact` | C_by_symbol (258 tok) | A_grep_load (1.00) | A, B, C |
| `q08_add_oauth_to_connector` | B_hybrid_search (4,942 tok) | A_grep_load (1.00) | A, B |
| `q09_stalecursorerror` | C_by_symbol (55 tok) | A_grep_load (1.00) | A, B, C |
| `q10_code_vs_symbol_chunker` | B_hybrid_search (5,010 tok) | B_hybrid_search (0.20) | A, B |

## Per-query detail (with relevance judgement)

### `q01_incrementalsource_definition` — Where is IncrementalSource defined?

**Intent:** Find the file + class declaration of the IncrementalSource Protocol.

**Grep terms:** `IncrementalSource`
**by_symbol:** `IncrementalSource`

**Judgement rubric:** A hit is relevant if it is the class declaration of IncrementalSource in
sources/base.py OR a direct reference that names the Protocol (an implementing
class, type annotation, or docstring that quotes the class).
Hits that merely mention "incremental" in unrelated contexts are NOT relevant.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 88,900 | 25 | 0.00 | yes | 0.100 | 0.99 | 1/1 | grep matched 25 files; engineer would load all 25 files whole into context (88,900 tokens). |
| B_hybrid_search | 1,208 | 5 | 0.20 | yes | 0.042 | 0.81 | 1/1 | hybrid_search returned 5 chunks; total 1,208 tokens. |
| C_by_symbol | 232 | 1 | 1.00 | yes | 0.019 | 0.00 | 1/1 | by-symbol='IncrementalSource'; 1 chunks, 232 tokens. |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q02_pg_table_iter_changes_since` — Show me how pg_table.iter_changes_since handles cursor advancement

**Intent:** Find the iter_changes_since implementation in PgTableSource, in particular the tuple-cursor advancement logic.

**Grep terms:** `iter_changes_since, pg_table`
**by_symbol:** `PgTableSource`

**Judgement rubric:** A hit is relevant if it shows the iter_changes_since method body OR the
tuple-cursor SQL in pg_table.py. Implementations in OTHER sources (http,
gdrive, github) are NOT relevant for this query — the question is
specifically about pg_table.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 252,345 | 81 | 0.00 | yes | 0.285 | 1.00 | 1/1 | grep matched 81 files; engineer would load all 81 files whole into context (252,345 tokens). |
| B_hybrid_search | 3,254 | 5 | 0.20 | yes | 0.038 | 0.72 | 1/1 | hybrid_search returned 5 chunks; total 3,254 tokens. |
| C_by_symbol | 1,135 | 2 | 0.50 | yes | 0.020 | 0.19 | 1/1 | by-symbol='PgTableSource'; 2 chunks, 1,135 tokens. |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q03_calls_to_proactive_refresh` — What calls proactive_refresh?

**Intent:** List the call sites (callers) of chunkshop.oauth.refresh.proactive_refresh.

**Grep terms:** `proactive_refresh`
**impact_fqn:** `chunkshop.oauth.refresh.proactive_refresh` (direction=callers)

**Judgement rubric:** Relevant hits = (a) the proactive_refresh definition itself, (b) tests that
call it, (c) production call sites. The DEFINITION is "less relevant" than
the CALLERS for this specific question — but is included because grep
surfaces it and the engineer can read upward from there.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 41,411 | 11 | 0.60 | yes | 0.111 | 0.83 | 2/2 | grep matched 11 files; engineer would load all 11 files whole into context (41,411 tokens). |
| B_hybrid_search | 608 | 5 | 1.00 | yes | 0.030 | 0.00 | 2/2 | hybrid_search returned 5 chunks; total 608 tokens. |
| C_by_symbol | — | — | — | — | — | — | — | n/a |
| D_impact_of | 2,036 | 6 | 1.00 | yes | 0.070 | 0.00 | 2/2 | impact-of chunkshop.oauth.refresh.proactive_refresh direction=callers depth=2; 6 edges, 2,036 tokens. |

### `q04_implement_a_new_connector` — How do I implement a new connector?

**Intent:** Find authoring-connectors docs, the Connector Protocol, and at least one verified-tier connector to study as an example.

**Grep terms:** `connector, Protocol`

**Judgement rubric:** A hit is relevant if it shows: the authoring-connectors cookbook, the
Source/IncrementalSource Protocol declarations, the connector registry, or
a verified-tier connector implementation (blob, rss, github, gdrive). NB:
this query intentionally tests breadth — the grep baseline on "connector"
will pull MANY files, most of which are noise (tests for one connector,
experimental stubs, etc.). The conceptually-relevant set is small.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 346,527 | 142 | 0.00 | yes | 0.278 | 0.90 | 2/2 | grep matched 142 files; engineer would load all 142 files whole into context (346,527 tokens). |
| B_hybrid_search | 2,636 | 5 | 1.00 | yes | 0.033 | 0.00 | 1/2 | hybrid_search returned 5 chunks; total 2,636 tokens. |
| C_by_symbol | — | — | — | — | — | — | — | n/a |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q05_why_tuple_cursor` — Why does chunkshop use a tuple cursor for pg_table?

**Intent:** Find the rationale comment/docstring explaining tuple-cursor boundary-row safety in pg_table.py.

**Grep terms:** `tuple cursor, tuple-cursor, after_id`

**Judgement rubric:** A hit is relevant if it contains the rationale (boundary-row safety,
same-updated_at silent row-loss, "tuple cursor" comments) OR the
PgTableSource cursor-shape docstring in config.py. CLAUDE.md and test
files that REFERENCE the rationale are partially relevant.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 23,059 | 7 | 0.20 | yes | 0.296 | 0.53 | 2/2 | grep matched 7 files; engineer would load all 7 files whole into context (23,059 tokens). |
| B_hybrid_search | 6,204 | 5 | 0.00 | no | 0.038 | 1.00 | 0/2 | hybrid_search returned 5 chunks; total 6,204 tokens. |
| C_by_symbol | — | — | — | — | — | — | — | n/a |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q06_pipeline_architecture` — How does chunkshop chunker, extractor, sink fit together?

**Intent:** Find the runner.run_cell orchestration that wires Source -> Chunker -> Embedder -> Extractor -> Sink.

**Grep terms:** `run_cell, Source, Chunker, Sink`
**by_symbol:** `run_cell`

**Judgement rubric:** Relevant = the run_cell / Pipeline orchestration code OR architecture docs
that describe the chunker -> extractor -> sink contract. The base.py
Protocols are also relevant since they ARE the contract.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 713,241 | 313 | 0.00 | yes | 0.537 | 0.98 | 2/2 | grep matched 313 files; engineer would load all 313 files whole into context (713,241 tokens). |
| B_hybrid_search | 2,551 | 5 | 0.20 | no | 0.034 | 0.85 | 0/2 | hybrid_search returned 5 chunks; total 2,551 tokens. |
| C_by_symbol | 1,772 | 1 | 1.00 | yes | 0.019 | 0.00 | 1/2 | by-symbol='run_cell'; 1 chunks, 1,772 tokens. |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q07_rename_fingerprint_impact` — If I rename Document.fingerprint, what files break?

**Intent:** Enumerate every file that reads or writes Document.fingerprint.

**Grep terms:** `fingerprint`
**by_symbol:** `Document`

**Judgement rubric:** Multi-file impact query. Every file that mentions Document.fingerprint is
relevant; the engineer needs ALL of them to safely rename. This is a worst
case for hybrid search (the engineer wants exhaustive recall, not the top
5 most-relevant) and a best case for grep (literal string match across
files).

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 90,029 | 31 | 1.00 | yes | 0.120 | 0.00 | 5/5 | grep matched 31 files; engineer would load all 31 files whole into context (90,029 tokens). |
| B_hybrid_search | 3,515 | 5 | 0.20 | yes | 0.034 | 0.98 | 2/5 | hybrid_search returned 5 chunks; total 3,515 tokens. |
| C_by_symbol | 258 | 2 | 0.50 | yes | 0.020 | 0.84 | 1/5 | by-symbol='Document'; 2 chunks, 258 tokens. |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q08_add_oauth_to_connector` — I want to add OAuth-based authentication to a new connector

**Intent:** Find the OAuthProvider Protocol, the proactive_refresh helper, the GoogleOAuthProvider reference implementation, and any existing OAuth-backed connector for a worked example.

**Grep terms:** `OAuth, OAuthProvider`

**Judgement rubric:** Vague-intent query — the engineer doesn't know the right vocabulary yet.
Relevant hits surface: the OAuthProvider Protocol, the existing
GoogleOAuthProvider concrete impl, OAuthTokens dataclass, proactive_refresh
helper, gdrive connector as a reference. Grep on "OAuth" picks up MANY
test files and changelog mentions — high noise expected.

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 79,066 | 31 | 1.00 | yes | 0.173 | 0.00 | 3/3 | grep matched 31 files; engineer would load all 31 files whole into context (79,066 tokens). |
| B_hybrid_search | 4,942 | 5 | 0.80 | yes | 0.031 | 0.01 | 2/3 | hybrid_search returned 5 chunks; total 4,942 tokens. |
| C_by_symbol | — | — | — | — | — | — | — | n/a |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q09_stalecursorerror` — What raises StaleCursorError?

**Intent:** Find every line that raises StaleCursorError so the engineer understands the trigger conditions.

**Grep terms:** `StaleCursorError`
**by_symbol:** `StaleCursorError`

**Judgement rubric:** Relevant hits = the class definition + every raise site. Mentions in tests
that ASSERT it gets raised count as partially relevant (they document the
contract).

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 38,929 | 12 | 1.00 | yes | 0.092 | 0.00 | 2/2 | grep matched 12 files; engineer would load all 12 files whole into context (38,929 tokens). |
| B_hybrid_search | 2,193 | 5 | 0.40 | yes | 0.031 | 0.96 | 2/2 | hybrid_search returned 5 chunks; total 2,193 tokens. |
| C_by_symbol | 55 | 1 | 1.00 | yes | 0.019 | 0.00 | 1/2 | by-symbol='StaleCursorError'; 1 chunks, 55 tokens. |
| D_impact_of | — | — | — | — | — | — | — | n/a |

### `q10_code_vs_symbol_chunker` — What is the difference between code_aware and symbol_aware chunkers?

**Intent:** Compare the two chunkers: what each is for, what languages each supports, when to pick one over the other.

**Grep terms:** `code_aware, symbol_aware`

**Judgement rubric:** Comparison query. Relevant hits surface BOTH implementations + at least
one of the docs that explains the choice. Hits showing only one of the two
are partial (rank should reflect that we want symmetry).

| Approach | Tokens | Hits | P@5 | Recall? | Wall (s) | Noise | Expected covered | Notes |
|---|---:|---:|---:|:---:|---:|---:|:---:|---|
| A_grep_load | 92,245 | 29 | 0.00 | yes | 0.223 | 0.85 | 4/4 | grep matched 29 files; engineer would load all 29 files whole into context (92,245 tokens). |
| B_hybrid_search | 5,010 | 5 | 0.20 | yes | 0.032 | 0.49 | 1/4 | hybrid_search returned 5 chunks; total 5,010 tokens. |
| C_by_symbol | — | — | — | — | — | — | — | n/a |
| D_impact_of | — | — | — | — | — | — | — | n/a |

## Caveats and threats to validity

- **Token measurement.** Both approaches use the same tokenizer (`tiktoken cl100k_base`). For Approach A the cost is sum-of-whole-file tokens — a real engineer's co-pilot loop. Pre-trimming files would reduce A's cost but also require the engineer to know what to trim, which is the very thing hybrid search automates.
- **Precision rubric.** Each query has hand-written `relevance_paths` substrings. A hit is 'relevant' if its source path contains any of those substrings. This is path-shaped, not content-shaped — it favors queries with clean module locality. Generic 'how-to' queries (q04, q08) have broader relevance_paths to compensate.
- **Grep ranking.** Grep has no ranking; we treat the first 5 matches (in file-system order) as the engineer's top-5 for the precision@5 calculation. A real engineer would skim filenames and pick, but they still pay the token cost to look.
- **Indexing cost not amortized.** Approach B+C+D pay a one-time ingest cost (~0s for this corpus) that A doesn't. After ingest, B/C/D are cheap per-query; A is fast every time. For repeated queries on a stable corpus, hybrid amortizes well.
- **Approach D applicability.** impact-of needs a populated `code_edges` table and an exact FQN. It is unfair to ask 'what calls X' of grep (it literally lists every line that mentions X) without acknowledging that the graph gives ranked, deduped, depth-bounded results.
- **Embedding cost.** Each query embedding takes ~50-100ms (one fastembed forward pass). Included in B/C wall time. Grep has no embed cost.
- **Single-corpus result.** Numbers are for the chunkshop repo. Repos with longer files / more boilerplate widen the gap in favor of hybrid; repos with very short, highly-distinctive symbol names narrow it.
- **k=5 is hyper-parameter.** chunkshop returns its top 5 chunks; if you push k=20 the picture changes. The point of comparing to grep+load is that grep returns N (no cap), so any finite k beats it on tokens.

## Reproducibility

This script is deterministic per token-count and precision metric:

- **Tokenization** is deterministic (tiktoken bpe encoder).
- **Grep results** are deterministic for a given repo snapshot. The `benchmarks` directory is in `GREP_EXCLUDE_DIRS` so the generated report doesn't pollute subsequent runs.
- **hybrid_search** ranks by RRF over deterministic per-leg scores; ties may flip on the 5th rank but the top 4 stayed stable across the two verification runs we conducted.
- **impact-of** is a pure Postgres recursive CTE — deterministic.
- **Wall times** vary 5-30% across runs (Postgres + kernel + fastembed warm-cache effects). The per-query latencies are reported but should not be over-interpreted for sub-second figures.

To verify reproducibility yourself:

```
python python/examples/benchmark_grep_vs_hybrid.py --csv-out /tmp/run1.csv --report-out /tmp/run1.md
python python/examples/benchmark_grep_vs_hybrid.py --csv-out /tmp/run2.csv --report-out /tmp/run2.md
diff <(cut -d, -f1-5 /tmp/run1.csv) <(cut -d, -f1-5 /tmp/run2.csv)   # expect no diff
```

## Recommendations

Use **grep+load** when:
- The corpus is < 50 files OR you are scanning ALL occurrences of a literal string (refactor planning, exhaustive recall).
- You have no indexed table available and ingest cost would not amortize.

Use **hybrid_search (B)** when:
- Your question is conceptual / semantic ('how does X work', 'why is Y this way'), where keyword grep would miss synonym matches.
- You want a token-efficient answer to paste into Claude — top-5 chunks of 100-500 tokens each fits well in any context.

Use **--by-symbol (C)** when:
- You know the symbol name and want its declaration + immediate context, not every test that mentions it.

Use **impact-of (D)** when:
- The question is structurally 'what calls X' or 'what does X depend on'. The graph gives ranked, deduped answers with confidence bands; grep gives you a flat list of every mention.
