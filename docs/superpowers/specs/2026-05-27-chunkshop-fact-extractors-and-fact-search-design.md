# Design Spec — chunkshop Bundled Fact Extractors, Caveman Reducer & fact-search

**Date:** 2026-05-27
**Status:** Design draft, pre-plan
**Topic slug:** chunkshop-fact-extractors-and-fact-search
**Tracking:** GitHub issue yonk-labs/chunkshop#30
**Builds on:** `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md` (ConsolidationChunker, MemorySink, consolidator contract)

## 1. Context & Problem

SP-A shipped the agent-memory write path: `ConsolidationChunker` already emits `kind='fact'` chunks (subject/predicate/object + `support_span` + `confidence`, linked to their episode via `doc_id` + `metadata.source_chunk_seq`), and `MemorySink` stores them in pgvector with bi-temporal supersede/retract. Facts are therefore *already* stored as independently-searchable rows.

Two gaps remain, both packaging rather than architecture:

1. **Fact extraction is bring-your-own.** The consolidator is either `passthrough` (no facts) or a user-wired `CallableConsolidator`. chunkshop ships no batteries-included extractor, so nobody gets facts out of the box. This is the mem0 parity gap: mem0's value is that extraction is built in and `search()` returns reconciled facts.
2. **No first-class fact query.** Retrieving facts means `chunkshop search --where ...` and hand-reconstructing the fact→chunk→doc chain. There is no command that returns a fact *with* its breadcrumb (originating chunk, full doc, summary) in one call.

This spec covers bundled extractors (#1), a `fact-search` command (#2), and documents the deferred separate-facts-table (#3) with the requirements it must meet when built.

## 2. Two-Axis Model (the central reframe)

Fact extraction and text reduction are **orthogonal axes**, not one list of "extractors":

- **Axis 1 — Fact extraction** (text → structured S/P/O triples). Fills the consolidator's *fact* slot.
  - `lede` — salient-sentence facts (extractive).
  - `lede+spaCy` — real dependency-parsed triples.
  - `LLM+lede` — clean triples from lede-prefiltered spans. **Deferred** (needs async pipeline #4, drags config surface, not deterministically testable).
- **Axis 2 — Reduction/compression** (text → smaller text, same meaning, fewer tokens). Implements the existing **summarizer contract** `(text, **kwargs) -> str`.
  - `caveman` — strip fluff/stopwords LLMs don't need. Transversal: runs on facts, chunks, docs, or summaries. Swappable anywhere `lede` is.

The consolidator's **summarizer slot** (`lede | caveman | none`) is independent of its **fact-extractor slot** (`lede | spaCy | [LLM]`).

## 3. Locked Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | First cut | Three local deliverables: `lede` extractor, `lede+spaCy` extractor, `caveman` reducer. LLM+lede deferred. |
| D2 | Config style | **Hybrid**: first-class discriminated `ConsolidatorConfig` variants for the shipped extractors; keep `CallableConsolidator` as the bring-your-own escape hatch. |
| D3 | Caveman's home | A second implementation of the **summarizer contract** `(text, **kwargs) -> str` (`chunkshop.summarizers.caveman`), NOT a fact extractor. |
| D4 | Caveman first site | Read-time prompt compression in `summarize_hits`. Optional, **off by default** until measured (hot-path perf). |
| D5 | Summary/fact decoupling | Consolidator composes a summarizer slot + a fact-extractor slot independently. |
| D6 | Fact storage | Stays **co-located** as `kind='fact'` rows (no separate table). #3 deferred. |
| D7 | Search pollution fix (interim) | **Kind-aware default filters**: normal `search` defaults to excluding `kind='fact'` rows; `fact-search` defaults to `kind='fact'`. No-op for tables that contain no facts (non-memory cells), so existing chunk-only cells are unaffected. A `--include-facts` flag restores the old all-rows behavior. |
| D8 | Confidence | Documented `[0,1]` = extractor's self-assessed triple reliability, derived per-extractor, **not cross-calibrated**. Drives `confidence_floor`. |
| D9 | Extras gating | `caveman` = no extra; `lede` extractor behind `lede` extra; `lede+spaCy` behind `lede-spacy` extra. Skip-not-fail when absent. |
| D10 | Storage lever | Support a `confidence_floor` so callers can embed only high-confidence facts (attacks row count, the dominant cost). |

## 4. Components & Contracts

### 4.1 Consolidator contract (unchanged, re-affirmed)

```
consolidate(text: str, meta: dict) -> {
    "summary": str,
    "facts": [ {subject, predicate, object, support_span, confidence}, ... ]
}
```

All Axis-1 extractors emit this. `summary` is filled by the composed summarizer slot (D5), not the extractor itself — extractors may return `summary=""` and let the slot fill it.

### 4.2 Axis-1 extractors

- **`lede` extractor** — selects top salient sentences via lede; each becomes a fact whose `support_span` is the sentence, `subject/predicate/object` are sparse/best-effort (proposition-style, aligned to SP-A's degrade path), `confidence` = normalized salience.
- **`lede+spaCy` extractor** — lede selects salient spans → spaCy dependency parse extracts SVO triples + entity types; `confidence` = heuristic (full SVO triple high, partial lower). `support_span` = the source sentence.

Both gate on their extras and skip-not-fail in tests when the extra is absent.

### 4.3 Axis-2 reducer

- **`caveman`** — `(text, **kwargs) -> str`. Removes stopwords/fluff via a deterministic rule set + stopword list (pure Python, no deps). Wireable in: the consolidator summary slot, `SummaryEmbedChunker`, and `summarize_hits` (read-time). First exposed at read-time (D4), optional/off-by-default.

### 4.4 Config shapes (hybrid)

First-class variants added to `ConsolidatorConfig` union (discriminated on `mode`), alongside existing `CallableConsolidator` / `PassthroughConsolidator`:

```yaml
# fact extractor + summarizer slot, both first-class
consolidator:
  mode: lede_spacy          # or: lede
  summarizer: { kind: caveman }   # or lede, or omitted (none)
  confidence_floor: 0.5     # optional; facts below are dropped pre-embed
  fact_max_chars: 1200
```

`caveman` as a summarizer is also usable standalone wherever a summarizer is accepted (`module: chunkshop.summarizers.caveman`).

### 4.5 `fact-search` command (#2)

`chunkshop fact-search --config CELL --query "..."` — a CLI/enrichment layer over existing rows:

1. Hybrid-search the cell's table with `where` defaulting to `kind='fact'` (+ optional `--confidence-floor`, `--subject`, `--predicate`).
2. For each hit fact, walk the breadcrumb: `doc_id` + `source_chunk_seq` → originating episode/chunk; `doc_id` → doc; optional lede/caveman summary of that chunk/doc.
3. Return a packaged result: fact triple + support_span + confidence + chunk link + doc link + (optional) summary. `--json` for machine use.

Breadcrumb reconstruction is a query-time join in the co-located model (no FK); this is the cost #3 would remove.

## 5. Deferred — #3 Separate Facts Table (documented requirements)

Stays deferred until evidence (Concern A below) justifies it. **When built it MUST:**

- Carry **explicit first-class link columns** to (a) the parent chunk and (b) the doc — not just the jsonb `source_chunk_seq` — so the **fact→chunk→doc graph is walkable** ("follow the breadcrumb") for graph build-out.
- Let facts use a **separate/cheaper embedding config** than chunks (Concern B mitigation).
- Keep a single authoritative writer for facts (no dual-write coexistence with the main table).

## 6. Known Concerns (tracked, not blocking this build)

- **A — Search pollution.** `hybrid_search` scans the whole table; `where` is filter-only/optional, so facts compete with chunks in one ranking and can dominate normal RAG results. Interim fix: D7 kind-aware defaults. This pollution is the leading evidence that would un-defer #3.
- **B — Fact-embedding storage explosion.** Facts are tiny text but carry a full-size fixed-dim embedding (same vector cost as a 1,000-word chunk). A chatty corpus can make facts the majority of vector rows, dominating disk + ANN index RAM. Mitigations: int8 default for facts, `confidence_floor` (D10), S/P/O dedup, and #3's cheaper-embedding option.
- **C — Read-time caveman hot path.** Runs on every read; must be fast and optional (D4).
- **#4 async/queued extraction** — deferred; pairs with the LLM extractor.

## 7. Testing

- **Deterministic snapshots** for `lede` and `lede+spaCy` extractors (fixed input → fixed facts), gated on their extras with skip-not-fail.
- **caveman** unit tests: stopword removal, idempotence, empty/short input, that it preserves meaning-bearing tokens.
- **fact-search** integration test against the Postgres test DSN: seed episode + facts, verify breadcrumb reconstruction (chunk + doc + summary) and `confidence_floor`/kind defaults.
- **Search-pollution regression**: assert normal `search` excludes facts by default and `fact-search` returns only facts.
- No live-network tests in this cut (LLM deferred).

## 8. Out of Scope

- LLM+lede extractor (deferred, with #4).
- Separate facts table / its own index (#3, deferred — §5).
- Async/queued extraction pipeline (#4, deferred).
- Capture/forwarding hooks (already out of scope per SP-A D2).
