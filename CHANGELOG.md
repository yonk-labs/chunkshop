# Changelog

## Unreleased

## 1.0.0-rc — 2026-06-23

First release candidate for 1.0.0. Bundles the Rust lede/lede-enrich Tier-1
enrichment parity work, a hybrid-search fix, and dependency-advisory patches.
Python (`chunkshop`) and Rust (`chunkshop-rs`) ship in lockstep at this version.

### Search

- **`hybrid_search` rejects `rrf_k < 1`** with a clear `ValueError` instead of
  leaking a `ZeroDivisionError` from RRF fusion (#78). Adds a DB-free regression
  test.

### Security / dependencies

- Patched newly-published advisories on transitive deps: `quinn-proto` 0.11.15
  (RUSTSEC-2026-0185), `urllib3` 2.7.0, `idna` 3.18, `msgpack` 1.2.1, `pypdf`
  6.14.2. The `rsa` / `paste` / `torch` (CVE-2025-3000) advisories have no
  upstream fix and are CI-ignored with justification.

### Rust — lede / lede-enrich Tier-1 enrichment parity (#76)

First functional Rust catch-up since the RM-A/B/C line: closes the
lede-dependent slice of the #76 Tier-1 enrichment gap. All new surface is gated
behind the existing optional `lede` cargo feature.

- **Deps:** bump the Rust `lede` crate 0.3 → **0.5.1** and add **`lede-enrich` 0.2**,
  both from crates.io (version-only deps). Floors pin the fixes we rely on:
  lede 0.5.1 added `extract::{fact_records,stats}` (lede#11) + amount fixes
  (currency-prefixed money, `units`, no more `$5 million`→`$5` truncation —
  lede#12); lede-enrich 0.2 fixed gazetteer NER precision (no more false
  positives on capitalized common nouns; consistent title stripping — lede#12),
  verified downstream. The `lede` feature widened to
  `["dep:lede", "dep:lede-enrich"]`. Default builds don't pull either crate.
- **`lede_top_terms` extractor** — top-N salient words/phrases via lede 0.5
  `top_terms_scored`; writes metadata `top_terms` = `[{term,score,kind}]`, tags
  = `[term]`.
- **`lede_report` extractor** — assembles `key_facts` +
  `metadata.{dates,amounts,urls,entities}` (entities filled by lede-enrich's
  gazetteer) + `fact_records` + `stats` from lede 0.5.1's
  `extract::{fact_records,stats}` — **byte-identical to Python's
  `readable_report().to_dict()`** for those keys (verified field-for-field).
  Still omits the aggregator-only / spaCy-only keys lede-rs doesn't expose
  (`attributes`, `spacy_*`, `search_text`, `promotion_candidates`, `summary`);
  a consumer reading the present keys gets parity, absent keys are missing not
  wrong.
- **`lede_entities` extractor** — deterministic, license-clean gazetteer NER via
  lede-enrich. Writes the shared `entities` key as `{"unlabeled": [surface_forms]}`
  — schema-uniform with Python's labeled `{LABEL: [...]}` dict (one code path for
  consumers), content-divergent (no per-label classification). Distinct config
  type from the Python-only `spacy_entities`.
- **`consolidator: { mode: lede }`** — salient-sentence propositions via lede 0.5
  `key_facts`, with rank-decay confidence `round(1 - i/n, 3)` and a
  `confidence_floor`. SVO fields are empty (the lede non-spaCy path yields
  sentences, not triples — matches Python `lede_facts`); `summary` empty.
- Feature-off: the four new config variants still parse; `build_extractor` /
  the consolidator return an actionable "gated behind the `lede` feature" error
  rather than panicking.

Spec: `docs/superpowers/specs/2026-06-22-rust-lede-enrich-integration-design.md`.

## 0.9.1 — 2026-06-09

Fixes a regression introduced by 0.9.0's path-less language detection (#69):
generated and minified files that 0.8.3 skipped were being symbol-parsed,
flooding downstream consumers with chunks and OOM-ing them (pg-raggraph#79,
bento). Python-only; the Rust crate is a lockstep version bump with no
functional change.

### Fixed

- **`symbol_aware` no longer over-parses generated / minified files (#71).**
  0.9.0's content heuristic started classifying machine-emitted files as code
  that 0.8.3 skipped. A 143 KB generated `.ts` (3,000 trivial functions) became
  3,000 symbol chunks; consumers that embed every chunk OOM'd. Two complementary
  guards:

  - **Content-detection guard (path-less only).**
    `detect_language_from_content` now returns `None` for files that look
    machine-emitted — an `@generated` / `sourceMappingURL` marker, or a minified
    (very long, >2000-char) line — so they fall back to `sentence_aware`
    (bounded) instead of being symbol-parsed. Explicit signals (`cfg.language`,
    `metadata['language']`, a real path) **bypass** the guard, so a caller can
    still force such a file through.
  - **Per-file symbol-chunk cap.** When a document would emit more than
    `max_symbols_per_file` symbol chunks, the chunker logs a warning and falls
    back to `sentence_aware` with `fallback_reason="too_many_symbols"`. Catches
    pathological generated files regardless of how the language was resolved.

  Under defaults, the 2,500-function generated `.ts` now yields 62 bounded
  chunks (was 2,500) and a 46 KB minified one-liner yields 23; normal code is
  untouched.

### Added

- **`SymbolAwareChunker.max_symbols_per_file`** (default `2000`, `null` to
  disable, must be `>= 1`) — caps symbol chunks per document. The default
  catches generated files while leaving even very large hand-written sources
  alone (real code rarely exceeds a few hundred top-level symbols).

## 0.9.0 — 2026-06-09

Two correctness/perf fixes. The minor bump is for the **search-pool default flip**
(#64): read pooling is now on by default, so search callers that previously opened
a fresh connection per call now reuse warm connections — a transparent, opt-out
behavioral change worth a version signal. Also fixes `symbol_aware` silently
dropping symbols for path-less documents (#69). Python-only this release; the Rust
crate is a lockstep version bump with no functional change.

### Fixed

- **`symbol_aware` no longer mislabels path-less code as `unsupported_language`
  (#69).** The chunker resolved language *only* from a file extension in
  `doc.metadata.path` / `source_path` or a path-shaped `doc.id`. Callers passing a
  synthetic id / `stele://` URI with no path (e.g. pg-raggraph / bento) got
  `fallback_reason="unsupported_language"` and **zero symbols** for ordinary
  sources — ~45% of `.py` and ~90% of `.ts`/`.tsx` files in a real repo. Language
  is now resolved in layers, most- to least-explicit:

  1. a new `SymbolAwareChunker.language` config override (validated against the
     known language tags),
  2. a `doc.metadata['language']` hint (exact tag or an extension alias like
     `"tsx"` / `".ts"` / `"py"`),
  3. a broadened set of path-like metadata keys (`file_path`, `filename`, `uri`,
     `url`, `rel_path`, … in addition to `path` / `source_path`),
  4. a path-shaped `doc.id` (unchanged), then
  5. a conservative **content heuristic covering all ten supported languages**
     (python, java, go, typescript, javascript, rust, c, cpp, csharp, ruby).

  The content heuristic scores language-distinctive markers and returns a result
  only on a clear, unambiguous winner — prose and near-ties return nothing and
  fall back as before, so `symbol_aware_fallback` now fires only for genuinely
  unsupported input. A wrong guess can never do worse than the prior
  unknown-language fallback.

### Changed

- **Read-connection pool is on by default (#64).** The opt-in
  `CHUNKSHOP_SEARCH_POOL` pool — the biggest single search win (hybrid median
  30.8 ms → 10.5 ms, **−66%**, byte-identical ranking) — now ships **on** and is
  transparent. Set `CHUNKSHOP_SEARCH_POOL=0` (also `false`/`no`/`off`) to restore
  the historical per-call connect. Made safe with three guards:

  - **Retry-once on a broken connection.** A *reused* idle connection that turns
    out dead (server restart / idle timeout) is discarded and the query retried
    once on a fresh connection, so a restart self-heals instead of surfacing an
    `OperationalError`. A *fresh* connection that fails is a real error and is not
    retried. Validated against real Postgres by terminating a pooled backend.
  - **Fork reset.** An `os.register_at_fork` child handler drops inherited
    connections (psycopg sockets do not survive `os.fork`). The subprocess
    orchestrator spawns via exec and never inherits the pool.
  - **Max-idle-age recycle.** A connection idle past 300 s is recycled on acquire
    rather than handed out stale.

### Added

- **`SymbolAwareChunker.language`** — force one language for every document in a
  cell, bypassing per-document detection. Must be a known codeparse language tag;
  rejected at config-load otherwise.

## 0.8.3 — 2026-06-04

The local `files` source learns **incremental ingest**: point it at a directory
and re-runs reprocess only new and changed files, pruning the chunks of deleted
ones, instead of re-embedding the whole corpus every run. Works for prose and
local source code alike (same source, content-agnostic cursor). Python-only this
release; the Rust crate is a lockstep version bump with no functional change.

### Added

- **Incremental `files` source.** `FilesSource` now implements the
  `IncrementalSource` and `PrunableSource` protocols (joining `s3` / `http` /
  `pg_table`). An opt-in `source.incremental` block lets `chunkshop ingest`
  itself skip-and-prune via a JSON cursor sidecar — no external consumer loop:

  ```yaml
  source:
    type: files
    glob: ./corpus/**/*.md
    id_from: path            # path or sha1 — not stem — with incremental
    incremental:
      cursor_path: ./.chunkshop/files-cursor.json
      detect: hash           # sha256 of bytes (survives git checkout); or `mtime`
  ```

  - **Change detection.** `detect: hash` (default) compares a sha256 of each
    file's bytes — reliable across `git clone` / `checkout`. `detect: mtime`
    skips unchanged files by `(mtime, size)` without reading them (faster, but
    unreliable on git work-trees where checkout rewrites mtimes).
  - **Deletions.** Files removed from disk have their chunks pruned, scoped to
    the cell's `source_tag` (`PrunableSource.iter_deleted_since`).
  - **Crash-safe.** The cursor is written atomically (temp file + rename) and
    only after a fully successful run; a crash leaves the prior cursor intact
    and the next run re-upserts idempotently. A `doc_limit`-truncated run does
    not advance the cursor.
  - Stdlib only — **no new runtime dependency**. Library API + worked consumer
    loop in `docs/cookbook/incremental-sources.md`; CLI setup, a full pattern
    write-up, and a no-database quickstart in `docs/incremental.md` (Pattern G)
    and `docs/samples/incremental-files/`.

### Notes

- The incremental feature is **Python-only** this release; Rust parity is a
  separate follow-up. `chunkshop-rs` is version-bumped to 0.8.3 for a lockstep
  release only.
- Remote sources (`s3` / `http` / `pg_table`) already had incremental sync, and
  the `github` connector already declares cursor sync — unchanged here.

## 0.8.2 — 2026-05-31

Performance pass on the read and write hot paths — measured before/after, no
change to output. Ingestion gets ~24% faster on many-small-doc corpora from sink
connection reuse; hybrid search runs its two legs concurrently (~24% lower median
latency) with an opt-in connection pool for high-QPS callers (~66% lower median).
Ranking output and ingested data are byte-identical; the full test suite is green
with no new failures. Method + A/B numbers: `docs/perf-optimization-2026-05-31.md`.

### Performance

- **Ingestion: `PgSink` reuses one write connection across documents** instead of opening and tearing one down per document. At ~5 ms/connect that was up to ~40% of non-embed time on many-small-doc corpora (chat, messages, records). The connection is opened lazily and **still COMMITs per document**, so the crash-safety and live-progress contracts are unchanged (a committed row stays visible to other sessions; a mid-run crash still only loses the in-flight doc). On any write error the transaction is rolled back and the connection dropped so a poisoned transaction can't leak into the next document. Measured **−24% wall** on a 200-doc / 1-chunk-per-doc corpus with `embedder.threads` held constant. Backed by `backends/postgres.py:new_connection()` (raw, caller-owned), `PgSink.close()`, and a `finally` in `runner.run_cell`. The win scales with docs ÷ chunks — largest for many small docs, smaller for few large ones.
- **Search: `hybrid_search` runs the semantic and FTS legs concurrently.** The two legs are independent, side-effect-free `SELECT`s, so they now run on a small thread pool (one worker per extra leg) instead of sequentially; psycopg releases the GIL during server I/O, so a 2-leg hybrid drops from `sum(legs)` to ≈`max(legs)`. **−24% median latency**, transparent and default-on. Fusion consumes the same per-leg results, so the ranked output is **byte-identical** to the sequential path. Single-leg queries stay inline (no thread overhead).
- **Search: opt-in read-connection pool (`CHUNKSHOP_SEARCH_POOL=1`).** Connection *setup* (~5–6 ms/leg), not the queries, dominates search latency. Setting this env var routes the hot read legs (`semantic_search`, `keyword_search`) through a tiny thread-safe idle-connection pool keyed by DSN (autocommit reads — nothing lingers idle-in-transaction; an errored connection is closed, never recycled; `chunkshop.search.close_search_pool()` drains it). **Default OFF** preserves the documented per-call-connect behavior byte-for-byte. Measured **−66% median hybrid latency** with the flag on. See `docs/hybrid-search.md` § Performance.

### Testing

- New `tests/chunkshop/test_search_pool.py` pins the pool lifecycle: reuse when enabled, a fresh connection per call when disabled, never pool a poisoned (errored) connection, and drain on `close_search_pool()`.
- `tests/chunkshop/test_pg_document_store.py` mocks updated to the `new_connection` write path (the sink no longer connects per document); the `_FakeConnection` gained `closed` / `rollback` / `close` to exercise the reuse-and-recover path.

### Notes

- **No API changes and no new runtime dependencies.** The search pool is the only new knob and it is opt-in via env var; the pool is hand-rolled (stdlib + psycopg) rather than pulling in `psycopg_pool`.
- Two research write-ups ship as docs only (no code): `embedder.threads` tuning for single-cell ingest in `docs/perf-optimization-2026-05-31.md`, and a third-party-benchmarked speed-vs-accuracy analysis of `caveman` filler-word reduction (BEIR: ~1–2% NDCG for ~25% cheaper embedding) in `docs/caveman-filler-word-reduction-2026-05-31.md`.
- Five follow-up performance ideas are tracked as issues #64–#68 (default-on search pool, COPY bulk-insert, length-bucketed embedding batches, warm-model search daemon, HNSW `ef_search`).

## 0.8.1 — 2026-05-30

### Added

- **Opt-in remote embedder (`type: openai`).** A second embedder type that calls any OpenAI-compatible `/v1/embeddings` endpoint instead of running locally — covers OpenAI, Azure, Voyage (Anthropic's recommended provider), Mistral, Together, and local servers (TEI / vLLM / Ollama) via `base_url` + `model` + optional `api_key_env` (bearer token read from an env var; keyless for local). `fastembed` remains the default — select the remote one per cell with `embedder.type: openai`. Uses stdlib HTTP only — **no new runtime dependency**. Batches by `batch_size`, reorders the response by `index`, retries on 429/5xx with exponential backoff, and validates the returned dim against config. Anthropic has no embeddings API (its ecosystem answer is Voyage); Cohere's distinct `/v1/embed` shape is not supported. See `docs/reference/embedder-openai.md` + `docs/samples/sample-openai-embedder.yaml`.

## 0.8.0 — 2026-05-30

Code intelligence goes wide and precise. The `codeparse` layer grows from 5 to
**10 tree-sitter languages** (adds Rust, C, C++, C#, Ruby); cross-file edges get
**import-aware resolution** (ambiguous name matches narrow to the module the
caller actually imports); and the code graph is **hardened** — an orphan-edge
bug is fixed and locked down by corpus-scale invariants validated against real
codebases (chunkshop's own Rust tree + Postgres 16.3, 250k call sites, zero
orphans). Also lands typed `edge_kind`, `provenance` tagging, and `scope_chain`
display metadata on the `code_edges`/symbol path.

### Added

- **`code_relationships`: import-aware narrowing of ambiguous cross-file edges.** When a callee/base name matches symbols in more than one file, the resolver previously fanned out one `CALLS`/`INHERITS`/`IMPLEMENTS` edge per candidate (`resolution='ambiguous_name'`). It now consults the caller file's imports (already parsed but previously discarded) and, when exactly one candidate's module is imported, emits a single precise edge tagged `resolution='import_resolved'` at the unique-match confidence band. The narrowing is conservative and language-agnostic — it matches a candidate file's *stem* against the caller's import tokens (works for Python `from a import x`, Rust `use crate::a::x`, C `#include "a.h"`, etc.); zero or multiple import-supported candidates keep the existing fan-out, so no edge is ever dropped. `provenance` stays `'heuristic'` (an import-narrowed edge is a stronger heuristic, not AST/SCIP truth) — consumers that want to rank it higher key on `evidence.resolution`. This is the Python-path read of #42; SCIP/stack-graphs resolution remains a Rust follow-up.
- **`codeparse`: tree-sitter extractors for Rust, C, C++, C#, and Ruby.** The `[code]` extra now ships `tree-sitter-rust`, `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-c-sharp`, and `tree-sitter-ruby` alongside the existing Python/Java/Go/TS/JS grammars, taking `symbol_aware` chunking + `code_relationships` cross-file edges from 5 to 10 real-parser languages. Rust groups methods under their `impl`/`trait` type (`struct`/`enum` → `class`, `trait` → `interface`); C emits functions + structs; C++ adds inline + out-of-line (`Class::method`) methods and namespaces; C# maps `class`/`interface`/`method_declaration` and resolves calls via `invocation_expression`; Ruby maps `def`/`class`/`module` with best-effort `call`-node call detection. Each extractor attributes calls to the **outermost** emitted symbol (no orphan edges) and parses lazily — the base install is unaffected, and `regex_fallback.py` remains the safety net when the `[code]` extra is absent. A parametrized invariant test enforces no-orphan-callers + in-bounds spans across all five.
- **`code_relationships` extractor: typed `edge_kind` column on `code_edges` (CS-2).** The PG `code_edges` table now carries a typed, codegraph-aligned `edge_kind` column (12-value `CHECK` constraint: `contains`, `calls`, `imports`, `exports`, `extends`, `implements`, `references`, `type_of`, `returns`, `instantiates`, `overrides`, `decorates`) alongside the existing uppercase `edge_type` column. Today's three emission paths (`CALLS`, `INHERITS`, `IMPLEMENTS`) map to `calls`, `extends`, `implements`; the other nine values are valid against the constraint but unfilled until CS-1 ports the 20-language extractor stack. `chunkshop.extractors.code_relationships` exposes `EdgeKind` (Literal), `EDGE_KINDS` (tuple), and `edge_type_to_kind()` as the source-of-truth for the ontology.
- **`chunkshop impact-of --edge-kind <kind>` filter.** New CLI option validated against the 12-value EdgeKind set; ANDs into the recursive-CTE WHERE alongside the existing `--edge-type`. `--edge-kind` is `None` by default — pre-CS-2 invocations are byte-identical.
- **`codeparse`: Go / TypeScript / JavaScript now parse via tree-sitter (#40).** The `[code]` extra ships `tree-sitter-go`, `tree-sitter-typescript`, and `tree-sitter-javascript` alongside the existing Python + Java grammars. The lossy per-language regex extractors for these three are replaced with real tree-sitter walks matching the `python.py` / `java.py` pattern. Concretely: Go methods now resolve their receiver type as `parent_name` (`func (c *Calculator) Add` → `parent_name='Calculator'`, `symbol_type='method'`) instead of landing as parentless functions; TS/JS methods carry real multi-line `line_start`/`line_end` spans instead of single-line collapses; struct/interface types are typed as `class`/`interface`. `regex_fallback.py` stays as the safety net — environments without the `[code]` extra (or where a grammar raises) fall through to regex transparently, and `ParseResult.parser` reports `"regex"` vs `"tree-sitter"` accordingly.
- **`symbol_aware` chunker: `scope_chain` display metadata (#41).** Every symbol chunk now carries `metadata.scope_chain` — a human-readable enclosing-scope path (`svc > UserService > get_user`) derived from the same inputs as `fqn` via the new `chunkshop.codeparse.build_scope_chain`. `fqn` stays the machine-readable graph join key; `scope_chain` is the UI/search-result display string. Additive — no existing metadata key changes.
- **`code_relationships` extractor: `provenance` + `provenance_metadata` columns on `code_edges` (CS-5).** The PG `code_edges` table now carries provenance tagging — a typed `provenance text NOT NULL DEFAULT 'ast'` column (3-value `CHECK`: `'ast' | 'scip' | 'heuristic'`) plus a `provenance_metadata jsonb NOT NULL DEFAULT '{}'` column for per-edge per-channel context (e.g., `{synthesizedBy: 'react-render', componentName: 'App'}` once CS-3 synthesizers land). Every edge from today's AST extractor is hardcoded to `provenance='ast'` with empty metadata. Foundation for CS-3 — without provenance, an AST-truth edge and a heuristic-guess edge are indistinguishable, and per codegraph's CLAUDE.md "partial coverage is WORSE than none" if you can't tell which is which. `chunkshop.extractors.code_relationships` exposes `Provenance` (Literal) and `PROVENANCES` (tuple).

### Notes

- `edge_type` is unchanged: same column name, same uppercase values, same primary-key membership, same write semantics. Existing readers (`chunkshop impact-of --edge-type`, `pg-raggraph` consumers, `pg-raggraph/tests/integration/test_chunkshop_bridge.py`) continue working untouched.
- Cross-backend extension (MariaDB / SQLite / ClickHouse) is a separate follow-up brief blocked by a backend-agnostic `code_edges` DDL refactor — see `skill-output/mission-brief/Mission-Brief-cs2-cross-backend.md`.
- Rust parity is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs2-rust-parity.md`.
- CS-5 is strictly additive on top of CS-2 — `edge_type`, `edge_kind`, and the `code_edges` PRIMARY KEY are byte-identical to the post-CS-2 state.
- Cross-backend extension (MariaDB / SQLite / ClickHouse) is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs5-cross-backend.md`. Should be bundled with `Mission-Brief-cs2-cross-backend.md` since they share the backend-agnostic DDL-seam refactor.
- Rust parity is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs5-rust-parity.md`. Blocked on `Mission-Brief-cs2-rust-parity.md` (which creates the `rust/chunkshop/src/extractors/` directory CS-5's Rust port lives in).
- No CLI surface in this PR — `chunkshop impact-of --provenance <kind>` filter is YAGNI until CS-3 produces non-AST edges to filter against.

### Changed

- **`code_relationships`: name-heuristic cross-file edges now tagged `provenance='heuristic'` (#42 SC-004).** Previously every `finalize()` edge was hardcoded `provenance='ast'`. Now only AST-direct intra-file edges (`evidence.resolution == 'intra_file'`) keep `'ast'`; cross-file edges resolved by unique-/ambiguous-name matching (CALLS, INHERITS, IMPLEMENTS) are tagged `'heuristic'`. This separates name-heuristic edges from a future Rust stack-graphs resolver (`'scip'`) sharing the same `code_edges` table. Schema unchanged — the `provenance` CHECK already permitted `'heuristic'`. The `_emit` chokepoint param is typed `Provenance` (not `str`).
- **A/B emission contract: §4.6 verdict qualified by new §4.6.1.** The "NAIVE WINS" verdict (PR #45) tested 2 of 3 retrieval modes defined in §4.2 (`naive_vector` + `graph_leg`-as-primary). The `hybrid` mode (vector-first then graph-expansion — chunkshop's intended production shape, per §4.2 "optional but recommended") was not run. The new §4.6.1 documents this gap, explains why graph-as-primary's failure profile (NER fallback to whitespace tokens skipped 7/12 questions by construction) doesn't extrapolate to hybrid, and **puts §3.8's "freeze edge-tier work / deprioritize RM-C / reconsider facts/cooccur" directive ON HOLD** pending a hybrid-mode re-run. Tracking issue filed against pg-raggraph.

### Fixed

- **`codeparse`: calls inside nested functions now attribute to the outermost emitted symbol (Risk 1).** Previously `_enclosing_function` returned the innermost function, so a call inside a nested function produced a `CALLS` edge whose `caller_node_id` referenced a symbol that was never emitted (an orphan edge source). Fixed for Python and the ECMAScript family (TypeScript + JavaScript, which share the walker). Go/Java were already structurally safe (no nested function *declarations*).
- **`codeparse`: Python symbol spans now include decorator lines (Risk 2).** A decorated `def`/`class` previously began at the `def`/`class` line, dropping `@decorator` lines from the symbol's `original_content` and `start_line` metadata. The span now starts at the first decorator (the `decorated_definition` node).
- Added a corpus-scale invariant test (no orphan callers, in-bounds spans, no parse crashes, deterministic node_ids) over chunkshop's own source tree, plus realistic per-language fixtures exercising nesting + decorators. This is the regression net that hardens the extractor pattern before it is replicated across new languages (sub-project A).
- Real-code validation for the new extractors: `test_rust_corpus.py` parses chunkshop's own `rust/` tree (~120 files, ~1.3k symbols, ~9.4k calls) in CI; env-gated `test_c_corpus.py` validated against **Postgres 16.3** `src/` (1,269 `.c` files, 25,431 symbols, 250,000 call sites) — **0 orphans, 0 out-of-bounds, 0 crashes, 0 regex fallback**. Go/Java closure/lambda orphan-safety and Rust `use`-based import narrowing also get dedicated tests.

## 0.7.0 — 2026-05-27

Agent-memory fact extraction goes batteries-included, plus a read-time
reducer, a fact query command, and a security fix in the github
connector. The `chunkshop` wheel and `chunkshop-rs` crate are bumped in
lockstep; the Rust crate has no behavioural change this cycle (all code
changes are Python-side).

**Bundled fact extractors (mem0-style).** Previously the
`ConsolidationChunker` could only produce facts via the `passthrough`
default (none), a bring-your-own `CallableConsolidator`, or the bundled
`extractive` consolidator (propositions with null subject/predicate/object).
Two first-class, CPU-only extractors now ship:

- **`consolidator: { mode: lede }`** — salient-sentence propositions with
  a rank-decay confidence (needs the `[lede]` extra).
- **`consolidator: { mode: lede_spacy }`** — dependency-parsed
  subject/predicate/object triples (verb-lemma predicate; 1.0 full-SVO /
  0.6 partial confidence; captures direct, prepositional, and copular
  objects). Needs the `[lede-spacy]` extra + a spaCy model.

Both are hybrid with the existing `CallableConsolidator` escape hatch and
support a `confidence_floor` (drop low-confidence facts before embedding).
An optional summarizer slot fills the episode summary independently of the
fact extractor.

**`caveman` reducer.** A new dependency-free fluff/stopword reducer on the
summarizer contract (`chunkshop.summarizers.caveman`), swappable anywhere
lede is. Exposed at read time via `chunkshop search --compress` (off by
default), which strips low-information tokens from the produced summary.

**`fact-search` command + fact-aware search.** `chunkshop fact-search`
queries `kind='fact'` rows and returns each fact with its originating
chunk → doc breadcrumb (and optional lede summary), with a
`--confidence-floor`. Normal `chunkshop search` now **excludes facts by
default** (`--include-facts` to opt back in), via a new `metadata_not`
WHERE predicate (`IS DISTINCT FROM`, a no-op for non-memory tables).

**`cooccurrence` extractor (Tier-1 graph edges).** A spaCy-free extractor
that pairs rake keyphrases (nodes) co-occurring within lede-salient
sentences into weak, undirected `co_occurs` candidates, emitted as
`metadata['cooccur']` (`{a, b, weight}`, word-boundary matched) for a
downstream graph consumer to materialize.

**Security — github connector scrubs inlined PATs (#31).** The verified
GitHub connector inlined the PAT into the HTTPS clone URL; on a `git`
failure the token leaked through `CalledProcessError`'s argv/stderr into
logs and exception trackers. Inlined credentials are now scrubbed from
argv + captured stdout/stderr before the error is raised.

## 0.6.2 — 2026-05-26

Connectors papercut. No core API changes; the `chunkshop` wheel and
`chunkshop-rs` crate are bumped in lockstep with no behavioural change
to either.

**Connectors — gdrive explicit `file_ids` selection mode.** The
verified Google Drive connector gains a second selection mode for
single-file / multi-select ingest (e.g. the rows a UI file-picker
selected), alongside the existing `folder_id`/`query` folder walk:

- **`file_ids: [<id>, ...]`** — ingest exactly the given Drive file IDs.
  Each is fetched directly via `files.get` (no folder walk, no
  `/changes` feed). Mutually exclusive with `folder_id`/`query`.
- **Modified-time delta sync.** Cursor is a `{file_id: modifiedTime}`
  map; on re-sync only files whose `modifiedTime` advanced are
  re-emitted, and unchanged files retain their prior entry via the
  `IncrementalSource` merge contract.
- **`reprocess: true`** — force re-emit of every selected file
  regardless of `modifiedTime`, so the sink overwrites even unchanged
  documents.

## 0.6.1 — 2026-05-26

Rust parity + connector papercuts. No Python-package API changes; the
`chunkshop` wheel and `chunkshop-rs` crate are bumped in lockstep.

**Rust — SP-1 sync primitives + RawStore parity (RM-B).** Closes the
v0.6.0 Python-vs-Rust behavioural gap: `SyncMode` / `IncrementalSource`
/ `PrunableSource` / `StaleCursorError` / `Document.fingerprint`, the
`pg_table` tuple cursor (boundary-row safety), the `s3` ETag
`IncrementalSource`, the `http` depth-crawl + ETag/Last-Modified cursor
+ robots.txt, and the `RawStore` primitive (filesystem + S3). 40 new
Rust tests; a Python↔Rust parity test asserts identical chunk output.

**Connectors — GitHub connector fixes.**
- **Auto-detect default branch (#27).** Omit `branch` to resolve the
  repo's `default_branch` via `GET /repos`; a wrong pinned branch now
  falls back to the default and retries instead of 404-ing. Opt out
  with `branch_strict: true`.
- **Clone-based walk (#28).** Set `clone: true` to `git clone --depth 1`
  the branch once and walk the tree locally, instead of one
  `/contents` API call per file — a single fetch regardless of file
  count. Bounded by `max_clone_mb` (default 200); falls back to the
  REST walk if the `git` binary is unavailable.

**Security.** The 3 Dependabot advisories tracked in #11 (urllib3
high+high, idna medium) were remediated in 0.6.0 (`urllib3` 2.7.0,
`idna` 3.16); confirmed zero open alerts.

## 0.6.0 — 2026-05-25

Connector platform + code understanding. Adds the SP-1 plugin-foundation
(IncrementalSource / PrunableSource / SyncMode Protocols + entry-point
registry with per-plugin import isolation + RawStore primitive + OAuth
interfaces), four verified-tier connectors via the new in-monorepo
`chunkshop-connectors` plugin package (blob, rss, github, gdrive — the
last two with real-OAuth e2e demos), 23 experimental-tier stubs
registered behind the same seam, SP-3 file rich-parsing (PDF / DOCX /
PPTX / XLSX / HTML behind opt-in extras), depth-bounded URL crawl with
ETag + Last-Modified incremental cursors, two new code-aware chunkers
(`code_aware` stdlib-ast for Python, `symbol_aware` tree-sitter for
Python/Java + regex fallback for Go/TS/JS), two new code-aware
extractors (`code_relationships` with cross-file edge resolution +
`write_edges` materialization, `code_summary` with lede/callable/
first-N-sentences backends), CLI `search --by-symbol` filter +
`chunkshop impact-of` subcommand with recursive-CTE N-hop traversal,
runner integration that auto-fires `extractor.finalize()` to materialize
the `code_edges` table.

**Test coverage**: core suite 530 → 701+, connectors suite 7 → 140
(verified-tier behavioral + experimental smoke + chunker×extractor
orthogonality matrix + attribution audit + e2e), real-world 5-KB
integration demo (3 GitHub repos + cross-cutting MD + 5 arxiv PDFs +
4 LLM-MD + ClickHouse → 5 hybrid-searchable KBs in ~14 minutes).

**Verified benefit**: 98.2% token reduction vs grep+load with higher
precision@5 across 10 realistic engineering queries
(`docs/benchmarks/grep-vs-hybrid-2026-05-25.md`).

**Pre-existing skips graduated**: the 6 lede/lede-spacy tests are now
skip-not-fail when the spaCy model isn't installed (CLAUDE.md's stale
"6 failures" note updated). CI now installs the full extras set
(`[code]`, `[lede-spacy]`, `[all-parsers]`, chunkshop-connectors
plugin) + `en_core_web_sm` so those tests actually run in CI.

**Security**: closes 3 Dependabot advisories (urllib3 high+high,
idna medium) via `uv lock --upgrade-package`.

Full per-commit changelog: `docs/CHANGES-2026-05-25.md`.
Agent reference (single self-contained doc for LLM consumers):
`docs/AGENT_REFERENCE.md`.

## 0.5.0 — 2026-05-22

Search lands. This release adds lede v0.4 hint-biased extraction, a hybrid
retrieval surface across all four backends, and a configurable "Fast-mode"
RAG path that summarizes retrieved chunks before they reach an LLM (~90%
input-token savings on real corpora). All Python; the Rust crate version is
bumped in lockstep but is unchanged this release.

### lede v0.4 hint-biased extraction

- **Hint kwargs flow through `summary_embed`.** `hints` / `hint_focus` /
  `hint_mode` pass through the existing `chunkshop.summarizers.lede` shim to
  bias extractive summaries toward query terms. No-hint output is
  byte-identical to before (golden-tested).
- **Per-document hints.** `CallableSummarizer` gains `hints_from_meta` /
  `hint_focus_from_meta` / `hint_mode_from_meta` — pull hints from each
  document's metadata, overriding static kwargs.
- **`lede_top_terms` extractor.** Ranked salient words/phrases via lede 0.4.1
  `top_terms(with_scores=True)` (real composite scores, unified kind ranking)
  into `tags` + `metadata['top_terms']`.
- **Hint expansion.** `chunkshop.hints.expand_hints` shim + an `expand:` block
  on the summarizer/extractor (lemma/synonyms/similar via lede-spacy ≥0.4.2).
  Optional `[lede-spacy]` extra; lede/lede_spacy are imported only inside three
  designated shim modules (grep-enforced).

### Hybrid search surface (`chunkshop.search`)

- **`semantic_search` / `keyword_search` / `hybrid_search`** on Postgres,
  SQLite, MariaDB (full ranked FTS) and ClickHouse (degraded token-filter),
  with RRF + weighted fusion, candidate over-fetch, and a metadata/source/tags
  `where` filter (filter-only, not a ranking leg). FTS uses OR-joined terms.
- **`summarize_hits`** — heading-aware Fast-mode summary: collapses the top-K
  retrieved chunks into one query-biased summary, prepending deduped chunk
  headings so extractive summarization keeps structural facts (titles/captions).
- Benchmarks + best practices: `docs/fast-mode-rag-benchmarks.md`.

### Search product (configurable)

- **`target.fts: {enabled, language}`** — opt-in FTS index built at ingest
  (create modes) or validated on append, across all four backends. Default off;
  absent `fts` ⇒ ingest is byte-identical.
- **`chunkshop search` CLI** — `--query`, `--k`, `--return chunks|summary+chunks|summary`,
  `--legs`, `--where KEY=VALUE`, `--json`. Loads the cell's embedder, embeds the
  query, runs hybrid search, optionally summarizes.
- **`SearchResult` + `search()`** — typed `{chunks, summary, query}` return with
  three modes; summary hints auto-derived from the query (overridable), summary
  via the injectable lede summarizer. `chunks` mode imports no lede.
- Guide: `docs/hybrid-search.md`.

## 0.4.5 — 2026-05-20

The Rust agent-memory port lands. The rest of the release is docs +
ergonomics that came out of integrating the new memory surface.

### Rust — RM-A (agent memory port; chunkshop#9)

Rust now has full SP-A parity. The crate (`chunkshop-rs`) ships the same
two-cell agent-memory primitives the Python package shipped in 0.4.4:

- **`chunkshop::memory::{stage_event, stage_events, ensure_staging_table,
  prune_staging, derive_event_id}`** — async sqlx staging API. The
  `event_id` derivation is **byte-identical** to Python's
  (`sha1("session_id\x00disambig\x00content")`), so a Python-staged
  event and a Rust-staged event with the same canonical tuple dedupe
  cleanly via `ON CONFLICT (event_id) DO NOTHING`.
- **`SessionStagingSource`** — session-level WHERE for consolidate mode
  from day 1 (the O1 latent-correctness bug fixed in Python `49861dc`
  cannot recur in Rust because the test exists from the first commit).
- **`SessionEpisodeFramer`** — gap/turn/word/tool boundary segmentation.
- **`Consolidator` trait + `ExtractiveConsolidator`** — zero-network
  default; LLM/custom consolidators are host-wired at compile time
  (not via YAML `module:`/`function:` callable — see spec §3.4).
- **`ConsolidationChunker`** — episode + per-triple fact emission with
  O4 passthrough on consolidator error.
- **`MemorySink`** — extends PgSink with tier/kind/namespace/recorded_at
  stamping, namespace-qualified row id (`{ns}::{doc_id}::{seq_num}` —
  Python `3dbd12f` parity), supersede (consolidated → DELETE prior
  rows scoped by source_tag), soft-invalidate (newer contradicting
  fact retracts older).
- **`O3 deferred-watermark advance`** — `iter_documents()` stores the
  pending watermark; `commit_processed()` (called by the runner after
  the per-doc write loop succeeds) actually issues the UPDATE. A
  mid-loop crash leaves the watermark unadvanced — same crash-safety
  contract Python's generator-yield semantics provide.
- **Preset YAMLs** at `rust/chunkshop/configs/memory/{realtime,consolidate}.yaml`
  — byte-for-byte parity with Python's, EXCEPT the consolidator section
  which uses `mode: extractive` (vs Python's `mode: callable`).

Feature-gated via new Cargo feature `memory = ["source", "sink"]`,
included in `full`.

Tests: 30 unit (config + framer + consolidator + chunker + iso helpers)
+ 22 PG integration (staging, source with O1, sink with supersede/
soft-invalidate, e2e composing the full pipeline, O1+O3 resilience).
All green. Spec: `docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md`.

### CLI

- **`validate` detects bakeoff configs.** Previously, `chunkshop validate
  --config docs/samples/bakeoff.yaml` printed a wall of cryptic pydantic
  `extra_forbidden` errors because `validate` always assumed an ingest-
  cell shape. Now it sniffs the YAML for the two fields unique to
  `BakeoffConfig` (`matrix` + `gold_queries`) and dispatches to the
  right schema:

      $ chunkshop validate --config docs/samples/bakeoff.yaml
      [validate] OK (bakeoff config) — 'samples_bakeoff'
        source:   files
        matrix:   3 embedders × 2 chunkers × 3 targets = 18 combos
        targets:  ['postgres', 'mariadb', 'sqlite']

  Closes #10. Ingest-cell path is unchanged.

### Docs

- **`docs/architecture/memory-sink.md`** — full user-facing architecture
  write-up for MemorySink. Data-flow diagram, two-tier semantics, row
  identity + namespace scoping, the O1 data-loss bug walk-through,
  crash-safety (O3), consolidator seam (Python callable vs Rust trait),
  pg-raggraph fact contract, explicit out-of-scope list.
- **`docs/samples/memory-scheduling/`** — four working scheduling
  patterns for the two-cell pattern: `cron/` (cron file + systemd
  `.service` / `.timer` units), `k8s-cronjob/` (ConfigMap + two CronJob
  manifests, sized appropriately), `in-process-python/` (asyncio
  scheduler + FastAPI lifespan integration + end-to-end demo),
  `in-process-rust/` (tokio mirror, axum integration + demo).
- **`docs/incremental.md`** Agent-memory section links to both.

### Issue references

- Closed: #9 (RM-A wave), #10 (validate bakeoff configs).

## 0.4.4 — 2026-05-19

Agent memory (SP-A) lands, plus consumer-ergonomics fixes that came out
of integrating the new memory surface with downstream tools.

### Agent memory primitives (SP-A)

- **`chunkshop.memory`** — staging API (`stage_event`, `stage_events`,
  `ensure_staging_table`, `prune_staging`), `SessionStagingSource`,
  `SessionEpisodeFramer`, `ConsolidationChunker`, `MemorySink` with
  `tier` (`provisional`/`consolidated`), supersede, and soft-invalidate
  semantics. Writes to `agent_memory.memory` with the pg-raggraph fact
  contract (`subject`/`predicate`/`object`/`support_span`/`confidence`/
  `effective_from`/`effective_to`/`retracted`/`retracted_at`/`extractor`/
  `namespace`). Design spec: `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`.
- **Two-cell pattern.** `memory/realtime.yaml` (run frequently) writes
  `tier='provisional'`; `memory/consolidate.yaml` (run nightly via
  external cron) segments quiet sessions into episodes, extracts facts
  via a user-wired callable consolidator, and supersedes the provisional
  rows for that session. No daemon; same scheduler model as every other
  chunkshop pattern.
- **`chunkshop.memory.read_pre_chunked(dsn)`** — read the consolidated
  store back out in the shape pg-raggraph's `GraphRAG.ingest_records()`
  accepts. Episode rows become `pre_chunked` entries; fact triples
  become `known_relationships` + synthesised `known_entities`. O2
  (consolidated-wins) and retracted-aware defaults applied at the
  read layer. End-to-end example: `docs/samples/memory-to-pgraggraph/`.
- **Critical correctness fix (O1).** Consolidate now selects whole
  sessions when a session has any new event, not at row granularity.
  Original implementation row-filtered: a late turn arriving after
  a session was already consolidated would re-select only that row,
  and `MemorySink`'s destructive supersede would replace the prior
  consolidated memory with just the fragment. The spec-required O1
  test caught this before merge; see `python/tests/chunkshop/test_memory_resilience.py`.

### Ergonomics / packaging

- **Lazy backend imports** in `chunkshop.sources`. `pip install chunkshop`
  with no extras no longer drags in `pymysql` / `sqlite-vec` /
  `clickhouse-connect` / `boto3` at import time — the four optional
  backend modules are imported only inside their `load_source`
  dispatch branch (mirrors the pattern `chunkshop.sinks` already used).
  Closes #7.
- **NDCG@k in bakeoff scorer.** `score_query()` emits `ndcg_at_K`
  alongside `recall_at_K` and `mrr`, single-relevant-item formulation
  (IDCG=1, so NDCG@k = 1/log2(rank+1) when gold ≤ k, else 0). No
  external deps. Aggregates flow through unchanged. Closes #8.

### Rust

- NDCG@k parity in `rust/chunkshop/src/bakeoff/score.rs` (mirrors the
  Python addition; same single-relevant-item formula, same key shape).
- Lazy-import equivalent is already idiomatic Rust via Cargo features
  (`#[cfg(feature = "source")]` and friends on every backend module).

### Internal

- Soft-invalidate / `_invalidate` SQL gained explicit `::timestamptz`
  casts so ISO-timestamp params adapt unambiguously against the
  `timestamptz` column. `extract(epoch FROM ...)::double precision`
  in the staging source so the framer keeps numeric gap arithmetic
  while metadata stays JSON-serialisable.
- Resilience-test coverage extended: O1 late-event rebuild and O3
  crash-resume (per-session commit, watermark-after-yield) now have
  dedicated tests.
- Tracked: chunkshop#9 (RM-A — Rust port of SP-A; latent until the
  Rust wave runs).

### Issue references

- Closed: #7 (lazy backend imports), #8 (NDCG in bakeoff scorer).
- Filed: #9 (RM-A, scope only).

## 0.4.3 — 2026-05-16

Batteries-included ergonomics for embedding chunkshop as a library and
for first-run installs. Two additive, fully backward-compatible changes
— existing YAML configs and `dsn_env`-based code keep working unchanged.

- **Direct `dsn` field on the target and `*_table` sources.** Accepts a
  literal connection string or `${VAR}` references expanded from the
  environment at connect time (only the exact `${NAME}` form — a bare
  `$` in a DSN password is left untouched). Takes precedence over
  `dsn_env`; if `dsn` is unset the legacy `os.environ[dsn_env]` lookup
  is used exactly as before. Lets library callers (e.g. a wrapper that
  builds `CellConfig` in code) pass a connection string without mutating
  `os.environ`. Security: a literal secret in `dsn` lands in the config
  file — prefer `${VAR}` or `dsn_env` for credentials.
- **`chunkshop prefetch --config X` command.** Downloads the embedder
  model named in a config up front so the multi-second fastembed ONNX
  fetch happens in an explicit setup step (Dockerfile / CI / install
  script) instead of silently inside the first ingest or library
  `store()`. Honors `HF_HUB_OFFLINE=1` to fail fast when uncached.
- Backward compatibility: backend constructors and `load_backend()`
  still accept the legacy `dsn_env=` keyword; the `dsn_env` path stays
  lazily resolved at `connect()`. `bakeoff` is unchanged (it keeps its
  own `dsn_env`-only target models by design).

## 0.4.2 — 2026-05-15

First release cut from `main` after the v4 modular-backend line was
merged back (PR #6). Brings main's five parallel Rust feature PRs onto
the modular-backend codebase and makes `main` the source of truth.
No Python API or schema changes vs 0.4.1 — the Python package is
functionally identical; this release is Rust features + repo/CI health.

- **`chunker-only` Cargo feature gate.** Library consumers can depend
  on `chunkshop-rs` for just the chunker structs without the
  embedder/source/sink/ML stack. `default = ["full"]` preserves
  backward compatibility. The whole modular sink/backend layer sits
  under the `sink` feature; `embedder` splits into `embedder-core`
  (BYO) and `embedder-hub` (hf-hub-backed). Re-applied from main PR #2.
- **`HierarchyChunker` custom heading regex** — `heading_pattern`
  config field overrides the default markdown heading detector.
  Re-applied from main PR #3.
- **`embedder-hub` feature split** — `hf-hub` is now opt-in separately
  from the core embedder. Re-applied from main PR #4.
- **Custom `BoundaryEmbedder` injection into `SemanticChunker`** —
  inject a boundary embedder without the fastembed/hf-hub path.
  Re-applied from main PR #5.
- **`fastembed` pinned `>= 5.13.1`** to align `ort` on `=2.0.0-rc.12`
  and prevent dep-resolution regressions under restrictive lockfiles.
  Re-applied from main PR #1.
- **CI now exercises the modular backends.** The workflow brought up
  only Postgres and installed no backend extras (it predated v4 and
  had never run against the modular code). Now brings up Postgres +
  MariaDB + ClickHouse from `docker-compose.test.yaml` and installs
  `all-backends`, so the full cross-backend matrix runs in CI.
- **Scenario fixtures migrated to the v4 target schema.** The 18
  `tests/sub` + `tests/use-cases` configs used the legacy 0.3.x
  `schema:` target shape; migrated to `type:` + `database:`.

## 0.4.1 — 2026-05-12

Polish + perf + ops layer on top of the 0.4.0 modular-backends release.
Headline is a **~43× speedup on MariaDB `query_top_k`** via a hybrid
euclidean/cosine path. CLI grows `validate` and `init` subcommands.
Per-backend docs gain Benefits / Limitations / Gaps / Troubleshooting
sections. `serde_yml` → `serde_yaml_ng` migration closes a Rust
dep-staleness gap.

- **MariaDB `query_top_k` hybrid euclidean/cosine path.** ~43× speedup
  at 8k chunks vs. the naïve cosine query. Cosine semantics preserved
  by ordering on `VEC_DISTANCE_EUCLIDEAN` against pre-normalized vectors
  (mathematically equivalent for unit-norm embeddings).
- **`chunkshop validate` subcommand** — parses a YAML config without
  running ingest; surfaces pydantic-shaped validation errors
  (PR-006, PR-008).
- **`chunkshop init` subcommand** — scaffolds a new YAML against a
  chosen backend template (PR-010, PR-014).
- **Multi-target bakeoff in Rust.** Single Rust binary runs the
  factorial matrix across all four backends (PR-018).
- **Per-engine docs** gain Benefits / Limitations / Gaps /
  Troubleshooting sections (Postgres, MariaDB, SQLite, ClickHouse).
  Architecture + README rewritten around the modular-backend story.
- **`docs/benchmarks/`** — measured performance + accuracy across all
  four backends, with raw result JSON frozen in-tree.
- **Default-install backend extras** + branded `ImportError` messages
  when a backend's deps aren't on the path (PR-002, PR-003, PR-011).
  Users on tight budgets opt out via `chunkshop[core]`.
- **`serde_yml` → `serde_yaml_ng`** migration (Rust). The former is
  unmaintained; the latter is the actively-developed fork. Public API
  unchanged (PR-001, PR-004).
- **Structured CLI logging.** Ad-hoc `print()` calls converted to
  `logging` (Python); chunker panic paths converted to `assert!` +
  rustdoc (Rust). No functional change (PR-005, PR-006).
- **ClickHouse append-mode warn-once on schema mismatch** instead of
  silent acceptance. Strict test mode for CI parity. Security + upgrade
  docs added per backend (PR-007, PR-009, PR-012, PR-013).

## 0.4.0 — 2026-05-10

**Modular backends ship.** chunkshop's sink layer is no longer
Postgres-only. **MariaDB**, **SQLite** (via `sqlite-vec`), and
**ClickHouse** all ship as first-class backends alongside Postgres —
same YAML, swap `target.backend`. The Rust port matches Python on every
backend.

Umbrella release for the R1 / R2 / R3 / R4 sub-projects plus the RT
cross-backend bakeoff matrix.

- **MariaDB backend (R2).** 11.7+ native `VECTOR` type. Full
  Sink / Backend / Source trait implementations. `MariadbTableSource`
  mirrors `PgTableSource`. Cross-language vector parity verified —
  Rust and Python round-trip through MariaDB produce byte-equivalent
  embeddings (`tests/parity/mariadb_*`, plus a manual walkthrough in
  `docs/parity/`).
- **SQLite backend (R3).** Backed by `sqlite-vec` `vec0` virtual
  tables. Two-table design (chunks + vec0 shadow) with MATCH JOIN
  `query_top_k`. Full create / append / overwrite mode support. HNSW
  once-warning. `SqliteTableSource` with column-projection SELECT
  and JSON metadata. Cross-language parity verified (R3-SC-007).
- **ClickHouse backend (R4).** Append-only sink with
  `ReplacingMergeTree` + `OPTIMIZE FINAL` for dedup. Engine-allowlist
  regex protects against arbitrary engine injection.
  `ClickhouseTableSource` mirrors the other backends with column
  projection.
- **Rust modular trait skeleton (R1).** `Backend`, `BackendConn`,
  `BackendDialect` traits with GAT-lifted connection methods. A single
  `AnyBackend` / `AnySink` / `AnySource` enum dispatches uniformly
  from YAML — adding a new backend is one trait impl + one enum
  variant + one factory branch.
- **RT cross-backend matrix.** 16-cell bakeoff in Rust running every
  chunker × backend combination on the canonical corpus
  (RT-SC-001..006).
- **`target.backend` field.** Selects which backend to ingest into.
  Default remains Postgres for backwards compatibility — existing
  Postgres-only YAML configs continue to work unchanged. New backends
  require their respective extras (`chunkshop[mariadb]`,
  `chunkshop[sqlite]`, `chunkshop[clickhouse]`).

## 0.3.2 — 2026-04-30

Adds the `if_oversize` fallback chain across all seven chunker configs
in both Python and Rust. Closes the silent-oversize gap in the wrapper
chunkers and brings Rust's `semantic` chunker to warning-parity with Python.

- **Universal `if_oversize: ChunkerConfig` field** on every chunker config
  in both languages. Routes any chunk whose `embedded_content` or
  `original_content` exceeds the effective ceiling through a fallback
  chunker. Chains up to 5 levels deep (deeper raises explicit error).
- **`fixed_overlap.max_chars` (optional)** — the chunker is now char-bounded
  too, not just word-bounded.
- **Wrapper effective ceiling** — `neighbor_expand` / `summary_embed` /
  `hierarchical_summary` resolve their ceiling as `cfg.max_chars >
  base.max_chars > None`. Wrappers inherit by default; override per cell.
- **Dedup'd WARN-once-per-cell** when `if_oversize` is unset and an
  oversize chunk would be emitted. Names the chunker, ceiling, and a
  copy-paste suggestion. No log spam.
- **Coarse-row exemption** on `hierarchical_summary` — coarse rows
  (one-per-group) are skipped from the check by design.
- **Rust `semantic` chunker** now logs `tracing::warn!` on hard-split,
  matching Python's `semantic.py:120`. Parity gap closed.
- **NEW `docs/samples/if-oversize/`** — runnable demo showing both the
  WARN behavior (no fallback) and the fallback chain (with fallback).
- **Recursion guard** — `if_oversize` chains beyond depth 5 raise
  `OversizeRecursionError` (Python) / `Error::OversizeRecursion` (Rust).
- **`docs/chunkers.md`** oversize-behavior table refreshed; the foreshadow
  sentence about 0.3.2 replaced by a concrete `Setting if_oversize` section.

## 0.3.1 — 2026-04-30

Documentation maintenance. No behavior changes.

- **`docs/samples/README.md` rewritten** to list all worked-example
  sub-samples (`bakeoff-ntsb/`, `sales-crm/`, `embedder-byo/`,
  `incremental-pg-table/`, `inline-mode/`) with links to each
  sub-README, and to cover every recipe YAML — including the previously
  undocumented `sample-semantic.yaml`, `sample-summary-embed.yaml`,
  `sample-hierarchical.yaml`, and `bakeoff.yaml` / `bakeoff-gold.yaml`.
- **README badges modernized.** Static "v0.2.0" / "v0.1.0 MVP" badges
  replaced with dynamic shields.io PyPI and crates.io version badges
  that auto-update with each release. Status table and monorepo-layout
  diagram refreshed to reflect Python and Rust as published packages.
- **NEW `docs/storage-model.md`** — answers "is the original text stored
  next to the embedding?" (yes, by default and unconditionally).
  Documents the three-payload row shape (`original_content` /
  `embedded_content` / `embedding`), explains which chunkers make the
  two text columns diverge, and gives copy-paste query patterns for
  vector search vs. UI display vs. retrieval debugging.
- **`docs/chunkers.md` — oversize behavior table.** New section
  documenting how each chunker handles inputs that would exceed
  `max_chars`. Calls out the three "no char ceiling" wrappers
  (`neighbor_expand`, `summary_embed`, `hierarchical_summary`) and the
  Python-only warning on `semantic` overflow (Rust parity gap).
  Foreshadows the `if_oversize` fallback chain coming in 0.3.2.
- **`docs/extractors.md` — "where chunk metadata comes from" section.**
  Spells out the four sources (source / framer / chunker / extractor)
  with the chunker-wins precedence rule. Steers users toward
  `pg_table.metadata_columns` for source-side structured metadata
  rather than extractors.
- **`docs/architecture.md` diagrams fixed.** Component map redrawn as
  a left-to-right pipeline (Source → Framer → Chunker → Embedder →
  Extractor → Sink → DB) so the sink sits visually downstream of the
  providers. Sequence diagram ("One ingest, step by step") rewritten
  to remove `<br/>` line breaks inside message text — they aren't
  supported by mermaid's sequenceDiagram parser and were silently
  breaking the render on GitHub. Nested loop (per source row → per
  framed document) preserved and called out in prose.
- **README crates.io link fixed.** Badge and status-table link were
  pointing at `crates.io/crates/chunkshop` (404). Real package name
  is `chunkshop-rs` — both updated.

## 0.3.0 — 2026-04-30

First release published to PyPI (`chunkshop`) and crates.io
(`chunkshop-rs`). The detailed bullets below cover what shipped in
this version. Highlights:

- **Rust port at parity with Python** for the single-cell pipeline
  AND the bakeoff. Same canonical YAML runs from both languages and
  produces equivalent leaderboards (verified by
  `scripts/parity_check_bakeoff.py`). Orchestrator remains Python-only.
- **YAML-driven HuggingFace embedder pointer** — point at any HF ONNX
  model from YAML alone, no rebuild. Mean pooling in both languages.
- **Bakeoff leaderboard surfaces speed-vs-quality** — chunks /
  ingest_s / embed_s columns + per-embedder query-time cost.
- **Inline (library) mode** — `chunkshop.Pipeline` (Python) and
  `chunkshop::Pipeline` (Rust) for embedding chunkshop in your service.
- **`pg_table` source `metadata_columns`** + VIEW pattern for
  bringing JOINed metadata into chunk metadata.
- **`target.delete_orphans`** — atomic per-doc shrink cleanup.
- **Bundled sample corpora** — NTSB (20 docs), sales-crm (974 notes +
  SQL dump). Compressed in-tree; total ~700 KB.
- **User-journey-first docs** — README leads with bring-corpus →
  bakeoff → recommended → ingest → repeat.

### Added

- **`pg_table` source — `metadata_columns` field (Python + Rust).**
  Pull arbitrary additional columns alongside id/content/title; they
  land in each chunk's metadata jsonb. Pair with `target.promote_metadata`
  to surface specific keys as typed columns for fast filtered queries.
  Coerces non-JSON-native types (Decimal → float, datetime → ISO string,
  bytes → base64) so json.dumps round-trips cleanly to the sink.
- **VIEW pattern documented for JOINed metadata.** `setup-sql.sh` now
  creates `sales_notes_enriched` — a Postgres VIEW that joins notes →
  orders → customers and notes → salespeople — and `from-pg-table.yaml`
  pulls `customer_name`, `customer_industry`, `salesperson_name`,
  `deal_status`, etc. via the view. Demonstrates "I need columns from
  joined tables in chunk metadata" without bloating chunkshop's YAML
  with a `joins:` field.
- **Compatibility matrix** in `docs/samples/sales-crm/README.md`
  showing which incremental-ingest patterns (cron + WHERE, watermarked
  cursor, CDC → staging, inline mode) compose with the view pattern,
  plus the "what about updates to JOINed columns?" caveat with three
  resolution options (periodic full re-ingest, trigger-based
  invalidation, multi-table CDC). Verified: `run_incremental_watermark.py`
  drives the view with `WHERE created_at > '$cursor'` cleanly — 974
  notes processed, cursor advanced as expected.

### Changed

- **Sample corpora compressed in-tree.** Bundled samples dropped from
  ~6 MB to ~700 KB:
  - SQL dumps gzipped (`sql/sales-crm-demo-{small,medium}.sql.gz`,
    3.2 MB → 423 KB total). `setup-sql.sh` streams via
    `gunzip -c | sed | psql` — no temp file, no visible decompression step.
  - Sales-note markdown bundled as `notes.tar.gz` (2.6 MB → 130 KB,
    ~20× compression since the templated notes are highly redundant).
    `run-demo.sh` extracts to `notes/` on first run; that directory is
    `.gitignore`d so the archive remains the source of truth.
  - NTSB corpus left uncompressed (152 KB; the friction of a decompression
    step isn't worth saving 130 KB).
- **Sample corpora are now bundled with chunkshop.** The NTSB bakeoff
  corpus (20 .md files, ~150 KB) ships at
  `docs/samples/bakeoff-ntsb/corpus/`; the sales-crm SQL dumps (small
  + medium tiers, ~3.3 MB) ship at `docs/samples/sales-crm/sql/`; the
  sales-crm markdown dump (649 notes, ~2.6 MB) ships at
  `docs/samples/sales-crm/notes/`. All sample YAMLs and scripts now
  reference these local paths instead of `pg-raggraph` sibling-checkout
  paths — clone chunkshop, run any sample, no other repos needed.
  The pg-raggraph originals are untouched (sources, not moves).

### Added

- **Sales CRM demo** (`docs/samples/sales-crm/`) — ingests the same
  realistic CRM dataset (974 sales notes, 300 deals) two ways:
  `pg_table` source against the SQL-loaded `chunkshop_sales_demo`
  schema, and `files` source against the markdown dump at
  `pg-raggraph/benchmarks/sales-crm-demo/docs/`. Both verified
  end-to-end producing 384-dim vectors (1062 chunks from pg_table,
  1675 from files — files have richer markdown structure that the
  hierarchy chunker splits more finely). The setup script renames the
  source SQL schema (`sales_demo_app` → `chunkshop_sales_demo`) before
  loading, so the chunkshop demo never collides with AGE testing that
  uses the original schema. README documents both paths and how to
  adapt to your own OLTP database.

- **Bakeoff leaderboard now surfaces speed-vs-quality.** Three new
  signals in the report:
  - `chunks` column: how many chunks each combo wrote
  - `ingest_s` column: total cell wall time (already tracked, finally
    visible)
  - `embed_s` column: subset of `ingest_s` spent specifically inside
    the embedder. Distinguishes "this combo is slow because of the
    embedder" from "slow because of the chunker / sink"
  - New "Query-time embedding cost" section: per-embedder wall time to
    embed all gold queries. At production scale this scales by your
    expected QPS — useful for choosing between a slower-but-better
    embedder and a faster-but-worse one. Format: `total_s` and
    `per_query_ms` per unique embedder.
- **`embed_seconds` cumulative accessor** on the embedder
  (Python: `FastembedProvider.embed_seconds`, Rust:
  `FastembedEmbedder::embed_seconds()`). Plumbed through `CellResult`
  → `ComboResult` so the bakeoff captures it without instrumenting
  inside `run_cell`.
- **`threads:` in YAML now respected by the user-defined Rust path.**
  Previously the Xenova int8 + BYO Rust path hardcoded
  `with_intra_threads(1)` (load-bearing for bit-exact parity but
  bad UX in production). Now defaults to 1, but a user-supplied
  `threads: 4` is honored — 2-4× faster on multi-core boxes for
  models where bit-exact parity isn't required. The
  `tests/embedding_parity.rs` parity check still runs at threads=1
  to preserve the bit-near-exact envelope.

### Documentation

- **`docs/embedder-catalogue.md`** — user-facing model catalogue.
  Tested-working models (5 verified end-to-end in both Python and Rust:
  Xenova/bge-small-fp32, Xenova/all-MiniLM-int8 mean, Xenova/bge-large-int8,
  Xenova/bge-m3, Xenova/jina-embeddings-v2-base-en), should-work shortlist,
  known-broken cases (intfloat/e5-small-v2 has no ONNX, jinaai/jina-v3
  uses external-data ONNX), dim/max_tokens/precision/pooling per model,
  ONNX file-size table, "what fits in N GB RAM" guidance, int8
  quantization explainer (why we use it, what it costs, what it saves).
- **`docs/samples/embedder-byo/byo-large.yaml`** — runnable companion
  to `byo.yaml` using BGE-large-int8 (1024 dim, ~340 MB). Verified
  end-to-end: 5 chunks @ dim=1024 in both languages. Demonstrates BYO
  scales beyond default 768-dim.
- README + `docs/embedders.md` cross-link to the new catalogue.

### Added

- **YAML-driven HuggingFace embedder pointer ("BYO embedder").** Adding a
  new embedding model is now a YAML edit, not a code edit + rebuild.
  Four new optional fields on `embedder` (when `type: fastembed`):
  `hf_repo`, `onnx_path`, `pooling` (cls/mean, default cls), and
  `additional_files`. When `hf_repo` is set, both implementations
  dynamically register the model with their respective backend at
  config-load time. When unset, the existing registry dispatch runs
  unchanged — every existing YAML keeps working.
  - **Python:** `register_byo_model` calls `TextEmbedding.add_custom_model`
    after pre-fetching files via `huggingface_hub.hf_hub_download`
    (works around fastembed-py's per-repo cache reuse).
  - **Rust:** new `Pooling` enum + `mean_pool` helper in `embedder.rs`.
    The hand-rolled forward pass now dispatches CLS or mean per the
    YAML's `pooling` field. Mean-pooling correctly masks padding tokens
    (verified by `embedder::tests::mean_pool_masks_padding`).
  - **Sample:** `docs/samples/embedder-byo/` — YAML + run script that
    verifies end-to-end from both languages. Verified PASS: 12 chunks
    @ dim=384 from each language using a model name not in either
    registry.
  - **`docs/embedders.md` rewritten** so YAML-only is the recommended
    path. Old "edit registry + rebuild" Case B becomes "Case B-legacy"
    for cases where shipping a permanent registration is preferred.

- **Nomic embedder wired into the Rust dispatch.** `nomic-ai/nomic-embed-text-v1.5`
  and `nomic-ai/nomic-embed-text-v1.5-Q` now resolve to fastembed-rs's
  `NomicEmbedTextV15` / `NomicEmbedTextV15Q` variants. The canonical
  `docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml` (12 combos, including
  nomic) now runs from both languages without a Rust-specific YAML
  variant. Removed `bakeoff-ntsb-rust.yaml`.
- **`docs/embedders.md` updated to cover both languages.** New "The Rust
  dispatch (file map)" section. The Catalogue gains Python/Rust columns
  per model. Cases A/B/C in "Adding a new model" now cover both Python
  and Rust paths, and a "YAML-driven HF pointer (feature pending)"
  section flags the queued brief that turns Case B into a YAML-only
  workflow.
- **Cross-language parity check now runs the canonical matrix.**
  `scripts/parity_check_bakeoff.py` drives the 12-combo
  `bakeoff-ntsb.yaml` from both languages. Top-combo check tightened:
  accepts tied-near-the-tolerance picks (Python and Rust can legitimately
  tie at MRR=0.958 and break the tie differently due to drift on a
  near-tie query). Verified PASS at default tolerance: all 12 combos
  within ±0.021 MRR; ordering consistent.

- **Rust bakeoff port (`chunkshop-rs bakeoff`).** The matrix → leaderboard →
  recommended.yaml loop now runs in both languages. Mirrors
  `python/src/chunkshop/bakeoff/` with byte-identical key derivation
  (combo table names match across languages), pure-math scoring (recall@k +
  MRR), and report.md formatting comparable with Python's.
- **Cross-language bakeoff parity test (`scripts/parity_check_bakeoff.py`).**
  Drives the same `bakeoff-ntsb-rust.yaml` from both implementations,
  diffs aggregate MRR (within ±2.5pp documented ORT-drift envelope) and
  ordering (consistent on distinct-MRR pairs). Verified PASS on the
  shipped sample: 7/8 combos within ±0.011 MRR; one outlier at 0.021
  (well inside envelope); both languages pick the same top combo
  (`hierarchy + bge-base-int8`).
- **`docs/samples/bakeoff-ntsb/bakeoff-ntsb-rust.yaml`** — Rust-compatible
  matrix variant (drops nomic, which isn't in the Rust embedder registry
  yet). Plus `sample-results-rust.md` and `sample-recommended-rust.yaml`
  committed alongside the Python-side artifacts so readers see both
  leaderboards without running the bakeoff themselves.
- **Embedder-registry-breadth gap documented.** `rust/README.md` now
  calls out that the Rust port supports a subset of Python's model_names
  (BGE int8 variants + stock fastembed-rs models) and that adding a model
  is one entry in `src/embedder.rs::resolve_model_name`. nomic + others
  are tracked as follow-ups.

### Documentation

- **User-journey-first docs.** README leads with the canonical 5-step
  loop: bring corpus → write gold queries → run bakeoff → ship the
  recommended cell → repeat for new corpus. New `docs/getting-started.md`
  walks the entire journey using the NTSB sample as the worked example,
  with copy-paste-runnable commands at every step. The bakeoff is now
  positioned as **step 1 of every adoption**, not a sample tucked under
  `docs/samples/`. This is the framing that makes the chunkshop pitch
  ("the experiment that picks the recipe, then the runtime that ships
  the recipe") legible to a first-time reader.
- **Honest framing of the Rust parity story.** Earlier prose ("5/5 sources
  ship in Rust", "all 6 chunkers", "parity-checked") oversold the
  cross-language pitch by stopping at the single-cell layer. The bakeoff
  and orchestrator are Python-only and the docs now say so up-front
  instead of burying it under "deliberately out of scope". Top-level
  README's status table now distinguishes single-cell parity (✅) from
  meta-runner parity (❌). `rust/README.md` carries a prominent note that
  Rust today is for *running* a chosen cell, not *picking* one. The NTSB
  bakeoff sample README explicitly flags Python-only. No code change —
  this is a framing fix so the next reader (you, future me, a contributor)
  doesn't get the same wrong impression. Rust bakeoff port is in flight.

### Changed

- **`scripts/run_incremental_watermark` rewritten in Python.** The original
  bash script's `sed`-fallback YAML editor would corrupt non-trivial source
  blocks when `yq` wasn't on PATH. Replaced with a self-contained Python
  script that uses `pyyaml` (already a chunkshop dep) — round-trips
  multi-line block scalars and quoted strings cleanly. Same flag surface;
  takes a `--chunkshop-bin` for environments where `chunkshop` isn't on PATH
  (e.g. inside `uv run --project python ...`).
- **`docs/samples/incremental-pg-table/` is now a directory** with a runnable
  end-to-end demo (`run_demo.sh` + `setup_demo.sh` + `add_row.sh`). The
  reference YAML moved to `docs/samples/incremental-pg-table/sample.yaml`;
  link in `docs/incremental.md` updated.

### Added

- **NTSB bakeoff sample (`docs/samples/bakeoff-ntsb/`).** Runnable end-to-end
  bakeoff against the 20-doc NTSB aviation-accident corpus shipped with
  `pg-raggraph/benchmarks/kg-rag-eval`. 4 chunkers × 3 embedders = 12 combos,
  12 hand-written gold queries. The committed `sample-results.md` is the
  full leaderboard from a verified run (`hierarchy + nomic-embed-v1.5-Q`
  wins at MRR=0.958, 11/12 top-1 hits); `sample-recommended.yaml` is the
  ready-to-run cell for the top combo. Demonstrates the `chunkshop bakeoff`
  CLI end-to-end on a realistic third-party corpus including all four
  non-semantic chunkers and three embedder sizes.

- **Incremental ingest, deltas, and inline (library) mode.** Five new patterns
  documented in `docs/incremental.md` for hooking change-data into chunkshop:
  cron + WHERE clause, watermarked cursor (with a `scripts/run_incremental_watermark.py`
  wrapper), staging-file inbox, Postgres CDC → staging table, and object-storage
  events. Plus a third-party-tools guide covering schedulers (Airflow / Prefect /
  Dagster / Temporal / Kestra / cron / k8s CronJob), CDC taps (Debezium / pgoutput /
  Estuary / DMS / Supabase Realtime / Materialize), and durable buffers (SQS /
  Kafka / Redis Streams / Rabbit / Cloudflare Queues).
- **`target.delete_orphans` flag (Python + Rust).** When true, after upserting
  chunks for a document the sink runs `DELETE ... WHERE doc_id = $1 AND seq_num
  >= $new_count` inside the same transaction. Closes the per-doc shrink gap
  (last run wrote 12 chunks; this run writes 8 → drop the 4 orphans atomically).
  Default false to preserve historical behavior. Four integration tests in
  `python/tests/chunkshop/test_sink_delete_orphans.py`.
- **Inline (library) mode: `source.type = inline` + `chunkshop.Pipeline`.**
  When the host application IS the source — webhook handler, queue consumer,
  in-process generator — the YAML drops the source dispatch and you call
  `Pipeline.ingest_text(doc_id, text, metadata)` per document. Symmetric in
  Python (`chunkshop.Pipeline.from_yaml`) and Rust (`chunkshop::Pipeline::from_yaml`).
  `Pipeline.delete_document(doc_id)` removes a doc explicitly, scoped to the
  pipeline's `source_tag` (write-once provenance). Runnable end-to-end demos
  for both languages in `docs/samples/inline-mode/` exercise insert / grow /
  shrink-with-orphan-cleanup / delete and produce identical chunk-count tables
  across Python and Rust.
- **`docs/samples/incremental-pg-table/`.** Reference YAML for Pattern A
  (sliding-window) and Pattern B (watermarked) — plus `setup_demo.sh`,
  `add_row.sh`, and `run_demo.sh` that exercise the full watermarked-cursor
  flow end-to-end against a fake source table. Verified to produce 6 chunks
  on first run (4 docs), no-op on a second identical run, and 1 new chunk
  after inserting a fifth row.

- **Rust: `lede` callable summarizer behind `lede` cargo feature.**
  `cargo build --features lede` (or `cargo build --workspace --features lede`)
  pulls `lede 0.3` from crates.io and registers
  `module: chunkshop.summarizers.lede` in the Rust runtime's callable
  summarizer dispatch. Default builds don't pull the dep — base
  `chunkshop-rs` stays small. Kwargs `max_length` (default 500) and `mode`
  (`"default"` / `"legacy"` / `"coverage"`, default `"default"`) match
  Python's lede integration; extra kwargs are warn-and-ignored so future
  upstream params don't trip old binaries. Without the feature, the
  existing "module not registered" error now hints at `--features lede`.
  Two feature-gated tests in `summarizer.rs`. Closes the only remaining
  callable-summarizer gap on the Rust port.

- **`chunkshop-rs` v0.1.0 MVP** (new `rust/` crate). Minimal Rust port that
  proves the cross-language wire-format claim: same YAML, same pgvector
  table, interchangeable chunk ordering. **In:** `files` source,
  `sentence_aware` chunker (byte-for-byte with Python on prose),
  `fastembed` embedder via Anush008's fastembed-rs crate, pgvector sink
  with `overwrite` + `create_if_missing` modes, `chunkshop-rs ingest`
  CLI, library+binary crate, integration test that skips without
  `CHUNKSHOP_TEST_DSN`. **Out (deliberate):** every other chunker
  (`hierarchy`, `fixed_overlap`, `neighbor_expand`, `semantic`,
  `summary_embed`, `hierarchical_summary`), framers, extractors, other
  sources, `append` mode, promoted columns, orchestrator, bakeoff.
  **Known drift:** fastembed-rs's `BGEBaseENV15Q` maps to Qdrant's
  fp32-optimized ONNX, not Xenova's int8-quantized one. On the shipped
  sample corpus `scripts/parity_check.py` reports 100% identical chunks
  and identical top-5 retrieval ordering, with ~0.01 cosine distance
  between matched embeddings (expected fp32-vs-int8 drift). See
  `rust/README.md` for the full feature matrix and the manual parity
  check script.

- **`semantic` chunker.** Splits documents on topic shifts detected by
  sentence-embedding similarity drops. No markdown headings or paragraph
  boundaries required — works on raw transcripts, interviews,
  auto-transcribed audio, headingless blog posts. Ships with a dedicated
  small boundary model (`sentence-transformers/all-MiniLM-L6-v2-int8`,
  ~22 MB, registered in `embedders/_registry.py`), or pass
  `boundary_model: "same"` to reuse the cell's main embedder. Config
  knobs: `breakpoint_percentile` (default 95), `min_sentences_per_chunk`
  (3), `max_chunk_chars` (2000 — matches hierarchy/sentence_aware), and
  `sentence_splitter` (`"naive"` default or `"nltk"`). SC-003 speed gate
  passes at 1.16x on a 5000-word doc (test:
  `tests/chunkshop/test_chunker_semantic_benchmark.py`). See
  `docs/tutorial-semantic.md` + the semantic section in `docs/chunkers.md`.

- **`chunkshop bakeoff` CLI.** Config-driven chunker x embedder matrix
  evaluation against a user's corpus. Outputs a leaderboard + a
  `recommended.yaml` that's a runnable `chunkshop ingest` cell pre-filled
  with the top-MRR combo. Config schema in `python/src/chunkshop/bakeoff/
  config.py`; sample at `docs/samples/bakeoff.yaml`; tutorial at
  `docs/tutorial-bakeoff.md`; recipes at `docs/quickstart-bakeoff.md`.

- **DocFramer** — pluggable Source-to-Chunker framing layer. New `framer:`
  section in YAML between `source` and `chunker`. Four framers:
  - `identity` (default, no-op pass-through — preserves backward compatibility).
  - `heading_boundary` — split a markdown blob on heading level (e.g. every
    `##` becomes its own logical doc).
  - `regex_boundary` — split on arbitrary regex with optional title capture.
  - `jsonpath` — expand nested JSON arrays (`items[*]`) into framed docs with
    configurable title/body paths.
  Each framed doc carries `metadata.framer` + `metadata.frame_seq` for
  provenance. See `docs/tutorial-framers.md` + `docs/quickstart-framers.md`.

- **Metadata extractors** — three new opt-in extractors plus a `composite`
  combinator:
  - `keybert_phrases` — embedding-based keyphrases (higher quality than RAKE).
  - `spacy_entities` — NER entities grouped by label (ORG/PERSON/GPE/DATE…).
  - `lang_detect` — ISO-639-1 language code + confidence.
  - `composite` — chains extractors, merges metadata dicts (last-wins on
    collision), concatenates tag lists. Surfaces child failures; does not swallow.
  Each ships as a pip extra: `[keybert]`, `[spacy]`, `[lang]`, or `[nlp]`
  umbrella. spaCy model auto-downloads on first use. See `docs/extractors.md`,
  `docs/quickstart-extractors.md`, and `docs/tutorial-metadata.md`.

### Changed

- **`s3` source — Python and Rust shipped together (5/5 sources on Rust).**
  Python's `S3Source` was a `NotImplementedError` stub; this brief replaces
  it with a real impl using `boto3` (new optional `[s3]` extra). Rust adds
  a `S3SourceConfig` variant and `S3Source` impl using the `object_store`
  crate's `aws` feature. Both honor an optional `endpoint_url` so users
  can point at minio / R2 / other S3-compatible servers without code
  changes; auth uses the standard AWS credential chain. Document shape
  matches across languages: `id = s3://<bucket>/<key>`, `metadata =
  {bucket, key, size, etag}`. Pagination handled via list_objects_v2.
  Tests are credential-gated (`CHUNKSHOP_S3_TEST_BUCKET` env var); the
  default test run is unchanged.

- **`chunkshop-rs` extractor stage shipped — all 5 pipeline stages now
  have at least partial Rust coverage.** Six extractor variants:
  - `none`, `composite` — trivial ports, byte-identical to Python.
  - `rake_keywords` — hand-rolled RAKE algorithm with a 150-word English
    stopword list; algorithm-only parity (NOT byte-identical to Python's
    rake-nltk, which uses NLTK's stopword list and slightly different
    tokenization).
  - `lang_detect` — via the `whatlang` crate (new dep), with an ISO 639-3
    → 639-1 conversion table for 40+ languages; algorithm-only parity
    (different statistical detector vs Python's `langdetect`).
  - `keybert_phrases`, `spacy_entities` — Python-only stubs that error at
    config-load with a clear message directing users to either Python or
    a custom Rust binary.

  Runner threads tags + metadata through the documented chunker-wins
  merge (`{**doc.metadata, **r.metadata, **c.metadata}`). Sink's
  `write_document` gains a `tags_per_chunk: &[Vec<String>]` parameter
  populating the `tags text[]` column per chunk; mismatched-length is an
  error. The `cross_language_append_with_promote_column` integration
  test still passes (no regression). 10 new lib tests + 7 new integration
  tests; lib total now 42 (was 32). New runtime dep: `whatlang = 0.16`.

- **`chunkshop-rs` chunker matrix is complete (6/6).** Ports
  `summary_embed` and `hierarchical_summary` from Python, closing the
  last two chunker gaps. Both wrap any base chunker. `summary_embed`
  replaces each chunk's `embedded_content` with a summary;
  `hierarchical_summary` emits both fine (granularity=fine) and coarse
  (granularity=coarse, summary of joined group) chunks linked by
  `group_id` matching Python's `{doc.id}::g{idx}` format. Three grouping
  strategies fully implemented (`fixed_n`, `word_budget`,
  `section_aware`); the Python `section_aware`-requires-`hierarchy`-base
  validator runs at config-load on the Rust side too. Shared
  `SummarizerConfig` (passthrough / external / callable) dispatched by a
  new `summarizer.rs` module. **Callable mode** in Rust currently
  recognizes only `chunkshop.summarizers.passthrough` — lede integration
  via crates.io is a follow-up feature-flagged brief, but unknown modules
  produce a clear error directing users to either Python or a custom
  Rust binary that registers their summarizer. Cross-language **byte-
  identical** parity verified by 4 new integration tests
  (`summary_embed_parity` × passthrough/external + `hierarchical_summary_parity`
  × fixed_n/section_aware). Lib test count grows to 32 (was 21 — added 11
  for summarizer modes, grouping strategies, and config validators).

- **`http` source — first feature where Python and Rust shipped together.**
  Python's `HttpSource` was a `NotImplementedError` stub; this brief
  replaces it with a real implementation (stdlib only — `urllib.request` +
  `xml.etree.ElementTree` + `re`) and adds the matching Rust port (using
  `reqwest`, promoted to a direct dep). Both fetch every URL in `urls`
  and walk an optional `sitemap` (one-level `<urlset><loc>`; sitemap-of-
  sitemaps explicitly out of scope), de-dup by first occurrence, build a
  `Document` per URL with `id=<url>`, `content=<body>`, `title=<HTML
  <title>>` and `metadata={url, status_code, content_type}`. Fail-fast on
  non-2xx. Tests on each side spin up an in-process HTTP server and cover
  URLs-only, sitemap walk, dedup, and the error path. Python pytest
  count grows from 181 to 185; Rust adds 4 new tests. Four of five sources
  now ship in Rust (`files`, `json_corpus`, `pg_table`, `http`); only
  `s3` remains.

- **`chunkshop-rs` now ships the `pg_table` source.** Reads documents
  from a Postgres table by id_column / content_column / optional title_column
  with an optional `where` clause. Mirrors Python: identifiers are
  regex-validated at config-load (allowlist `^[a-z_][a-z0-9_]*$`); the
  WHERE clause is trusted operator input concatenated as-is. Implementation
  uses sqlx (already a dep). The runner's source dispatch is now async to
  accommodate the network-bound query path; files / json_corpus variants
  keep their sync inner impls and are awaited through the async wrapper at
  no overhead. Three of five sources ship in Rust now (`files`,
  `json_corpus`, `pg_table`); remaining `http` + `s3`. Verified by
  `rust/chunkshop/tests/pg_table_source.rs` against a real Postgres.

- **`chunkshop-rs` now ships the `semantic` chunker.** Splits documents at
  topic shifts detected by sentence-embedding similarity drops. Same algorithm
  as Python: naive sentence split → embed each via a small boundary model
  (default `sentence-transformers/all-MiniLM-L6-v2-int8` → fastembed-rs's
  stock quantized AllMiniLML6V2Q in Rust) → cosine distances between
  adjacent sentences → percentile threshold (numpy linear-interpolation
  default) → breakpoint detection → span build → small-span merging
  (forward then backward) → max_chunk_chars hard-split on sentence boundaries.
  Algorithm helpers tested per-component (4 unit tests for percentile,
  span-merge cases) plus 5 sentence-splitter tests; an end-to-end smoke
  test confirms the full chunker runs against the sample corpus and emits
  well-formed chunks. **Cross-language byte-identical chunks are NOT
  promised** for this chunker: the percentile-cutoff over float embeddings
  is sensitive to MB-1's documented ~1e-3 ORT-binary drift, which can shift
  which sentence-pairs cross the threshold. Five of six Python chunkers
  ship in Rust now; remaining: `summary_embed` and `hierarchical_summary`
  (both need a callable summarizer, where the `lede` crate from crates.io
  is a natural fit).

- **`chunkshop-rs` now ships the framer pipeline stage** with all four
  framers Python ships: `identity` (default 1-to-1 pass-through), `heading_boundary`
  (split markdown on a configurable heading regex with preamble extraction
  and title-from-heading), `regex_boundary` (split on arbitrary regex with
  optional title-pattern capture), and `jsonpath` (parse content as JSON,
  walk dotted path with `*` for list iteration). The runner now wires
  source → framer → chunker → embedder → sink. **Byte-identical to Python**
  for both heading_boundary and jsonpath, verified by
  `rust/chunkshop/tests/heading_boundary_parity.rs` and
  `rust/chunkshop/tests/jsonpath_parity.rs` against committed Python
  references. Default identity-framer means existing YAMLs without a
  `framer:` block see no behavior change (verified by the
  `cross_language_append_with_promote_column` test re-running unchanged).

- **`chunkshop-rs` now ships the `fixed_overlap` and `neighbor_expand`
  chunkers.** `fixed_overlap` is a word-level sliding window with
  `start_word` / `n_words` metadata (Python's canonical baseline chunker).
  `neighbor_expand` wraps any base chunker and joins each chunk's ±N
  neighbors into `embedded_content`. Both are **byte-identical to Python**
  on the same input — verified by `rust/chunkshop/tests/fixed_overlap_parity.rs`
  and `rust/chunkshop/tests/neighbor_expand_parity.rs` against committed
  Python references. The runner gains a `ChunkerImpl` trait + a recursive
  `build_chunker` so neighbor_expand can wrap any other chunker (including
  itself, in principle). Four of six Python chunkers ship in Rust now
  (`sentence_aware`, `hierarchy`, `fixed_overlap`, `neighbor_expand`);
  remaining: `semantic` and the two summary_* wrappers.

- **`chunkshop-rs` now ships the `json_corpus` source** — same shape as
  Python: reads a JSON file, takes the array under `documents_key` (default
  `"documents"`), pulls `id` / `content` / `title` from configured fields
  (defaults `id`, `content`, `title`), and stuffs the remaining row keys
  into the document's `metadata` as raw JSON values (preserving types so
  downstream `promote_metadata` casts work). Verified by
  `rust/chunkshop/tests/json_corpus_source.rs`. Any Python YAML with
  `source.type: json_corpus` runs unchanged on Rust.

- **`chunkshop-rs` sink reaches full-mode parity with Python.** Adds
  `mode: append` (table-existence + dim-match + ALTER preflight),
  `force_overwrite` flag, the source-tag-conflict safety check on
  `mode: overwrite` (refuses to drop a table holding rows from a different
  cell unless `force_overwrite: true`), the BLAKE2b-keyed
  `pg_advisory_xact_lock` that serializes concurrent-cell schema setup,
  and `promote_metadata` jsonb-to-typed-column writes (allowlisted types,
  identifier-safe paths). `source` stays write-once on `ON CONFLICT` —
  provenance is preserved across cells. Cross-language verified by
  `rust/chunkshop/tests/sink_modes_parity.rs`: a Python `mode: overwrite`
  cell + a Rust `mode: append` cell both write into one table, both rows
  query by `WHERE source = ...`, and the promoted column holds the right
  typed values for both. New dep: `blake2`.

- **`chunkshop-rs` now ships the `hierarchy` chunker** — Python's shipped
  default and the bakeoff winner. Same logic: heading prefix prepended to
  `embedded_content` (`{heading}\n\n{body}`), per-`section_part` metadata,
  `min_section_chars` filter, recursion through `split_to_max_chars` for
  oversized sections. Cross-language **byte-identical chunk text** verified
  by `rust/chunkshop/tests/hierarchy_parity.rs` against a committed Python
  reference (`scripts/produce_rust_hierarchy_reference.py`). With this plus
  the int8 BGE embedder parity (below), the canonical
  `hierarchy + bge-base-int8` config now runs end-to-end on Rust and is
  retrieval-equivalent to Python on the sample corpus
  (`scripts/parity_check.py`: top-1 match True, 100% byte-identical chunk
  text, max cosine drift 7e-3).

- **`chunkshop-rs` embedder now matches Python on the same Xenova int8 ONNX**
  for `Xenova/bge-base-en-v1.5-int8` and `Xenova/bge-small-en-v1.5-int8`. The
  Rust embedder now downloads `onnx/model_quantized.onnx` (and four tokenizer
  files) via `hf-hub`, runs ORT directly with `intra_threads=1`, CLS-pools,
  and L2-normalizes — the same pipeline Python's fastembed runs. On the
  sample corpus `scripts/parity_check.py` now reports **identical top-k
  retrieval order, 100% byte-identical chunk text, and mean ~1-2e-3 / max
  ~5-15e-3 cosine drift per chunk** (was ~1e-2 mean — ~5x improvement). New
  test `rust/chunkshop/tests/embedding_parity.rs` enforces the envelope at
  CI-time. Strict bitwise parity is **not** claimed: Python's `onnxruntime`
  wheel and Rust's `ort` crate are independent ORT C++ binary builds and
  diverge by ULPs on quantized matmul paths. New deps: `hf-hub`, `ort`,
  `tokenizers`, `ndarray` (all already transitive via fastembed; promoted to
  direct). All other model names continue to use fastembed-rs's stock
  variants — those don't claim parity. See `rust/README.md` "Embedding parity
  vs Python" for the full envelope and verification method.

- **Default embedder flipped from `Xenova/bge-small-en-v1.5-int8` (384 dim) to
  `Xenova/bge-base-en-v1.5-int8` (768 dim).** Same `int8` quantization + same
  registry path, but roughly +3–5 MTEB points on standard retrieval benchmarks
  at the cost of ~50 MB extra download and 2× pgvector storage per row. Every
  shipped example YAML, tutorial, and quickstart now uses the new default.
  **Action:** (a) Tables already ingested with bge-small-int8 are not
  vector-compatible with bge-base-int8 — re-ingest into a fresh table, or pin
  the old model via `embedder.model_name` in YAML. (b) Users with tight RAM/disk
  budgets should pin `Xenova/bge-small-en-v1.5-int8` explicitly; it remains
  registered and supported. (c) `factorial-int8/*-bge-small.yaml` configs
  deliberately keep the old model — those are comparison cells, not defaults.

### Fixed

- **Chunker `max_chars` hotfix.** `HierarchyChunker` previously emitted unbounded
  chunks between markdown headings; `SentenceAwareChunker` had a 3000-char cap
  (~750 tokens, over `bge-small-en-v1.5`'s 512-token limit). Both now enforce
  `max_chars: 2000` by default, splitting on paragraph→sentence→char boundaries.
  Split children of a single hierarchy section share `metadata.heading` and
  carry `metadata.section_part` (0-indexed). **Action:** Corpora previously
  ingested with oversized sections should be re-ingested; embeddings on
  oversized chunks only represented the first ~512 tokens. Users on larger-
  context embedders (`text-embedding-3-small/large`) should raise `max_chars`
  in YAML — see `docs/chunkers.md`.
