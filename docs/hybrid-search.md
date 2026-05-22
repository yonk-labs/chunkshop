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

## Enabling FTS at ingest (`target.fts`)

For ingest-driven setups you can skip the manual `ensure_fts` call entirely by
declaring `target.fts` in your cell YAML. The sink builds (or validates) the FTS
index as part of the ingest run — no separate startup call needed:

```yaml
target:
  type: postgres
  database: chunkshop_samples
  table: handbook
  mode: overwrite           # or create_if_missing
  fts:
    enabled: true
    language: english       # default; any PostgreSQL text-search config name
```

`FtsConfig` has two fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | `bool` | `false` | Opt in to FTS index creation |
| `language` | `str` | `"english"` | PostgreSQL text-search config name; allowlisted |

`language` is allowlisted (the same set accepted by `ensure_fts`) because it is
concatenated into the generated-column DDL where it cannot be a bound parameter.

### Behavior per mode

| `target.mode` | What happens with `fts.enabled: true` |
|---|---|
| `overwrite` | Drops + re-creates the table, then calls `ensure_fts` to build the index. |
| `create_if_missing` | Creates the table (if absent), then calls `ensure_fts` to build the index. |
| `append` | Validates that the FTS index already exists; raises `RuntimeError` if it's missing (the table was created without FTS). Re-create with `overwrite` or `create_if_missing` to add the index. |

The validation on `append` guards against querying a non-existent FTS index at
search time — if the index is absent you get a clear error at ingest rather than
a silent empty FTS leg at query time.

### Per-backend support

The same `target.fts` block works across all four backends, but FTS fidelity
differs (see **Which backends** above):

| Backend | FTS structure built |
|---|---|
| Postgres + pgvector | `search_vector tsvector` generated column + GIN index |
| SQLite + sqlite-vec | FTS5 external-content table |
| MariaDB 11.7+ | FULLTEXT index |
| ClickHouse 24.10+ | `tokenbf_v1` data-skipping index (degraded — binary match, no ranking) |

When `fts.enabled: false` (the default) the sink writes no FTS structure and the
manual `ensure_fts` pattern from earlier in this doc applies.

---

## The `chunkshop search` CLI

`chunkshop search` is a one-shot query command. It loads the cell config, embeds
the query with the same embedder that ran ingest, calls `search()`, and prints
results as human-readable text or JSON:

```text
Usage: chunkshop search [OPTIONS]

Options:
  --config PATH                   Path to the YAML/JSON cell config. [required]
  --query TEXT                    Free-text query string. [required]
  --k INTEGER                     Number of results to return. [default: 10]
  --return [chunks|summary+chunks|summary]
                                  What the result carries: fused hit list,
                                  summary, or both. [default: chunks]
  --legs TEXT                     Comma-separated retrieval legs (semantic,
                                  fts). [default: semantic,fts]
  --where TEXT                    Filter as KEY=VALUE (source=x, tags=a,b,
                                  metadata.k=v). Repeatable.
  --json                          Emit results as JSON instead of human-
                                  readable text.
  --help                          Show this message and exit.
```

### One-shot examples

```bash
# Default: semantic + FTS, top 10, human-readable text
chunkshop search --config cell.yaml --query "how do we rotate API keys"

# Fast-mode summary — collapses hits before display, top 5
chunkshop search --config cell.yaml --query "how do we rotate API keys" \
    --return summary+chunks --k 5

# Tenant scoping via source_tag — only chunks ingested with source=infra_v2
chunkshop search --config cell.yaml --query "firewall rules" \
    --where source=infra_v2

# Tag filter + metadata filter (repeatable)
chunkshop search --config cell.yaml --query "costs" \
    --where tags=billing,ops --where metadata.category=quarterly

# JSON output for programmatic consumption
chunkshop search --config cell.yaml --query "alpha" --k 5 --json
```

### Text output shape

```
SUMMARY:
<extractive summary string>       ← only present when --return includes summary

1. [0.8312] doc_id#3  The first 120 chars of the chunk body…
2. [0.7991] doc_id#1  The next result…
…
```

### JSON output shape (`--json`)

```json
{
  "query": "alpha",
  "summary": null,
  "chunks": [
    {
      "doc_id": "handbook_2024",
      "seq_num": 3,
      "score": 0.8312,
      "text": "original chunk body…",
      "legs": ["fts", "semantic"]
    }
  ]
}
```

`summary` is `null` when `--return chunks` (the default). `chunks` is an empty
array when `--return summary`. Both are populated for `--return summary+chunks`.

### `--where` filter syntax

`--where` is repeatable. Each item is `KEY=VALUE`:

| Form | Meaning |
|---|---|
| `source=<tag>` | Exact match on the write-once `source` provenance column. Useful for tenant scoping. |
| `tags=a,b` | Array overlap on the tags column — chunks with at least one of the listed tags. |
| `metadata.<key>=<val>` | Top-level metadata equality (jsonb containment on Postgres). |

Unknown keys raise `ValueError` and exit non-zero.

---

## `search()` + `SearchResult` — three return modes

`search()` in `chunkshop.search_common` is the typed Python entry point. It
wraps `hybrid_search` (Postgres default) and optionally `summarize_hits` into a
single call, returning a `SearchResult`:

```python
@dataclass
class SearchResult:
    chunks: list[Hit]       # fused Hit list (empty when return_mode="summary")
    summary: str | None     # extractive summary (None when return_mode="chunks")
    query: str              # the original query string
```

### Signature

```python
from chunkshop.search_common import search, SearchResult

result: SearchResult = search(
    dsn,
    schema="chunkshop_samples",
    table="handbook",
    query="how do we rotate API keys",   # required for "fts" leg
    query_vec=qvec,                       # required for "semantic" leg
    k=10,
    legs=("semantic", "fts"),             # default
    where=None,
    fusion="rrf",                         # "rrf" or "weighted"
    return_mode="chunks",                 # "chunks" | "summary+chunks" | "summary"
    summarize_fn=None,                    # required for any non-chunks mode
    summary_hints=None,                   # explicit hint terms; overrides auto-derivation
    summary_expand=None,                  # HintExpansion for synonym/lemma widening
    summary_max_length=1200,
    language="english",
)
```

### The three `return_mode` values

| `return_mode` | `chunks` field | `summary` field | Lede imported? |
|---|---|---|---|
| `"chunks"` (default) | Full fused hit list | `None` | No — import-boundary-clean |
| `"summary+chunks"` | Full fused hit list | Extractive summary string | Yes |
| `"summary"` | Empty list | Extractive summary string | Yes |

`"chunks"` mode is the zero-dep fast path. It does not import lede or call
`summarize_hits` at all — choosing `return_mode="chunks"` keeps the call
lede-free regardless of whether the `[lede]` extra is installed.

### Auto query-hint summarization

For `"summary"` and `"summary+chunks"` modes, `search()` auto-derives hint terms
from the query via `lede.extract.top_terms` (through the `lede_top_terms` shim)
when `summary_hints` is not given. You can override with:

- **`summary_hints`** — explicit list of terms to bias the summary toward. Caller
  wins; auto-derivation is skipped.
- **`summary_expand`** — a `HintExpansion` config that widens the derived hints
  with synonyms / lemmas via lede_spacy. Applied after `summary_hints` if given.

Both knobs are optional; omitting them gives you query-auto-biased summarization
out of the box.

### Short runnable example

```python
import os
from chunkshop.config import FastembedEmbedder
from chunkshop.embedders import load_embedder
from chunkshop.search_common import search
from chunkshop.summarizers.lede import summarize      # needs [lede] extra

DSN    = os.environ["CHUNKSHOP_DSN"]
QUERY  = "how do we rotate API keys"

embedder = load_embedder(
    FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384)
)
qvec = embedder.embed([QUERY])[0]

result = search(
    DSN,
    schema="chunkshop_samples",
    table="handbook",
    query=QUERY,
    query_vec=qvec,
    k=10,
    return_mode="summary+chunks",
    summarize_fn=summarize,           # injected — chunkshop core never imports lede
)

print(result.summary)                 # extractive, query-biased, ≤1200 chars
for h in result.chunks:
    print(h.score, h.doc_id, h.seq_num, h.legs)
```

The FTS language used at query time is inferred from `tgt.fts.language` in the
CLI. In direct `search()` calls, pass `language=` explicitly if your table was
indexed with a non-English config.

---

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
