# Chunkshop → pg-raggraph Emission Contract

**Status:** Active — gates the A/B graph-vs-naive experiment.
**Mission brief:** `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`
**Chunkshop version at time of writing:** 0.7.0 (commit ef2aceb on `main`).
**Audience:** pg-raggraph maintainers implementing `resolve_entity()`, retrieval modes, and the A/B runner.

## TL;DR
Chunkshop emits two graph-leg inputs on every ingest: (1) Tier-1.5 fact rows (`kind='fact'`) with subject/predicate/object triples and (2) Tier-1 cooccur edges in chunk metadata. This doc nails down the field-by-field shapes, ordering invariants, null conventions, and the verdict criteria for whether graph-leg retrieval beats naive vector retrieval. pg-raggraph implements `resolve_entity()` + a retrieval-mode harness + the A/B runner against this contract.

## 1. Tier-1.5 Fact Rows (`kind='fact'`)
*(filled in Task 3)*

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
