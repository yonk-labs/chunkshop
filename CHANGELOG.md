# Changelog

## Unreleased

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
