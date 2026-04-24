# Quickstart: `semantic` chunker

Recipe card for splitting unstructured text on topic shifts. Full walkthrough
in [`tutorial-semantic.md`](tutorial-semantic.md); detailed reference in the
[chunkers.md semantic section](chunkers.md#5-semantic--embedding-drift-boundary-detection).

## When to pick it

| Your source looks like… | Use |
|---|---|
| Markdown with real `#`/`##` headings | `hierarchy` (the default, not this) |
| Meeting transcripts, interviews, call notes | `semantic` |
| Auto-captioned video / podcast output | `semantic` |
| Long blog posts without section markers | `semantic` |
| Headingless prose that reads as one continuous blob | `semantic` |
| Code or logs | `sentence_aware` with `doc_type: code`, not this |
| Very short docs (< 10 sentences) | `sentence_aware` — similarity stats are too noisy |

## Minimal recipe (dedicated boundary model)

```yaml
chunker:
  type: semantic
  # Defaults shown — omit any field to accept the default.
  boundary_model: sentence-transformers/all-MiniLM-L6-v2-int8
  breakpoint_percentile: 95
  min_sentences_per_chunk: 3
  max_chunk_chars: 2000
  sentence_splitter: naive
```

First run downloads the MiniLM boundary model (~22 MB) in addition to the
main embedder.

## Memory-tight recipe (reuse main embedder)

```yaml
chunker:
  type: semantic
  boundary_model: "same"   # reuse the cell's main embedder instance
  breakpoint_percentile: 95
```

Trade: main embedder is usually larger (bge-base-int8 ~110 MB vs MiniLM int8
~22 MB), so sentence-level boundary embedding runs slower. Skip the double
model load when RAM matters more than speed.

## More-chunks recipe (finer topic splits)

```yaml
chunker:
  type: semantic
  breakpoint_percentile: 85   # cut on top-15% of drops (vs default top-5%)
  min_sentences_per_chunk: 3
```

Use when your corpus has many short topic shifts (customer support
transcripts, rapid-fire interview turns).

## Fewer-chunks recipe (coarser topics)

```yaml
chunker:
  type: semantic
  breakpoint_percentile: 98
  min_sentences_per_chunk: 8
```

Use when your corpus is long-form (lectures, essays) and you want chunks
to span paragraphs.

## With NLTK sentence splitter

```yaml
chunker:
  type: semantic
  sentence_splitter: nltk
```

The `nltk` splitter handles abbreviations ("Dr.", "i.e.") and decimals
("$1.50") better than the naive regex. Triggers a one-time Punkt download
on first use.

## Wrapped with `neighbor_expand` for more recall

```yaml
chunker:
  type: neighbor_expand
  window: 1
  base:
    type: semantic
    breakpoint_percentile: 95
```

Semantic chunks are often context-dependent (the answer to "how much" needs
"to pay who"). `neighbor_expand` glues ±1 neighbor into each row's embedded
content.

## What this replaces

Before `semantic`, ingesting a headingless transcript meant either:

```python
# BEFORE — custom splitter per transcript shape
def split_transcript(text, target_words=500):
    chunks = []
    buf = []
    cur_words = 0
    for para in text.split("\n\n"):
        words = para.split()
        if cur_words + len(words) > target_words and buf:
            chunks.append(" ".join(buf))
            buf = [para]
            cur_words = len(words)
        else:
            buf.append(para)
            cur_words += len(words)
    if buf: chunks.append(" ".join(buf))
    return chunks
```

— which splits on word counts, not topic, so retrieval lands on the wrong
half of a pivot. Or:

```yaml
# AFTER — 2 lines
chunker:
  type: semantic
```

— which splits where the topic actually shifts, regardless of surface shape.

## Speed note

On a mid-range CPU, semantic chunking a 5000-word doc costs roughly 1.2× a
main-cell bge-base embed pass. The
[benchmark test](../python/tests/chunkshop/test_chunker_semantic_benchmark.py)
runs under `uv run pytest -v -s -m slow`. If the ratio exceeds 2× on your
hardware, lower `breakpoint_percentile` (fewer, bigger chunks = fewer
boundary embeddings to compute).

## See also

- [`tutorial-semantic.md`](tutorial-semantic.md) — narrative walkthrough
  with queries and tuning.
- [`chunkers.md`](chunkers.md#5-semantic--embedding-drift-boundary-detection) — full
  reference: knobs, sample output, when not to pick.
- [`samples/sample-semantic.yaml`](samples/sample-semantic.yaml) + the
  `semantic_demo_interview.md` fixture — a runnable cell.
