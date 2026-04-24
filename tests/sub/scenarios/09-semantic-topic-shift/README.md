# 09 — semantic chunker: topic-shift boundary detection

Exercises the `semantic` chunker on a short, headingless performance-review
monologue with three obvious topic pivots (last quarter → career development →
goals for next quarter). Structural chunkers would emit this as one giant
chunk because there are no markdown headings.

- **Source:** `files` glob → one markdown file.
- **Chunker:** `semantic` with `breakpoint_percentile: 90` (a bit more
  aggressive than the 95 default — the three pivots are close together and
  95 sometimes merges two of them on this fixture).
- **Embedder:** `Xenova/bge-base-en-v1.5-int8`.

Asserts at least 2 chunks (expected 2-3 depending on tokenizer version).
