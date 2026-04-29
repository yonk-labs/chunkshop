# Changelog

## Unreleased

### Documentation

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
