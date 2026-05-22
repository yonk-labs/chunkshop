# Hybrid search + Fast-mode summarization (the read side)

chunkshop's ingest side writes vector tables; this doc covers the **read side** —
the in-process Python API for querying them. It does three things:

1. **Semantic search** — pgvector-style cosine top-K, the classic vector query.
2. **Keyword (FTS) search** — full-text search over `original_content`.
3. **Hybrid search** — run both legs and fuse them into one ranked list.

On top of retrieval it ships a **Fast-mode RAG** helper, `summarize_hits`, that
collapses the K retrieved chunks into a single query-biased summary before you
send them to an LLM — ~90% fewer input tokens for ~2–3 ms. Full numbers and
caveats live in [`fast-mode-rag-benchmarks.md`](fast-mode-rag-benchmarks.md).

> Read-side surfaces are **in-process Python**. The
> [`query-clients.md`](query-clients.md) doc covers the language-neutral SQL
> path (raw pgvector cosine from JS/TS, Rust, Go); this doc covers the Python
> retrieval + Fast-mode API that wraps it with FTS and fusion.

## Which backends

All four sink backends expose the same read API (`ensure_fts`, `keyword_search`,
`semantic_search`, `hybrid_search`), but FTS fidelity differs:

| Backend | Module | FTS leg |
|---|---|---|
| Postgres + pgvector | `chunkshop.search` | Full ranked FTS (`tsvector` + `ts_rank`) |
| SQLite + sqlite-vec | `chunkshop.search_sqlite` | Full ranked FTS (FTS5 `bm25`) |
| MariaDB 11.7+ | `chunkshop.search_mariadb` | Full ranked FTS (FULLTEXT relevance) |
| ClickHouse 24.10+ | `chunkshop.search_clickhouse` | **Degraded** — token-filter, binary match (no ranking) |

ClickHouse's FTS leg is degraded **by design**: it uses
`multiSearchAnyCaseInsensitive` token matching with a binary 1.0/absent score
(no `ts_rank`-equivalent). A document either contains a query token or it
doesn't. Semantic search and fusion work identically; only the FTS leg's
ranking signal is weaker. See `fast-mode-rag-benchmarks.md` §8 (L9) for the
coverage caveat.

## The `Hit` contract

Every search function returns `list[Hit]`. `Hit` is a frozen dataclass
(`chunkshop.search_common.Hit`):

| Field | Type | Meaning |
|---|---|---|
| `doc_id` | `str` | Document id |
| `seq_num` | `int` | Chunk sequence within the doc |
| `text` | `str` | `original_content` — raw chunk body, safe to show / grep / cite |
| `score` | `float` | Fused score, **higher = better** |
| `metadata` | `dict` | Per-chunk metadata (`heading`, `strategy`, extracted entities, …) |
| `legs` | `tuple[str, ...]` | Which legs matched, e.g. `("fts", "semantic")` |
| `embedded_text` | `str` | `embedded_content` — the text that was actually embedded |

The distinction between `text` and `embedded_text` matters for Fast mode. With
the `hierarchy` chunker, `embedded_text` **prepends the section heading / case
caption** to the chunk body (e.g. `"Apple v. Pepper\n\n…"`), so it carries
framing context that `text` (the raw body) drops. `summarize_hits` builds its
summary from `embedded_text` by default for exactly this reason — see Fast mode
below. (`embedded_text` defaults to `""` and falls back to `text` when empty.)

## Per-backend signatures

All four modules expose the same four functions. The only signature difference
is the `schema` argument:

- Postgres / MariaDB / ClickHouse: `schema=` is the database/schema name.
- SQLite: `schema=""` (default — SQLite has no schemas; the table lives in the
  attached DB file given by the `dsn`).

```python
ensure_fts(dsn, *, schema, table, language="english") -> None

keyword_search(dsn, *, schema, table, query, k, where=None,
               language="english") -> list[Hit]

semantic_search(dsn, *, schema, table, query_vec, k, where=None) -> list[Hit]

hybrid_search(dsn, *, schema, table, query=None, query_vec=None, k,
              legs=("semantic", "fts"), where=None, fusion="rrf",
              weights=None, rrf_k=60, language="english",
              candidate_multiplier=3) -> list[Hit]
```

(`language=` is accepted on the SQLite/ClickHouse `hybrid_search` for API
symmetry but their FTS legs don't use a regconfig the way Postgres does.)

## `ensure_fts` — opt-in FTS index

FTS is **opt-in**. A chunkshop table written by ingest has no full-text index
until you build one. Call `ensure_fts` once (it's idempotent — safe to call on
every startup) before using the `"fts"` leg:

```python
from chunkshop.search import ensure_fts

ensure_fts(DSN, schema="chunkshop_samples", table="handbook")
```

On Postgres this adds a `search_vector tsvector` column **generated** from
`original_content` plus a GIN index, so it stays in sync with no
application-side maintenance. If you only ever run `semantic_search`, you can
skip `ensure_fts` entirely.

`language` is allowlisted (`"english"` or `"simple"` on Postgres) because it's
concatenated into the generated-column DDL where it can't be a bound parameter.

## Worked example — embed a query, run hybrid search, inspect hits

The query must be embedded with the **same model the ingest cell used**. Use
chunkshop's own embedder loader so the model registration and pooling match:

```python
import os
from chunkshop.config import FastembedEmbedder
from chunkshop.embedders import load_embedder
from chunkshop.search import ensure_fts, hybrid_search, semantic_search

DSN    = os.environ["CHUNKSHOP_DSN"]
SCHEMA = "chunkshop_samples"
TABLE  = "handbook"
QUERY  = "how do we rotate API keys"

# 1. Embed the query with the ingest cell's embedder.
#    load_embedder returns an Embedder; .embed takes a list and returns an
#    ndarray of shape (n, dim) — take row 0 for the single query vector.
embedder = load_embedder(
    FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384)
)
qvec = embedder.embed([QUERY])[0]      # ndarray, shape (384,)

# 2. (Once) build the FTS index so the "fts" leg has something to query.
ensure_fts(DSN, schema=SCHEMA, table=TABLE)

# 3. Hybrid search: semantic + FTS, RRF-fused, top 10.
hits = hybrid_search(
    DSN,
    schema=SCHEMA,
    table=TABLE,
    query=QUERY,           # used by the "fts" leg
    query_vec=qvec,        # used by the "semantic" leg
    k=10,
    legs=("semantic", "fts"),
    fusion="rrf",
)

for h in hits:
    print(f"{h.score:.4f}  {h.doc_id}#{h.seq_num}  legs={h.legs}  "
          f"{h.metadata.get('heading', '')}  {h.text[:80]!r}")
```

Semantic-only or FTS-only are just the single-leg functions:

```python
# Pure vector top-K (no FTS index needed):
hits = semantic_search(DSN, schema=SCHEMA, table=TABLE, query_vec=qvec, k=10)

# Pure keyword (needs ensure_fts):
from chunkshop.search import keyword_search
hits = keyword_search(DSN, schema=SCHEMA, table=TABLE, query=QUERY, k=10)
```

> For SQLite, swap the import to `chunkshop.search_sqlite` and pass
> `schema=""` (or omit it): `hybrid_search(DSN, table=TABLE, query=QUERY,
> query_vec=qvec, k=10)`.

### The `where` filter (filter-only, not a ranking leg)

`where` is a metadata predicate applied to **every** leg before ranking. It
does not contribute to the score — it only excludes rows. Three supported keys:

```python
hits = hybrid_search(
    DSN, schema=SCHEMA, table=TABLE, query=QUERY, query_vec=qvec, k=10,
    where={
        "source": "handbook_v2",       # source = 'handbook_v2'
        "tags": ["security", "ops"],    # tag overlap (case-insensitive)
        "metadata": {"category": "policy"},  # see backend note below
    },
)
```

- `{"source": "..."}` — exact match on the write-once `source` provenance column.
- `{"tags": [...]}` — array/list overlap; query values are casefolded.
- `{"metadata": {...}}` — on **Postgres** this is jsonb containment (`@>`); on
  the other backends it's **top-level key equality** on the metadata column.

All filter values are bound parameters — never interpolated. Unknown keys raise
`ValueError`.

**Caution on tag filters:** don't hard-filter on free-text *keyword* tags (the
ones a keyword extractor produces). The query's keyword ("keys") and the
document's extracted tag ("secrets") are often a synonym mismatch that exact
overlap can't bridge — it silently drops recall. Reserve `where` for
**structured** fields (`source`, `category`, tenant, date). Use keyword tags for
faceting or a soft re-rank, not as a recall-gating predicate. Measured result:
`fast-mode-rag-benchmarks.md` §5.

## Fusion: RRF vs weighted

`hybrid_search` over-fetches `max(k * candidate_multiplier, k)` candidates per
leg (default `candidate_multiplier=3`), fuses, then truncates to `k`. The
over-fetch lets a chunk that ranks beyond `k` *within* one leg still land in the
fused top-K if both legs agree on it.

### `fusion="rrf"` (default) — Reciprocal Rank Fusion

Each leg contributes `1 / (rrf_k + rank)` (rank is 1-based; `rrf_k=60` default).
A row matched by **both** legs sums its contributions and ranks higher — that's
the point of hybrid. RRF ignores raw score magnitudes (it uses ranks only), so
it's robust to legs whose scores live on different scales (cosine similarity vs
`ts_rank`). **Use RRF unless you have a reason not to.**

### `fusion="weighted"` — min-max normalized weighted sum

Each leg's scores are min-max normalized to `[0, 1]`, then summed as
`sum(weights[leg] * norm)`. Default weight is `1.0` per leg. Use this when one
leg's signal is genuinely higher-quality and you want to bias toward it:

```python
hits = hybrid_search(
    DSN, schema=SCHEMA, table=TABLE, query=QUERY, query_vec=qvec, k=10,
    fusion="weighted",
    weights={"semantic": 0.8, "fts": 0.2},   # trust the vector leg more
)
```

### When NOT to fuse

Hybrid is not a free win. On **strong-embedding corpora the semantic leg often
dominates**, and fusing in a weaker FTS leg *dilutes* it — measured MRR went
*down* vs semantic-only on such a corpus. Fusion earns its keep only when the
legs are **complementary** — FTS pulling in docs that semantic missed (rare
exact terms, codes, names) and vice versa.

Practical guidance:
- Default to semantic-only; add the FTS leg for the queries semantic misses
  (literal identifiers, synonyms the embedder doesn't bridge).
- When you do fuse, quality-weight the legs (semantic ≫ fts) rather than
  treating them equally.

Details and the dilution measurement: `fast-mode-rag-benchmarks.md` §4 and §8 (L6).

## Fast-mode RAG — `summarize_hits`

Classic RAG sends the LLM the top-K chunks verbatim. At K=10–30 that's thousands
of input tokens, most of it padding around the relevant sentence. **Fast mode**
inserts a deterministic ~2–3 ms summarization step between retrieval and the
LLM: collapse the K chunks into one query-biased summary and send *that*.

```
query
  ├─ parse the query into keywords     (lede top_terms)
  ├─ hybrid search                     (semantic + FTS, RRF) -> top-K hits
  ├─ summarize_hits                    (one query-biased summary)
  └─ send ONE summary to the LLM       (instead of K raw chunks)
```

The helper lives in `chunkshop.search_common`:

```python
summarize_hits(hits, summarize_fn, *, max_length=1200, hints=None,
               hint_focus=0.7, hint_mode="soft", prepend_headings=True,
               use_embedded=True) -> str
```

`summarize_fn` is **injected** — chunkshop core never imports a summarizer. Pass
`chunkshop.summarizers.lede.summarize` (or any callable with the
`(text, **kwargs) -> str` contract). The lede path needs the `[lede]` extra
(`uv sync --extra lede`), which pulls the sibling `extractive_summary` repo.

### The full recipe end-to-end

```python
import os
from lede.extract import top_terms                     # needs the [lede] extra
from chunkshop.config import FastembedEmbedder
from chunkshop.embedders import load_embedder
from chunkshop.search import ensure_fts, hybrid_search
from chunkshop.search_common import summarize_hits
from chunkshop.summarizers.lede import summarize        # the injected summarize_fn

DSN    = os.environ["CHUNKSHOP_DSN"]
SCHEMA = "chunkshop_samples"
TABLE  = "handbook"
QUERY  = "how do we rotate API keys"

embedder = load_embedder(
    FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384)
)
qvec = embedder.embed([QUERY])[0]

ensure_fts(DSN, schema=SCHEMA, table=TABLE)             # once

# 1. Parse the query into weighted keywords to bias the summary.
hints = top_terms(QUERY, n=6)                           # tuple[str, ...]

# 2. Retrieve.
hits = hybrid_search(
    DSN, schema=SCHEMA, table=TABLE, query=QUERY, query_vec=qvec, k=10,
    legs=("semantic", "fts"), fusion="rrf",
)

# 3. Collapse K chunks into ONE query-biased summary.
context = summarize_hits(
    hits,
    summarize,                  # injected lede summarizer
    max_length=1200,
    hints=hints,                # bias toward the query's keywords
    hint_focus=0.7,
    hint_mode="soft",
    prepend_headings=True,      # re-attach the dropped captions/titles
)

# 4. Send ONE summary to the LLM instead of N raw chunks.
prompt = f"Answer the question using only the context.\n\nContext:\n{context}\n\nQuestion: {QUERY}"
# llm(prompt) ...
```

`hints` can be a plain list/tuple (`top_terms` output) or a `{term: weight}`
dict. When `hints=None`, the hint kwargs are **not** forwarded — the summarizer
stays on its own defaults (so summarizers that don't accept `hints` still work).
`hint_mode="soft"` biases toward hint-bearing sentences without removing
others; `"hard"` keeps only sentences containing a hint.

### Why it works — and why `prepend_headings` matters

Two things make Fast mode viable, both measured in
[`fast-mode-rag-benchmarks.md`](fast-mode-rag-benchmarks.md):

1. **Token savings.** On 772 real SCOTUS docs, a single summary is ~234 tokens
   vs ~2,431 for the top-10 raw chunks — **~90% fewer input tokens** (and ~90%
   lower input cost) for ~2–3 ms, costing about one query in ten of accuracy
   (LLM-judged). Savings *grow* with document length.

2. **Query-hint biasing** is what keeps the summary answer-bearing — answer
   preservation went 5/7 (hinted) vs 2/7 (un-hinted). Parsing the query into
   keywords and biasing the summary is a measured win, not a guess.

3. **Heading-prepend preserves structural facts.** This is the non-obvious one.
   The single most-dropped fact is the **case caption / section title** ("Apple
   v. Pepper"). The `hierarchy` chunker stores it as a *heading line*, and lede
   is an extractive *sentence* summarizer — so it compresses the heading right
   back out, at every `max_length`. Feeding heading-bearing `embedded_text` as
   input alone barely helps (retention 0.36 → 0.38). The fix that works is to
   **prepend the deduped chunk headings to the summary output**: facts retention
   0.36 → **0.72**, caption retention 0.03 → **0.90**, for ~tens of tokens. That
   is exactly what `prepend_headings=True` (the default) does — it dedupes
   `metadata["heading"]` across hits (case-insensitive, in hit order, capped at
   the first 5 distinct headings to avoid caption noise on broad retrievals) and
   prepends them. Bulking up `max_length` instead never reaches raw and collapses
   your token savings — there's no knee.

`use_embedded=True` (default) builds the summary body from `embedded_text`
(heading-bearing, falling back to `text` when empty) rather than the raw body.

### Best-practice cautions (read before shipping)

- **Don't hard-filter on free-text keyword tags.** Synonym mismatch silently
  drops recall. Use structured fields in `where`; use keyword tags for faceting
  or soft re-rank. (§5)
- **Semantic-only often beats hybrid on strong-embedding corpora** — fusion
  dilutes. Add FTS for the queries semantic misses, and weight legs by quality
  rather than fusing reflexively. (§4, §8 L6)
- **Fast mode is a volume/cost play.** ~90% token savings for ~one-in-ten
  accuracy loss is a clear win for high-volume RAG; for high-stakes single
  lookups, send raw chunks (or summary + an "expand if unsure" escape hatch).
  (§7a)

## See also

- [`fast-mode-rag-benchmarks.md`](fast-mode-rag-benchmarks.md) — full numbers,
  methodology, and limitations behind every claim here.
- [`query-clients.md`](query-clients.md) — the language-neutral SQL query path
  (raw pgvector cosine from Python / JS-TS / Rust / Go).
- [`summaries.md`](summaries.md) — ingest-time summarization (`summary_embed`,
  `hierarchical_summary`). `summarize_hits` is the *query-time* counterpart.
- [`storage-model.md`](storage-model.md) — what each column holds
  (`original_content` vs `embedded_content` is the `text` vs `embedded_text`
  split you see on `Hit`).
