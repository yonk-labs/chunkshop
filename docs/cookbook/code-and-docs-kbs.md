# Code + Docs: the two-KB pattern

When you ingest a code repository for retrieval, you have **two
semantically distinct asset types**:

1. **Code** — `.py`, `.java`, `.go`, `.ts`, `.js`, `.rs`, etc.
   Symbol-bounded. The natural unit is a function or class with a
   fully-qualified name, a line range, and a language tag. Splitting
   mid-statement (or mid-class) produces useless retrieval results.
2. **Docs** — `.md`, `.rst`, `.txt`, `README`, `CHANGELOG`, anything
   under `docs/`. Natural-language prose. The natural unit is a
   paragraph or section: sentence-aware splitting gives self-contained
   passages.

Chunking them the same way is a mistake either direction: prose chunkers
slice classes in half, and symbol chunkers treat a 500-paragraph
markdown file as one giant "module". The pattern: **two cells, two
tables, one shared embedder**.

```
<schema>.kb_code       <schema>.kb_docs
  glob: *.py/*.go/...    glob: *.md/*.rst/*.txt
  chunker:               chunker:
    symbol_aware           sentence_aware
  extractor:             extractor:
    code_summary +         lang_detect +
    code_relationships     rake_keywords
  embedder:              embedder:
    fastembed bge-small    fastembed bge-small   <- SAME embedder
```

Same embedder = same vector space = you can search them separately or
jointly with one ``hybrid_search`` call per table, fused client-side.

## When to pick this pattern

- You're ingesting a code repo where the docs are first-class
  (`README`, `CHANGELOG`, design docs, ADRs, tutorials) and you want
  retrieval to find both "where is X defined" *and* "how do I use X".
- You want different filters per asset type — e.g. "search only
  Python code" vs "search only English-language docs".
- You want one embedder load, one model download, one vector dim.

## When NOT to pick this pattern

- The repo has no meaningful docs (pure library, no `README` beyond
  a one-liner). Just run the code KB.
- You want a single global ranking with strict cross-asset
  comparability. RRF over two tables fuses ranks well, but if you need
  exact "absolute" comparability tune the per-leg weights with the
  ``weighted`` fusion.
- Your docs are in a different language family. The shared embedder
  has to cover both code and docs; English-trained models like
  ``bge-small-en`` are fine for both English prose and code, but a
  Chinese-only model would miss either.

## The two YAML cells

### `kb_code` cell

```yaml
cell_name: kb_code
source:
  type: files
  glob: "<repo>/**/*.py"       # one cell per extension you want
  id_from: path
chunker:
  type: symbol_aware
  granularity: function         # one chunk per top-level fn/class
  include_imports: true         # prepend `import` block to embedding
extractor:
  type: composite
  extractors:
    - type: code_summary        # stamps metadata.summary (lede backend)
      backend: lede
      max_length: 240
    - type: code_relationships  # stamps metadata.callees per chunk
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  batch_size: 64
  threads: 4
target:
  type: postgres
  database: chunkshop_code_and_docs_demo
  table: kb_code
  mode: create_if_missing       # so multiple language cells co-exist
  source_tag: code_py           # one tag per language
  hnsw: false                   # small corpus; seq-scan beats HNSW
  promote_metadata:             # surface as queryable columns
    - { path: symbol_name, type: text }
    - { path: fqn,          type: text }
    - { path: symbol_type,  type: text }
    - { path: language,     type: text }
    - { path: summary,      type: text }
    - { path: start_line,   type: int  }
    - { path: end_line,     type: int  }
```

To cover more languages, run the same cell shape with different
`source.glob` + `source_tag`. The `symbol_aware` chunker handles
Python, Java, Go, TypeScript, and JavaScript natively. Other
extensions (e.g. `.rs`) fall back to `sentence_aware` chunking —
still useful, just no symbol awareness.

### `kb_docs` cell

```yaml
cell_name: kb_docs
source:
  type: files
  glob: "<repo>/**/*.md"        # one cell per extension you want
  id_from: path
chunker:
  type: sentence_aware
  min_chars: 200
  max_chars: 1200
extractor:
  type: composite
  extractors:
    - type: lang_detect         # stamps metadata.language (ISO-639-1)
    - type: rake_keywords       # stamps tags with key phrases
      top_k: 8
      min_chars: 4
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8  # SAME as kb_code
  dim: 384
  batch_size: 64
  threads: 4
target:
  type: postgres
  database: chunkshop_code_and_docs_demo
  table: kb_docs
  mode: create_if_missing
  source_tag: docs_md
  hnsw: false
  promote_metadata:
    - { path: language,     type: text }
    - { path: source_path,  type: text }
```

## Querying each KB

```python
from chunkshop.embedders import load_embedder
from chunkshop.config import FastembedEmbedder
from chunkshop.search import ensure_fts, hybrid_search

embedder = load_embedder(FastembedEmbedder(
    type="fastembed",
    model_name="Xenova/bge-small-en-v1.5-int8",
    dim=384,
))

# One-time per table: build the tsvector + GIN index for the FTS leg.
ensure_fts(DSN, schema="chunkshop_code_and_docs_demo", table="kb_code")
ensure_fts(DSN, schema="chunkshop_code_and_docs_demo", table="kb_docs")

q = "IncrementalSource cursor advancement"
qv = embedder.embed([q])[0]

# Search code only.
code_hits = hybrid_search(
    DSN, schema="chunkshop_code_and_docs_demo", table="kb_code",
    query=q, query_vec=qv, k=5,
    legs=("semantic", "fts"), fusion="rrf",
)

# Search docs only.
docs_hits = hybrid_search(
    DSN, schema="chunkshop_code_and_docs_demo", table="kb_docs",
    query=q, query_vec=qv, k=5,
    legs=("semantic", "fts"), fusion="rrf",
)
```

## Querying both at once

```python
def search_both(query, query_vec, k=10):
    code_hits = hybrid_search(
        DSN, schema=SCHEMA, table="kb_code",
        query=query, query_vec=query_vec, k=k,
    )
    docs_hits = hybrid_search(
        DSN, schema=SCHEMA, table="kb_docs",
        query=query, query_vec=query_vec, k=k,
    )
    # Dedup by content (a chunk could theoretically be in both KBs
    # if you're ingesting code-as-prose), keep the higher-scoring copy.
    pool = {}
    for origin, hits in (("kb_code", code_hits), ("kb_docs", docs_hits)):
        for h in hits:
            key = h.text.strip()[:200]
            prev = pool.get(key)
            if prev is None or h.score > prev[1].score:
                pool[key] = (origin, h)
    return sorted(pool.values(), key=lambda t: t[1].score, reverse=True)[:k]
```

RRF scores from independent ``hybrid_search`` calls are comparable as
**relative rankings within each call** — they're not absolute. For
top-k joint retrieval that's fine. If you need stricter calibration,
use ``fusion="weighted"`` and pick per-table weights.

## Tradeoffs

| Decision | Why |
| --- | --- |
| Same embedder (same dim) for both KBs | One model download, one vector space, joint search is just two queries + merge. Different embedders means different vector spaces and joint search becomes a re-rank problem. |
| Different chunker per KB | A function is the natural code unit; a paragraph is the natural prose unit. Forcing one chunker on both halves the win in one direction. |
| Different extractor per KB | Code wants summaries + relationships. Prose wants language + keywords. RAKE on a Python function is noise; `code_summary` on prose is wasted compute. |
| Same Postgres schema, two tables | One DSN, one schema to drop on re-ingest, one search API. |
| `mode: create_if_missing` per cell | The demo runs **one sub-cell per file extension** (one for `*.py`, one for `*.go`, ...), all writing to `kb_code`. `create_if_missing` lets the first sub-cell create the table and subsequent ones append. Pair with a unique `source_tag` per sub-cell for provenance. |
| `hnsw: false` | The chunkshop repo produces ~6 000 chunks total — sequence scan over a vector column beats HNSW build cost. Flip to `true` for >100 000 chunks. |

## Decision rule: which KB to search first

| Query intent | Search this KB first | Why |
| --- | --- | --- |
| "how do I X" / "what is X" / "why does X exist" | **kb_docs** | Conceptual questions hit READMEs, tutorials, ADRs. Symbol-level chunks usually have no answer. |
| "where is X defined" / "show me the code for X" | **kb_code** | You want a function body, not a paragraph that mentions the name. |
| "how does X interact with Y" | **both, joint** | The answer is probably in a design doc *and* in the call graph. |
| Bug reproduction / failing test | **kb_code** | You want the function, then maybe a doc cross-reference. |
| Onboarding / "what does this repo do" | **kb_docs** | The 30-second answer lives in the README. |

When in doubt, do both and let RRF decide.

## Runnable demo

The runnable companion to this doc lives at
[`python/examples/code_and_docs_kbs_demo.py`](../../python/examples/code_and_docs_kbs_demo.py).
Pointed at the chunkshop repo itself it ingests both KBs, runs four
demo queries, and prints a per-KB summary table. Below is its
last-known-good output (chunkshop repo at branch
`feat/code-and-docs-kbs`, May 2026):

```
========================================================================
# Demo: two-KB ingest — code AND docs from one repo
========================================================================
  repo:        /Users/.../chunkshop
  schema:      chunkshop_code_and_docs_demo
  code table:  chunkshop_code_and_docs_demo.kb_code
  docs table:  chunkshop_code_and_docs_demo.kb_docs

--- Cell 1: kb_code ---
  [code] py: 379 file(s) -> running cell 'code__py'
    -> docs=379 chunks=1963 wall=21.6s embed=15.9s
  [code] java: 1 file(s) -> running cell 'code__java'
    -> docs=1 chunks=1 wall=0.1s embed=0.0s
  [code] go: 1 file(s) -> running cell 'code__go'
    -> docs=1 chunks=4 wall=0.1s embed=0.0s
  [code] ts: 1 file(s) -> running cell 'code__ts'
    -> docs=1 chunks=2 wall=0.1s embed=0.0s
  [code] js: 1 file(s) -> running cell 'code__js'
    -> docs=1 chunks=2 wall=0.1s embed=0.0s
  [code] rs: 101 file(s) -> running cell 'code__rs'
    -> docs=101 chunks=563 wall=26.6s embed=24.8s

--- Cell 2: kb_docs ---
  [docs] md: 220 file(s) -> running cell 'docs__md'
    -> docs=220 chunks=3602 wall=139.8s embed=127.0s
  [docs] txt: 10 file(s) -> running cell 'docs__txt'
    -> docs=10 chunks=18 wall=1.2s embed=0.5s

--- Cell summary ---
  kb_code: docs=484 chunks=2535 wall=48.7s embed=40.6s
  kb_docs: docs=230 chunks=3620 wall=140.9s embed=127.5s

--- Demo queries (top-5 each) ---

  --- code-style query against kb_code: 'IncrementalSource cursor advancement'
      1. assert_cursor_advances  (fts,semantic, score=0.0328) __init__.py
         def assert_cursor_advances(source: IncrementalSource) -> None:
      2. merge_cursor  (fts,semantic, score=0.0320) __init__.py
         def merge_cursor(source: IncrementalSource, prev: dict, docs: list) -> dict:
      ...

  --- same query against kb_docs: 'IncrementalSource cursor advancement'
      1. 2026-05-25-sp1-connector-plugin-foundation.md  (fts,semantic, score=0.0325, lang=en)
      2. 2026-05-25-sp2-chunkshop-connectors-bulk-port.md  (fts,semantic, score=0.0315, lang=en)
      3. authoring-connectors.md  (fts,semantic, score=0.0308, lang=en)
      4. incremental-sources.md  (fts,semantic, score=0.0295, lang=en)
      ...

  --- how-to query against kb_docs: 'how do I add a new connector'
      1. README.md  (fts,semantic, score=0.0315, lang=en)
         ## Authoring a new connector
         1. Create `chunkshop_connectors/<name>/connector.py` with a class decorated `@ver...
      2. 2026-05-25-chunkshop-connector-plugin-foundation-design.md  (fts,semantic, score=0.0291, lang=en)
      3. 2026-05-25-sp1-connector-plugin-foundation.md  (fts,semantic, score=0.0288, lang=en)
      ...

  --- joint query across both KBs: 'hybrid_search Postgres fusion'
      1. [kb_code] hybrid_search  (fts,semantic, score=0.0325) search.py
         def hybrid_search(
      2. [kb_docs] CHANGELOG.md  (fts,semantic, score=0.0320, lang=en)
      3. [kb_docs] fast-mode-rag-benchmarks.md  (fts,semantic, score=0.0313, lang=en)
      4. [kb_docs] hybrid-search.md  (fts,semantic, score=0.0304, lang=en)
      ...

========================================================================
# Per-KB summary
========================================================================
  table          rows   docs   avg_chars
  kb_code        2535    484         364
  kb_docs        3620    229         745

  Per-query top-1 hit:
    - code-style query against kb_code        -> symbol=assert_cursor_advances  (score=0.0328)
    - same query against kb_docs              -> path=2026-05-25-sp1-connector-plugin-foundation.md  (score=0.0325)
    - how-to query against kb_docs            -> path=README.md  (score=0.0315)
    - joint query across both KBs             -> [kb_code] symbol=hybrid_search  (score=0.0325)
```

Notice the per-query outcomes:

- "IncrementalSource cursor advancement" hits **functions named exactly
  that thing** in kb_code (`assert_cursor_advances`, `merge_cursor`) and
  the spec/design docs that describe them in kb_docs.
- "how do I add a new connector" — a prose-style intent — lands the
  README's "Authoring a new connector" section in kb_docs.
- The joint query for "hybrid_search Postgres fusion" surfaces the
  `hybrid_search` function definition (from kb_code) **above** the
  documentation about it (from kb_docs). For a coding agent that's
  often what you want; for a human onboarding, you might want to bias
  the other direction with `fusion="weighted"` and a higher weight on
  kb_docs.

## Required extras

```bash
uv pip install -e ".[dev,extractors,all-backends,lede,lang]"
```

- `extractors` brings `rake-nltk` (for the `rake_keywords` extractor on
  the docs side).
- `lede` powers the `code_summary` extractor's default backend.
- `lang` brings `langdetect` for the `lang_detect` extractor on the
  docs side.

## See also

- [code-aware-chunking.md](code-aware-chunking.md) — the prior single-KB
  pattern that only handled Python.
- [authoring-connectors.md](authoring-connectors.md) — once you have
  the two-KB layout, the typical next step is replacing the `files`
  source with a connector-driven source so re-ingest is incremental.
- [incremental-sources.md](incremental-sources.md) — cursor semantics
  for the connector you'll likely write next.
