# Changelog

## Unreleased

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
