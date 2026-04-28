# 10 — `summary_embed` with `passthrough` summarizer

Exercises the `summary_embed` wrapper chunker in its baseline mode:
`embedded_content == original_content`. No actual summarization happens —
this is the A/B baseline against which real summarizer modes (lede,
sumy, LLM-wired callables) get measured.

- **Source:** one markdown file with two `#` sections.
- **Base chunker:** `hierarchy` (splits on the headings).
- **Summarizer:** `passthrough` — proves the wrapper + metadata plumbing
  without pulling lede/sumy/LLM dependencies in CI.

Asserts at least 2 rows (one per section) with `metadata.strategy` and
`metadata.summarizer` populated. The summarizer key is how downstream
can tell a baseline cell apart from a real-summarizer cell when multiple
run into the same table.
