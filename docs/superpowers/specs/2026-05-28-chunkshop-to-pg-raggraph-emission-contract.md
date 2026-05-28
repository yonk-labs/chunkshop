# Chunkshop → pg-raggraph Emission Contract

**Status:** Active — gates the A/B graph-vs-naive experiment.
**Mission brief:** `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`
**Chunkshop version at time of writing:** 0.7.0 (commit ef2aceb on `main`).
**Audience:** pg-raggraph maintainers implementing `resolve_entity()`, retrieval modes, and the A/B runner.

## TL;DR
Chunkshop emits two graph-leg inputs on every ingest: (1) Tier-1.5 fact rows (`kind='fact'`) with subject/predicate/object triples and (2) Tier-1 cooccur edges in chunk metadata. This doc nails down the field-by-field shapes, ordering invariants, null conventions, and the verdict criteria for whether graph-leg retrieval beats naive vector retrieval. pg-raggraph implements `resolve_entity()` + a retrieval-mode harness + the A/B runner against this contract.

## 1. Tier-1.5 Fact Rows (`kind='fact'`)

**Source files (on `main` HEAD commit ef2aceb):**
- Emission: `python/src/chunkshop/chunkers/consolidation.py` (lines 37-70)
- Field normalization: `python/src/chunkshop/chunkers/_consolidator.py` (lines 28-78)
- Persistence: `python/src/chunkshop/sinks/pg.py` (schema 67-80, write 416/444)
- Consumer hygiene: `python/src/chunkshop/cli.py` (lines 671-729)

### 1.1 Discriminator
A fact row is any chunk whose `metadata->>'kind' = 'fact'`. The discriminator lives in the jsonb `metadata` column — **not** a promoted column. Episode chunks (the "parent" of a fact set) are stamped `kind: 'episode'` at the same site.

Consumers must filter on `metadata->>'kind'` to separate facts from prose chunks. Chunkshop's own `chunkshop search` excludes fact rows by default via `metadata_not: {kind: fact}` (`cli.py:728-729`).

### 1.2 Required fields

Every fact row metadata dict contains the following keys (in addition to whatever doc-level metadata is inherited from the source document):

| Field | Type | Source line | Notes |
|---|---|---|---|
| `kind` | `str` (literal `'fact'`) | `consolidation.py:63` | Discriminator. |
| `subject` | `str \| None` | `consolidation.py:63` / `_consolidator.py:28` | Pass-through from consolidator. |
| `predicate` | `str \| None` | `consolidation.py:63` / `_consolidator.py:29` | Pass-through. No controlled vocabulary enforced by chunkshop. |
| `object` | `str \| None` | `consolidation.py:63` / `_consolidator.py:30` | Pass-through. |
| `support_span` | `str` | `consolidation.py:59-62` | **Plain string excerpt, not a `{start,end}` offset dict.** Length-capped at `cfg.fact_max_chars`; null coerces to `""` (`_consolidator.py:30`). Also written as the chunk's `original_content` AND `embedded_content` (`consolidation.py:70`), so the span participates in semantic search alongside its fact. |
| `confidence` | `float \| None` | `consolidation.py:66` / `_consolidator.py:31` | Range nominally `[0.0, 1.0]` but not enforced. Bundled `lede` / `lede_spacy` consolidators apply a write-time floor at `_consolidator.py:71-78` that DROPS null-confidence facts. BYO callable consolidators MAY emit null — and `chunkshop fact-search`'s read-time floor (`cli.py:1318-1320`) KEEPS those nulls. Consumers should not assume non-null. |
| `truncated` | `bool` | `consolidation.py:60-62, 66` | `True` iff the original support span exceeded `cfg.fact_max_chars` and was cut. |
| `source_chunk_seq` | `int` | `consolidation.py:38, 51, 55-56, 67` | **Doc-local seq_num back-pointer**, NOT a global chunk id. Always `0` (the episode is appended first). To globally identify the parent, combine with `doc_id`: the parent's row id is `f"{doc_id}::0"`. |
| `consolidator` | `str` | `consolidation.py:68` | Name of the consolidator mode (e.g. `"lede"`, `"lede_spacy"`, or a callable's module path for BYO). |
| `extractor` | `str` | `consolidation.py:68` | Duplicates `consolidator`. Present for back-compat with consumer code keyed on the `extractor` metadata key. |

Plus any keys inherited from the source document's metadata via `_strip_transient(dict(doc.metadata or {}))` at `consolidation.py:37`. Fact-specific keys win on collision because they come later in the `{**meta, "kind": "fact", ...}` spread (`consolidation.py:63`).

### 1.3 Failure-mode semantics

If a consolidator callable raises, the chunker emits ONE passthrough episode chunk with `kind: 'episode'` and `consolidation_error: <str(exception)>` in its metadata (`consolidation.py:41-47`). **No fact rows are emitted in that case.** Consumers should not treat "zero facts for a doc" as evidence of an unfactual doc — check for the presence of an episode with `consolidation_error`.

### 1.4 Persistence

- Lands in the standard chunks table (same table as prose chunks). Schema: `metadata jsonb NOT NULL DEFAULT '{}'` (`pg.py:67-80`).
- Row id is `f"{doc_id}::{seq_num}"` (`pg.py:129-130`). Fact rows occupy seq_nums 1..N for a doc; the parent episode is at seq 0.
- `ON CONFLICT (id) DO UPDATE` is idempotent — re-running an ingest against the same doc upserts.
- `metadata` is written via `json_literal(c.metadata)` (`pg.py:444`), which serializes the full nested dict (including any inherited doc metadata) to jsonb.

### 1.5 Dedup & uniqueness

- No explicit `(subject, predicate, object)` dedup in the consolidation chunker. If the consolidator emits the same triple twice for one doc, both land with different seq_nums.
- Primary key uniqueness is on row `id` only.
- Consumers needing a unique triple set must dedupe at query time (e.g. `SELECT DISTINCT metadata->>'subject', metadata->>'predicate', metadata->>'object' FROM ... WHERE metadata->>'kind' = 'fact'`).

### 1.6 Default consumer hygiene

| Consumer | Default fact treatment | Source |
|---|---|---|
| `chunkshop search` (CLI) | Excluded via `metadata_not: {kind: fact}` unless caller passes `--include-facts` or supplies their own `metadata_not` | `cli.py:671-674, 728-729` |
| `chunkshop fact-search` (CLI) | Restricted to fact rows only via `where={"metadata": {"kind": "fact"}}` | `cli.py:1305` |
| Plain SQL consumers (incl. pg-raggraph) | Must filter on `metadata->>'kind'` explicitly | — |

pg-raggraph's graph leg should ingest fact rows by filtering `metadata->>'kind' = 'fact'` and the cooccur edges by reading `metadata->'cooccur'` from non-fact (prose) chunks.

### 1.7 Example fact row

```json
{
  "kind": "fact",
  "subject": "John Roberts",
  "predicate": "wrote opinion in",
  "object": "Bostock v. Clayton County",
  "support_span": "Justice Roberts joined the majority opinion in Bostock v. Clayton County...",
  "confidence": 0.85,
  "truncated": false,
  "source_chunk_seq": 0,
  "consolidator": "lede_spacy",
  "extractor": "lede_spacy"
}
```

(Real example values will be replaced in Task 9 after running the bakeoff-scotus-ab.yaml ingest end-to-end.)

## 2. Tier-1 Cooccur Edges (`metadata['cooccur']`)
*(filled in Task 4)*

## 3. Verdict Criteria — "Did Graph Beat Naive?"
*(filled in Task 6)*

## 4. Required pg-raggraph-Side Artifacts
*(filled in Task 5)*

## 5. Change-Management
Shape changes require:
1. A PR to chunkshop bumping this doc's version + a note in `CHANGELOG.md`.
2. A coordinated PR in pg-raggraph updating `resolve_entity()` / retrieval modes.
3. Until both land, the A/B experiment uses the prior shape.

## Source-of-truth file citations
| Emission | Source file (on `main` HEAD) |
|---|---|
| Tier-1.5 facts | `python/src/chunkshop/chunkers/consolidation.py` (see §1) |
| Tier-1 cooccur | `python/src/chunkshop/extractors/cooccurrence.py` (see §2) |
| Persistence (jsonb metadata) | `python/src/chunkshop/sinks/pg.py` |
| Consumer hygiene examples | `python/src/chunkshop/cli.py`, `python/src/chunkshop/search.py` |
