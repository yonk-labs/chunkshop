# Tutorial: the `semantic` chunker

Your corpus doesn't always come with headings. Interview transcripts, meeting
notes, auto-captioned video, long blog posts without section markers — these
defeat `hierarchy` (no headings to split on) and `sentence_aware` (one giant
paragraph-packed chunk per doc). Fixed-width windows work but split mid-idea.

The `semantic` chunker splits on **topic shifts** — cuts the doc wherever
sentence-embedding similarity drops sharply between consecutive sentences.
No syntactic cues required. This walkthrough shows how to use it, what the
output looks like, and when to pick it over the three structural chunkers.

## What you'll have at the end

- A chunkshop_samples.interview_semantic Postgres table holding the interview
  transcript at `docs/samples/semantic_demo_interview.md`, split into ~3
  topical chunks.
- A side-by-side comparison showing why `semantic` beats `hierarchy` on this
  kind of input.
- A mental model for the knobs (`breakpoint_percentile`,
  `min_sentences_per_chunk`, `max_chunk_chars`) so you can tune for your
  corpus.

## Prerequisites

Same as the [main tutorial](tutorial.md) — `uv sync`, Postgres with
pgvector, `CHUNKSHOP_DSN` exported. If you've done the main tutorial
you're ready.

## Step 1 — Look at the input

```bash
wc -w docs/samples/semantic_demo_interview.md
# ~620 words
head -1 docs/samples/semantic_demo_interview.md | cut -c1-80
# "Alright, let's just start from the beginning. I joined Northwind back in 2..."
```

One continuous blob. No markdown headings. If you look closely the transcript
has three clear topic regions: (1) career history, (2) compensation and
logistics, (3) hobbies. A human notices the pivot words ("Okay, let's talk
about compensation", "Outside of work?"). A heading-based chunker doesn't.

## Step 2 — See what `hierarchy` does

Run the default sample config against this file for contrast:

```bash
cat > /tmp/interview-hierarchy.yaml <<'EOF'
cell_name: interview_hierarchy
source: {type: files, glob: docs/samples/semantic_demo_interview.md, id_from: stem}
framer: {type: identity}
chunker: {type: hierarchy}
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 4
extractor: {type: none}
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: interview_hierarchy
  mode: overwrite
  hnsw: false
runtime: {omp_num_threads: 4, heartbeat_every: 1}
EOF

chunkshop ingest --config /tmp/interview-hierarchy.yaml
```

Query the row count:

```sql
SELECT doc_id, seq_num, length(original_content) AS chars
FROM chunkshop_samples.interview_hierarchy;
```

Output:

```
           doc_id            | seq_num | chars
-----------------------------+---------+-------
 semantic_demo_interview     |       0 |  3200+
```

One row. The whole document. Because there's no heading to split on,
`hierarchy` falls back to "emit one chunk with the doc title prefixed."
The embedder then truncates that 3200-char string to its first ~2 KB,
and **half the document is never embedded**. Hobbies? Not in the vector.

## Step 3 — Run the `semantic` chunker

The shipped `sample-semantic.yaml` does exactly this:

```bash
chunkshop ingest --config docs/samples/sample-semantic.yaml
```

Expected output (exact chunk count varies with `breakpoint_percentile` and
tokenizer version — you'll see 2-3 chunks on this transcript):

```
cell samples_semantic DONE docs=1 chunks=2 wall=0.6s
```

On a fresh cache, first run downloads the MiniLM boundary model (~22 MB)
in addition to the bge-base-int8 main embedder. Subsequent runs reuse
the cache.

## Step 4 — Inspect the chunks

```sql
SELECT seq_num,
       substring(original_content from 1 for 60) AS preview,
       length(original_content) AS chars
FROM chunkshop_samples.interview_semantic
ORDER BY seq_num;
```

Output (your exact boundaries depend on tokenizer version + hardware; here's
a representative 2-chunk run where the percentile threshold caught the
career→compensation pivot but merged compensation+hobbies):

```
 seq_num |                          preview                            | chars
---------+-------------------------------------------------------------+-------
       0 | Alright, let's just start from the beginning. I joined No.. | ~1750
       1 | Okay, let's talk about compensation. My current total com.. | ~1100
```

Lower `breakpoint_percentile` to 90 and you'll see the three-chunk split
(career / compensation / hobbies). Raise to 98 and everything merges into
one chunk. See Step 6 for tuning.

Each chunk has `metadata.strategy = "semantic"`:

```sql
SELECT seq_num, metadata->>'strategy'
FROM chunkshop_samples.interview_semantic
ORDER BY seq_num;
```

## Step 5 — Run a semantic query

Same pattern as the main tutorial. Python script:

```python
import os, psycopg
from fastembed import TextEmbedding

MODEL = "Xenova/bge-base-en-v1.5-int8"
DSN = os.environ["CHUNKSHOP_DSN"]

embedder = TextEmbedding(model_name=MODEL, threads=4)
for q in [
    "what did they build at their last job",
    "how much do they want to get paid",
    "what do they do for fun",
]:
    qvec = list(embedder.embed([q]))[0]
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT seq_num, substring(original_content from 1 for 60) "
            "FROM chunkshop_samples.interview_semantic "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            (qvec.tolist(),),
        )
        seq, preview = cur.fetchone()
        print(f"Q: {q!r}\n  -> seq_num={seq} | {preview}...\n")
```

On the default 2-chunk split, the career question lands on chunk 0 and
the compensation + fun questions both land on chunk 1 (since hobbies got
merged in with compensation at `percentile=95`). Drop
`breakpoint_percentile` to 90 in `sample-semantic.yaml`, re-ingest, and
the three queries resolve to three distinct chunks. Either way the
retrieval works *because each chunk is a coherent semantic unit, not an
arbitrary slice* — which is the whole point compared to the truncated
hierarchy chunk from Step 2.

## Step 6 — Tuning knobs

### `breakpoint_percentile`

- **Higher (e.g. 97)** — cuts on fewer, more dramatic drops. Larger chunks.
  Risk: merges mildly-related topics.
- **Lower (e.g. 85)** — cuts more aggressively. Smaller chunks. Risk:
  fragments a single topic across multiple chunks.
- Default `95` is a reasonable middle: top-5% of similarity drops become
  breaks.

### `min_sentences_per_chunk`

- Prevents tiny chunks that appear when a run of 1-2 sentences gets
  semantically flagged as "different" from both neighbors.
- Below-threshold spans merge forward (or backward if last).
- Default `3` works for prose; raise to 5-8 for dialog transcripts where
  short turns dominate.

### `max_chunk_chars`

- Hard upper bound — identical semantics to `hierarchy`/`sentence_aware`.
- Oversized topical spans hard-split on sentence boundary, logged with a
  warning.
- Default `2000` fits bge's 512-token limit. If you're using a larger
  context embedder, raise it — see the tuning table in
  [`chunkers.md`](chunkers.md#tuning-max_chars-for-your-embedder).

### `boundary_model: "same"`

- Reuses the cell's main embedder for boundary detection. No second model
  load.
- Trade: the main embedder is larger (bge-base int8 is ~110 MB vs MiniLM
  int8 at ~22 MB), so sentence-level boundary embedding runs slower.
- Use this when RAM is tight. Skip it when you care about ingest speed.

### `sentence_splitter`

- `"naive"` (default) — fast regex on `.?!` + whitespace. Gets 95% of
  sentences right on English prose.
- `"nltk"` — NLTK's Punkt tokenizer. More accurate on edge cases (abbreviations,
  decimals, quote-heavy dialog). Requires the `punkt_tab` corpus, which
  auto-downloads on first use.

## When NOT to use `semantic`

- **Structured markdown.** `hierarchy` always wins when you have real
  `#`/`##` headings — zero inference cost, perfect boundary recall.
- **Code or logs.** Semantic drift tracks natural-language topicality, not
  function boundaries. Use `sentence_aware` with `doc_type: "code"`.
- **Very short docs (< 10 sentences).** Similarity statistics are too
  noisy to pick meaningful breaks. Use `sentence_aware`.
- **Tight latency budget on ingest.** `semantic` costs ~1.2× a main-cell
  embed pass on a 5000-word doc. That's cheap per-doc but adds up at
  scale.

## Speed

Measured on a mid-range CPU against `docs/samples/*-*.md` concatenated to
~5000 words:

- Main embed (bge-base int8, 15 chunks of ~2000 chars each): 2.25 s
- Semantic chunking (MiniLM int8, ~328 sentence embeddings): 2.61 s
- Ratio: **1.16×**

The SC-003 gate asserts ≤ 2× the main-embed cost. We clear it by a wide
margin because MiniLM int8 is faster per-token than bge-base int8, even
running 20× more forward passes. The
`tests/chunkshop/test_chunker_semantic_benchmark.py` test reproduces this
on any machine; run it with `uv run pytest -v -s -m slow` to see the
ratio on your hardware.

## Next steps

- Pair `semantic` with `neighbor_expand(window=1)` to glue topical context
  across chunks — useful when queries span topic boundaries.
- Swap to `boundary_model: same` if you're running many cells concurrently
  and RAM is tight.
- Run the [bakeoff](tutorial-bakeoff.md) with `semantic` added to the
  `matrix.chunkers` list to compare it against `hierarchy` + `sentence_aware`
  on your own corpus. That's the only way to know whether it's a net win
  for your specific data.
