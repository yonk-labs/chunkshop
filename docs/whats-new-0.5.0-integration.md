# chunkshop 0.5.0 — what's new & how to use it (integration brief)

**Audience:** an AI copilot or developer in *another* project integrating chunkshop 0.5.0. Self-contained — paste this whole file into a fresh session.

**TL;DR:** chunkshop is now ingest **and** retrieval. 0.5.0 adds (1) **hint-biased extractive summarization** (lede v0.4), (2) a **hybrid search surface** (`chunkshop.search`) across Postgres/SQLite/MariaDB/ClickHouse, and (3) a **"Fast-mode" RAG path** — `summarize_hits` collapses retrieved chunks into one query-biased summary before the LLM sees them (~90% input-token savings on real corpora, ~1-in-10 accuracy cost). Plus opt-in `target.fts` and a `chunkshop search` CLI.

Install: `pip install "chunkshop==0.5.0"`. Optional extras below.

---

## 1. What chunkshop is, in one paragraph

chunkshop is a **deterministic ingest-to-pgvector tool**: one YAML "cell" = `source → chunker → embedder → extractor → sink`. Through 0.4.x it was ingest-only. 0.5.0 adds a **read side** — you can now search what you ingested and summarize the results. Same wire format across Python and the Rust port; vectors are interchangeable. It's a library *and* a CLI.

## 2. Install / extras

```bash
pip install "chunkshop==0.5.0"
# optional:
pip install "chunkshop[lede]"        # hint-biased summarization + reports (pulls lede>=0.4.5)
pip install "chunkshop[lede-spacy]"  # hint expansion: lemma/synonyms/similar
pip install "chunkshop[sqlite]" "chunkshop[mariadb]" "chunkshop[clickhouse]"  # backends
pip install "chunkshop[all-backends]"
```
- **Summarization needs `[lede]`.** If you only do retrieval (return `chunks`), you don't need it — chunkshop never imports lede on the chunks-only path.
- `synonyms` expansion needs `lede-spacy[synonyms]` (nltk+WordNet); `similar` needs a spaCy vector model (`en_core_web_md`). `lemma` needs any spaCy model.

## 3. The three things that are new

### 3a. Hint-biased summarization (ingest-time)

The `summary_embed` chunker can bias its extractive summary toward query terms via lede v0.4 hints. Pass them under `summarizer.kwargs`:

```yaml
chunker:
  type: summary_embed
  base: {type: hierarchy}
  summarizer:
    mode: callable
    module: chunkshop.summarizers.lede
    kwargs:
      hints: ["onboarding", "benefits"]   # or {term: weight}
      hint_focus: 0.7                      # 0=ignore .. 1=only-hint pool
      hint_mode: soft                      # soft=bias, hard=filter
```
- **Per-document hints:** add `hints_from_meta: lede_hints` to pull hints from each doc's `metadata["lede_hints"]` (overrides static `kwargs.hints`).
- **No-hint output is byte-identical to 0.4.x** — adopting this changes nothing unless you set hints.

**New extractor — `lede_top_terms`** (tags a chunk with its salient terms):
```yaml
extractor:
  type: lede_top_terms
  n: 10
  kinds: [words, phrases]
  expand: {kinds: [lemma]}     # optional: enrich tags with morphological variants
```
Produces `tags = ["county budget", "taxes", ...]` and `metadata["top_terms"] = [{term, score, kind}, ...]` (real composite scores from lede 0.4.1).

### 3b. Hybrid search surface (`chunkshop.search`)

```python
from chunkshop.search import hybrid_search, semantic_search, keyword_search

hits = hybrid_search(
    dsn, schema="my_db", table="chunks",
    query="who pays property tax?",
    query_vec=embedder.embed(["who pays property tax?"])[0],
    k=10,
    legs=("semantic", "fts"),       # two ranking legs
    where={"source": "memo", "tags": ["legal"]},  # FILTER only, not a ranking leg
    fusion="rrf",                   # or "weighted", weights={"semantic":1.0,"fts":0.6}
)
# hits: list[Hit{doc_id, seq_num, text, score, metadata, legs, embedded_text}]
```
- Backends: **Postgres / SQLite / MariaDB** have full ranked FTS; **ClickHouse** is degraded (token-filter, binary match — by design). Use the matching module: `chunkshop.search` (pg), `search_sqlite`, `search_mariadb`, `search_clickhouse`.
- `Hit.text` is `original_content`; **`Hit.embedded_text`** carries the heading-bearing `embedded_content` (matters for summarization — see 3c).

### 3c. Fast-mode RAG — `summarize_hits` + `search()` return modes

The headline feature. Instead of stuffing 10-30 raw chunks into an LLM prompt, summarize them first:

```python
from chunkshop.search_common import search
from chunkshop.summarizers.lede import summarize  # the injectable summarizer

res = search(
    dsn, schema="my_db", table="chunks",
    query="who pays property tax?",
    query_vec=qv, k=10,
    return_mode="summary+chunks",   # "chunks" | "summary+chunks" | "summary"
    summarize_fn=summarize,         # required for summary modes
)
# res: SearchResult{chunks: list[Hit], summary: str | None, query: str}
print(res.summary)   # one query-biased summary; send THIS to your LLM
```
- `return_mode="chunks"` (default) → `summary=None`, no lede import.
- `summary` → `chunks=[]`, just the summary.
- **Summary hints are auto-derived from the query** (via lede `top_terms`); override with `summary_hints=[...]`, widen with `summary_expand=HintExpansion(kinds=["lemma"])`.
- The summary **prepends the deduped chunk headings/captions** so structural facts (titles, case names) survive extractive compression — feeding `embedded_text` alone isn't enough.

Lower-level helper if you already have hits:
```python
from chunkshop.search_common import summarize_hits
summary = summarize_hits(hits, summarize, max_length=1200,
                         hints=["property", "tax"], prepend_headings=True)
```

### 3d. Opt-in FTS at ingest + the `chunkshop search` CLI

Enable FTS in the cell so the index is built at ingest (no manual step):
```yaml
target:
  type: postgres
  database: my_db
  table: chunks
  mode: create_if_missing
  fts: {enabled: true, language: english}   # default OFF; absent ⇒ ingest unchanged
```
`overwrite`/`create_if_missing` build the index; `append` validates it exists (raises if missing).

CLI (one-shot, scriptable):
```bash
chunkshop search --config cell.yaml --query "who pays property tax?" \
  --k 10 --return summary+chunks --where source=memo --json
```
It loads the cell's embedder, embeds the query, runs hybrid search, optionally summarizes. `--json` mirrors `SearchResult` (`{query, summary, chunks:[{doc_id,seq_num,score,text,legs}]}`).

## 4. End-to-end sample (ingest with FTS → fast-mode search)

```python
from chunkshop.config import CellConfig
from chunkshop.runner import run_cell
from chunkshop.embedders import load_embedder
from chunkshop.config import FastembedEmbedder
from chunkshop.search_common import search
from chunkshop.summarizers.lede import summarize

DSN = "postgresql://user:pw@localhost:5432/mydb"

# 1) ingest a corpus WITH fts enabled
run_cell(CellConfig.model_validate({
    "cell_name": "docs",
    "source": {"type": "files", "glob": "/data/docs/*.md", "id_from": "stem"},
    "chunker": {"type": "hierarchy"},
    "embedder": {"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
    "target": {"type": "postgres", "dsn": DSN, "database": "mydb", "table": "chunks",
               "mode": "create_if_missing", "fts": {"enabled": True, "language": "english"}},
}))

# 2) fast-mode search: one summary instead of 10 chunks
emb = load_embedder(FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384))
q = "what is the on-call rotation policy?"
res = search(DSN, schema="mydb", table="chunks", query=q,
             query_vec=emb.embed([q])[0], k=10,
             return_mode="summary+chunks", summarize_fn=summarize)

llm_answer = my_llm.chat(f"Question: {q}\n\nContext:\n{res.summary}\n\nAnswer:")
```

## 5. Use cases

- **Token-cheap RAG ("Fast mode"):** summarize retrieved chunks → ~90% fewer input tokens for ~2-3ms, ~1-in-10 accuracy cost. Best for high-volume / cost-sensitive RAG. For high-stakes single-answer lookups, send `chunks` (or `summary+chunks` as a hedge).
- **Hybrid retrieval where exact terms matter:** semantic for recall + FTS for names/IDs/codes the embedder misses.
- **Faceting / display:** `lede_top_terms` tags for UI facets or a soft re-rank signal.
- **Multi-tenant scoping:** `where={"source": "tenant_a"}` (the `source` column is write-once provenance — a safe boundary).

## 6. Framing & gotchas (read before you build)

- **Semantic-only often wins.** On strong-embedding corpora, fusing a weaker FTS leg *dilutes* ranking. Add FTS for the queries semantic misses; weight legs by quality; don't fuse reflexively. (chunkshop's RRF default is fine; `weighted` lets you down-weight FTS.)
- **Never hard-filter on free-text keyword tags.** Query/doc vocabulary mismatch ("keys" vs "secrets") silently drops the answer. `where` is for **structured** fields (source, category, date, tenant) — not keyword overlap. Keyword tags are for faceting/soft signals.
- **Heading-drop is the summary's main accuracy leak**, not length. `summarize_hits` already prepends headings (default `prepend_headings=True`); keep it on. Cranking `max_length` doesn't fix dropped titles.
- **FTS is OR-joined** (multi-word queries don't require all terms co-occur). ClickHouse FTS is filter-grade (binary), not ranked — by design.
- **lede is optional and lazily imported.** `return_mode="chunks"` and the no-fts ingest path import no lede. Summary modes require the `[lede]` extra.
- **`target.fts` is opt-in and default-off.** Existing tables/ingests are unaffected; enable it to get keyword/hybrid search.

## 7. Where to dig deeper (in the chunkshop repo)

- `docs/hybrid-search.md` — full read-side reference (every signature, the `where` filter, fusion, the CLI, `SearchResult`).
- `docs/fast-mode-rag-benchmarks.md` — the numbers + best practices (token savings, LLM-judge results, the length-sweep / heading-drop findings, limitations).
- `docs/quickstart-summaries.md` / `docs/summaries.md` — ingest-time `summary_embed` vs query-time `summarize_hits`.
- `CHANGELOG.md` → 0.5.0.

---
**Version:** chunkshop 0.5.0 (PyPI + crates.io). Python is the reference implementation; the Rust crate shares the wire format. Summarization/expansion is Python-only.
