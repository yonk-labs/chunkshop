# Changelog

## Unreleased

### Added

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
