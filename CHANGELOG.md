# Changelog

## Unreleased

### Added

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
