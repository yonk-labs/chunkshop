# A/B Gate (chunkshop ↔ pg-raggraph) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship chunkshop's side of the pg-raggraph A/B experiment — a contract doc that documents the Tier-1.5 fact + Tier-1 cooccur emission shapes, verdict criteria for "did graph beat naive", two A/B-ready ingest configs, and a sanity test that proves the emission is well-shaped before pg-raggraph runs the experiment.

**Architecture:** Four deliverables, all docs-and-config plus one Python test. No new chunker/extractor code — emission code from PR #33/#34 is fixed. Plan is structured around five Success Criteria (SC-001..SC-005) and four Drift Checkpoints (DC-001, DC-002, DC-003, DC-FINAL) inherited from `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`.

**Tech Stack:** Python 3.12+ (chunkshop), pytest, PostgreSQL + pgvector (test DSN), YAML configs, Markdown docs. No new dependencies.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md` — re-read at every DC-XXX checkpoint.

---

## File Structure

**Created:**
- `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` — the contract doc (covers SC-001, SC-003, SC-005)
- `docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml` — A/B-ready single-cell ingest config (SC-002)
- `docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml` — A/B-ready single-cell ingest config (SC-002)
- `python/tests/chunkshop/test_ab_gate_emission.py` — sanity test (SC-004)

**Read-only references (DO NOT MODIFY):**
- `python/src/chunkshop/extractors/cooccurrence.py` — cooccur emission code
- `python/src/chunkshop/chunkers/consolidation.py` (esp. ~line 63) — `kind='fact'` row emission
- `python/src/chunkshop/extractors/__init__.py` — extractor registry
- `python/src/chunkshop/sinks/pg_vector.py` — how rows + metadata land
- `python/src/chunkshop/cli.py` — search behavior that consumes facts (lines 673, 724-729, 1277-1305)
- `docs/samples/bakeoff-scotus/*.yaml` — existing layout to mirror
- `docs/samples/bakeoff-ntsb/*.yaml` — existing layout to mirror

**Worktree:** Executor must create before Task 1 — `git worktree add ../chunkshop-ab-gate -b feat/ab-gate` from `main`. Per `CLAUDE.md` worktree convention. Use the `superpowers:using-git-worktrees` skill.

---

## Task 1: ⛔ DC-001 Gate — Ground-truth emission shapes from main HEAD

**Purpose:** Drift Checkpoint DC-001 from the brief: verify the shape you're about to document matches current code, not your memory of the PR description. This is a *read-only* task — no files modified.

**Files:**
- Read: `python/src/chunkshop/extractors/cooccurrence.py`
- Read: `python/src/chunkshop/chunkers/consolidation.py:50-120` (the `kind='fact'` emission site, locate the function)
- Read: `python/src/chunkshop/sinks/pg_vector.py` (search for `metadata` write + `kind` discriminator handling)
- Read: `python/src/chunkshop/extractors/result.py` (ExtractResult shape)
- Read: `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md` (re-read the brief now)

- [ ] **Step 1: Re-read the mission brief**

Read `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`. Confirm you understand the five SCs and the Out-of-Scope list.

- [ ] **Step 2: Capture cooccur emission shape**

Read `python/src/chunkshop/extractors/cooccurrence.py` end-to-end. Capture in a scratch note (not committed):
- Exact `metadata` key (currently `cooccur`).
- Exact edge object shape — fields `a`, `b`, `weight` — confirm `a < b` invariant is enforced where.
- Empty-list semantics (what gets emitted on a short doc / no co-occurrences).
- Where the `tags` list comes from and what its relation to edges is.

- [ ] **Step 3: Capture fact emission shape**

Run: `grep -nE "kind.*['\\\"]fact['\\\"]|kind=.fact." python/src/chunkshop/chunkers/consolidation.py`
Read the function around the match (current line ~63). Capture:
- Full set of fields written into the `kind='fact'` row's metadata (`subject`, `predicate`, `object`, `support_span`, `confidence`, `source_chunk_seq`, anything else).
- Value types and ranges (confidence as float 0..1? null allowed?).
- How `support_span` is structured (string? `{start, end}` dict? line range?).
- What `source_chunk_seq` points at and the integrity invariant.

- [ ] **Step 4: Capture sink-side persistence**

Read `python/src/chunkshop/sinks/pg_vector.py` — search for `kind` and for `metadata`. Capture:
- Which column the `kind` discriminator lands in (jsonb metadata or promoted column?).
- How nested `metadata['cooccur']` survives the write (jsonb storage vs flatten).
- Whether fact rows share the chunk table or land elsewhere.

- [ ] **Step 5: Capture downstream consumption signals**

Run: `grep -nE "kind.*['\\\"]fact['\\\"]|metadata_not|cooccur" python/src/chunkshop/cli.py python/src/chunkshop/search.py python/src/chunkshop/memory/reader.py`
Capture: how `chunkshop search` / `fact-search` exclude or include fact rows by default — this informs the contract's "consumer hygiene" section.

- [ ] **Step 6: Write a short SHAPE-NOTES.md scratch (uncommitted, for your own use)**

Path: anywhere outside the repo (e.g., `/tmp/ab-gate-shape-notes.md`). One section per shape with code citations (`file.py:line`). This becomes the source for Tasks 3 + 4. NOT committed — purely your working memory.

- [ ] **Step 7: Self-check DC-001 pass**

Confirm: every claim you're about to make in the contract doc can be cited to a file + line you actually read in this task. If anything is "I remember it was X" — go back and verify. Drift = death.

No commit (read-only task).

---

## Task 2: Scaffold the contract doc with section headers

**Files:**
- Create: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md`

- [ ] **Step 1: Create the file with section skeleton**

```markdown
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
| Persistence (jsonb metadata) | `python/src/chunkshop/sinks/pg_vector.py` |
| Consumer hygiene examples | `python/src/chunkshop/cli.py`, `python/src/chunkshop/search.py` |
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): scaffold chunkshop↔pg-raggraph emission contract

Skeleton with section headers + source-of-truth table. Sections 1-4 filled in
follow-up commits. Anchored to mission brief
skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md."
```

---

## Task 3: Fill §1 — Tier-1.5 fact rows

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` (§1)

**Source:** your scratch notes from Task 1, Step 3.

- [ ] **Step 1: Draft §1 with this structure**

Replace the `## 1. Tier-1.5 Fact Rows ...` placeholder with content covering, in this order:

1. **Discriminator:** how a consumer detects a fact row vs a chunk row (the `kind='fact'` key location — in jsonb metadata? promoted column?).
2. **Required fields:** every key in the metadata, with type + value range + null/empty rules. One subsection per field:
   - `subject` (type, example, normalization rules)
   - `predicate` (type, controlled vocab or free-form? case norm?)
   - `object` (type, example)
   - `support_span` (type — string excerpt? `{start,end}` dict? — example)
   - `confidence` (type, range, null semantics)
   - `source_chunk_seq` (type, what it references — the chunk's `seq` in the same table)
3. **Ordering & uniqueness:** are there dedup rules? Is `(subject, predicate, object, source_chunk_seq)` unique? Or are duplicates emitted and consumers dedupe?
4. **Default exclusion in chunkshop's own search** — cite `python/src/chunkshop/cli.py:724-729` to show consumers should expect `metadata_not: {kind: fact}` semantics by default.
5. **Cite source file:line** at the top of the section.

Every field MUST have an example value drawn from a real ingest (you can generate one in Task 9, OR use a plausible example here and update in Task 9 if needed).

- [ ] **Step 2: Self-check — is every claim citable?**

Re-scan §1. Every field/range/invariant must trace back to `python/src/chunkshop/chunkers/consolidation.py` or sink code. If you wrote "presumably X" or "I think X" — go back to Task 1's notes or re-read the source.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): document Tier-1.5 fact row emission shape

Fields, types, ranges, null semantics, and ordering rules with source citations
to chunkshop.chunkers.consolidation and pg_vector sink."
```

---

## Task 4: Fill §2 — Tier-1 cooccur edges

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` (§2)

**Source:** your scratch notes from Task 1, Step 2.

- [ ] **Step 1: Draft §2 with this structure**

1. **Location:** `metadata['cooccur']` on chunk rows (not on fact rows). Cite source.
2. **Element shape:** `{a: str, b: str, weight: <type>}`. Document weight type/range (int count? normalized float?).
3. **Ordering invariant:** `a < b` (alphabetical, case-sensitive?). Cite the enforcing line.
4. **Empty / no-edges case:** what gets emitted (empty list `[]` vs absent key vs null)?
5. **Relation to `tags`:** explain that `ExtractResult.tags` holds the raw phrases and `metadata['cooccur']` holds the pairs.
6. **Phrase normalization:** are `a` and `b` lowercased? Stemmed? Whitespace-stripped? Cite the source.
7. **Word-boundary fix (commit b2образа):** mention that substring false-positives were fixed in commit `b2affec` so consumers don't have to filter.

Include a complete example: a 2-3 element `cooccur` list as it would appear in jsonb.

- [ ] **Step 2: Self-check — is every claim citable?**

Same drill as Task 3 Step 2.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): document Tier-1 cooccur edge emission shape

Edge object shape, alphabetical ordering invariant, empty-list semantics,
phrase normalization with citations to extractors.cooccurrence."
```

---

## Task 5: Fill §4 — Required pg-raggraph-side artifacts (SC-005)

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` (§4)

- [ ] **Step 1: Draft §4 as a numbered checklist of pg-raggraph deliverables**

For each artifact: name, purpose, expected I/O signature, where it consumes chunkshop output. At minimum:

1. **`resolve_entity(text: str, ...) -> ResolvedEntity | None`** — purpose: collapse a fact endpoint (subject/object) or a cooccur node (a/b) onto a canonical node ID. Expected I/O: takes a normalized phrase + optional context, returns a node ID or null. Notes: this is the bottleneck pg-raggraph flagged (pg_trgm + vector sim + embed per call); the doc should NOT prescribe an implementation, only the signature.
2. **Retrieval-mode harness** — purpose: run the same gold query through {naive vector, graph-leg, hybrid} and emit comparable result sets. Expected I/O: takes a gold-Q file + corpus table name, emits per-question top-K result lists.
3. **A/B runner** — purpose: orchestrate harness × corpora × modes, write results to disk.
4. **Results writer** — purpose: dump a structured A/B results doc (JSON + markdown) that maps to the verdict criteria in §3.

For each: status = `[ ] not yet implemented in pg-raggraph as of 2026-05-28`. This becomes the dependency-tracking checkbox set.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): list required pg-raggraph-side artifacts (SC-005)

Names, purposes, I/O signatures for resolve_entity, retrieval-mode harness,
A/B runner, results writer. Tracked as checkboxes for cross-repo dep status."
```

---

## Task 6: ⛔ DC-003 Gate — Fill §3 verdict criteria, then self-audit numeric-not-vibe

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` (§3)

**Purpose:** SC-003 + Drift Checkpoint DC-003: every threshold must be a number with a unit. "Graph leg should be noticeably better" is rejected; ">5pp recall@10 lift on ≥60% of gold questions" is accepted.

- [ ] **Step 1: Draft §3 — verdict criteria as a numbered checklist**

Cover at minimum:

1. **Primary metric: top-K recall@10 lift** — define lift: `recall_graph - recall_naive`, in percentage points. Threshold: graph wins if `lift ≥ 5pp` on `≥60%` of gold questions across both corpora combined.
2. **Secondary metric: MRR (Mean Reciprocal Rank)** — define formula. Threshold: graph wins if `MRR_graph - MRR_naive ≥ 0.05` overall.
3. **Tertiary metric: answer-quality LLM judge** — using the existing `chunkshop.bakeoff.gold` LLM-judge pattern (cite the file). Threshold: graph wins if it produces an "acceptable" answer on `≥X%` more questions than naive (pick a concrete X, e.g., 10%).
4. **Tie-breaker rules:** if 2 of 3 metrics favor graph, graph wins. If 1 of 3, naive wins. Exact ties on a metric count as zero votes.
5. **Per-corpus breakdown:** show metrics for scotus and ntsb separately AND combined. Document an asymmetry rule (e.g., graph must win on at least one corpus to count overall).
6. **Latency tax acknowledged but not gating:** graph leg is slower per query (entity-resolve cost). Verdict is quality-only for v1; latency is a follow-up decision.
7. **A worked example:** "Given results {scotus: recall_graph=0.62 / naive=0.58, ntsb: recall_graph=0.55 / naive=0.60, MRR combined graph=0.41 / naive=0.39, LLM-judge graph wins 18/30}, did graph win?" — show the calculation step-by-step using the rules above, end with a single PASS/FAIL.

- [ ] **Step 2: DC-003 self-audit — re-read §3 and reject anything non-numeric**

Scan §3. Search for any of: "noticeably", "significantly", "much", "appreciably", "meaningfully", "clearly". If found, replace with a number + unit. If you can't, the criterion is broken — re-draft it.

Confirm: every "graph wins" rule resolves to a boolean given concrete numbers. The worked example in Step 1 should walk through this end-to-end.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): commit numeric verdict criteria for A/B gate (SC-003)

Three metrics (recall@10 lift, MRR delta, LLM-judge wins) with hard
thresholds, tie-breaker rules, per-corpus breakdown, worked example.
DC-003 audit passed: no 'noticeably/significantly' language."
```

---

## Task 7: Create A/B-ready ingest config for bakeoff-scotus

**Files:**
- Read: `docs/samples/bakeoff-scotus/bakeoff-scotus.yaml` (existing matrix config to mirror format/source-pointer)
- Read: `docs/samples/sample.yaml` (single-cell ingest example)
- Read: `python/src/chunkshop/extractors/__init__.py` (find composite/cooccurrence extractor registration)
- Read: `python/src/chunkshop/chunkers/__init__.py` (find consolidation chunker registration)
- Create: `docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml`

**Note:** The existing `bakeoff-scotus.yaml` is a matrix config for `chunkshop bakeoff`. This A/B config is a *single-cell ingest* for `chunkshop ingest` that produces ONE table containing both raw chunks AND emitted facts + cooccur metadata.

- [ ] **Step 1: Examine the existing scotus matrix config + a single-cell sample**

Look at:
- `docs/samples/bakeoff-scotus/bakeoff-scotus.yaml` (top 50 lines: source shape)
- `docs/samples/sample.yaml` (top to bottom: single-cell ingest YAML)
- Whichever `factorial-int8` cell config is closest to what we want (a single source → chunker → embedder → extractor → sink pipeline)

- [ ] **Step 2: Write the A/B config**

```yaml
# A/B-ready single-cell ingest for bakeoff-scotus.
#
# Produces ONE pgvector table containing:
#   - raw chunks (sentence-aware) for naive-vector baseline retrieval
#   - kind='fact' rows in metadata (via consolidation chunker pipeline)
#   - metadata['cooccur'] edges on chunk rows (via cooccurrence extractor)
#
# Consumed by:
#   - pg-raggraph's A/B runner — naive leg reads chunk rows, graph leg reads
#     fact rows + cooccur edges and resolves entities.
#   - chunkshop's sanity test (python/tests/chunkshop/test_ab_gate_emission.py)
#
# Usage:
#   export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
#   chunkshop ingest --config docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml

name: scotus_ab_gate

source:
  type: json_corpus
  path: /home/yonk/yonk-tools/pg-raggraph/benchmarks/age-bakeoff/src/age_bakeoff/extraction/data/scotus.json
  documents_key: documents
  id_field: id
  content_field: content
  title_field: title

chunker:
  type: hierarchy        # SCOTUS docs have headings; matches the shipped default
  max_chars: 1200

embedder:
  type: fastembed
  model: BAAI/bge-small-en-v1.5

extractor:
  type: cooccurrence
  # Tier-1 cooccur edges land in metadata['cooccur'] on chunk rows.
  # (Defaults documented in extractors/cooccurrence.py — copy explicit
  # values here once you've read that file in Task 1 / Task 7 Step 1.)

target:
  dsn_env: CHUNKSHOP_TEST_DSN
  schema: chunkshop_ab_gate
  table: scotus_ab
  mode: overwrite
  force_overwrite: true
```

- [ ] **Step 3: Tune `extractor:` block from the cooccurrence source**

Open `python/src/chunkshop/extractors/cooccurrence.py`. Find the `CooccurrenceExtractor.__init__` signature and the YAML config model in `python/src/chunkshop/config.py` (search for `cooccurrence`). For each non-default parameter, add an explicit line to the `extractor:` block above. Default values are fine — being explicit makes the A/B run reproducible across chunkshop bumps.

- [ ] **Step 4: Check whether fact emission needs a `consolidation` chunker wrapper**

Open `python/src/chunkshop/chunkers/consolidation.py`. Read the class docstring + `__init__`. The `kind='fact'` rows are emitted by the consolidation chunker (with a user-wired consolidator callable per `config.py:520`), NOT by a plain `hierarchy` chunker.

If facts only land when `chunker.type: consolidation` is used: rewrite the YAML so `chunker:` is a `consolidation` block wrapping a `hierarchy` base, with the consolidator callable wired per the chunker's docstring. Otherwise leave `chunker: hierarchy` as-is.

After this step the YAML should be the *exact* config you'll run in Task 9 — no further tuning.

- [ ] **Step 5: Validate the config loads**

Run:
```bash
cd python
uv run --no-sync chunkshop validate --config ../docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml
```
Expected: exit 0, prints "config OK" or equivalent. If `validate` subcommand doesn't exist, run `uv run --no-sync chunkshop ingest --config <path> --dry-run` instead. If neither exists, skip to Task 9 which runs the ingest for real.

- [ ] **Step 6: Commit**

```bash
git add docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml
git commit -m "config(ab-gate): add single-cell A/B-ready ingest for bakeoff-scotus

Produces raw chunks + fact rows + cooccur edges in one pgvector table for
pg-raggraph's A/B runner. SC-002 partial — ntsb config follows in next task."
```

---

## Task 8: Create A/B-ready ingest config for bakeoff-ntsb

**Files:**
- Read: `docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml`
- Read: `docs/samples/bakeoff-ntsb/README.md` (corpus layout)
- Read: `docs/samples/bakeoff-ntsb/sample-recommended-python.yaml` (likely a single-cell example for this corpus)
- Create: `docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml`

- [ ] **Step 1: Mirror the scotus A/B config, swapping in NTSB's source shape**

Read `docs/samples/bakeoff-ntsb/sample-recommended-python.yaml` (if it exists; otherwise the matrix config's `source:` block) to get the exact NTSB source pointer.

Write the YAML — same structure as Task 7's scotus-ab.yaml — but with:
- `name: ntsb_ab_gate`
- `source:` block matching NTSB's corpus path + field names
- `chunker:` same as scotus (`hierarchy` if NTSB docs have headings, else `sentence_aware`)
- `target.schema: chunkshop_ab_gate`, `target.table: ntsb_ab`
- Same embedder + extractor as scotus

Include the same header comment block as Task 7, swapping corpus name.

- [ ] **Step 2: Validate the config loads**

Same command pattern as Task 7 Step 5, with the new file path.

- [ ] **Step 3: Commit**

```bash
git add docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml
git commit -m "config(ab-gate): add single-cell A/B-ready ingest for bakeoff-ntsb

Mirrors bakeoff-scotus-ab.yaml shape against NTSB corpus. SC-002 complete."
```

---

## Task 9: Smoke-run both A/B configs against test DSN

**Files:**
- No files modified. This is a validation step.

**Prereq:** `docker compose -f docker-compose.test.yaml up -d` must be running so the test PG is reachable.

- [ ] **Step 1: Run scotus-ab ingest**

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
cd python
uv run --no-sync chunkshop ingest --config ../docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml
```
Expected: exit 0. Last lines should report doc count + chunk count.

- [ ] **Step 2: Verify the table contains both chunks and facts**

```bash
psql "$CHUNKSHOP_TEST_DSN" -c "SELECT
  COUNT(*) FILTER (WHERE metadata->>'kind' = 'fact') AS facts,
  COUNT(*) FILTER (WHERE metadata->>'kind' IS DISTINCT FROM 'fact') AS chunks,
  COUNT(*) FILTER (WHERE metadata ? 'cooccur') AS chunks_with_cooccur
FROM chunkshop_ab_gate.scotus_ab;"
```
Expected: `facts > 0`, `chunks > 0`, `chunks_with_cooccur > 0`. If `facts = 0`, the consolidation chunker pipeline isn't wired — revisit Task 7's chunker config.

- [ ] **Step 3: Spot-check shapes match the contract doc**

```bash
psql "$CHUNKSHOP_TEST_DSN" -c "SELECT metadata FROM chunkshop_ab_gate.scotus_ab WHERE metadata->>'kind' = 'fact' LIMIT 1;"
psql "$CHUNKSHOP_TEST_DSN" -c "SELECT metadata->'cooccur' FROM chunkshop_ab_gate.scotus_ab WHERE metadata ? 'cooccur' AND jsonb_array_length(metadata->'cooccur') > 0 LIMIT 1;"
```
Compare both outputs against §1 and §2 of the contract doc. **If the shape diverges from what you documented, fix the CONTRACT doc, not the emission code** (PR #33/#34 are fixed per the brief). Re-commit the contract doc patch.

- [ ] **Step 4: Update §1 / §2 of the contract doc with the real example values from Step 3**

Replace any placeholder examples written in Task 3 / Task 4 with the actual jsonb you saw in Step 3. Commit:

```bash
git add docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md
git commit -m "docs(contract): replace example values with real ingest output

From running bakeoff-scotus-ab.yaml against test DSN. Confirms documented
shape matches actual emission on main HEAD."
```

- [ ] **Step 5: Repeat Steps 1-3 for ntsb-ab**

Same commands with `bakeoff-ntsb-ab.yaml` + `chunkshop_ab_gate.ntsb_ab`. No re-commit needed unless shapes differ from scotus.

- [ ] **Step 6: Drop the test schema**

```bash
psql "$CHUNKSHOP_TEST_DSN" -c "DROP SCHEMA chunkshop_ab_gate CASCADE;"
```
Don't leave test state around.

---

## Task 10: ⛔ DC-002 Gate — Confirm no pg-raggraph-side code before writing sanity test

**Files:**
- Read: `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md` (re-read Out of Scope)

- [ ] **Step 1: Re-read the brief's Out of Scope list**

Read the "Out of Scope" section of the mission brief. Confirm you understand: `resolve_entity`, retrieval-mode harness, A/B runner, results writer, the actual A/B run — ALL live in pg-raggraph, not here.

- [ ] **Step 2: Self-check — anything you're about to write that touches those?**

The sanity test (next task) is allowed to:
- Run chunkshop ingest end-to-end
- Read back from pgvector
- Assert shapes match contract doc
- Dump a JSON summary

It is NOT allowed to:
- Implement entity resolution
- Implement a retrieval harness
- Run a graph-leg query
- Compute recall@10 or MRR
- Score answers with an LLM judge

If you're tempted to add any of the disallowed items "while we're here" — that's the drift this gate exists to catch. Stop, note it as a follow-up task for pg-raggraph, move on.

No commit (audit-only task).

---

## Task 11: Write the sanity test (SC-004)

**Files:**
- Create: `python/tests/chunkshop/test_ab_gate_emission.py`
- Reference (for patterns): `python/tests/chunkshop/test_end_to_end_samples_corpus.py` (existing E2E test that uses CHUNKSHOP_TEST_DSN)

- [ ] **Step 1: Write the test**

```python
"""Sanity test for A/B-gate emission shapes.

Runs one bakeoff corpus end-to-end via the A/B-ready ingest config and
asserts that fact rows + cooccur edges land in pgvector with the shape
documented in
``docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md``.

Dumps a JSON summary (row counts + sample fact + sample edge) under
``skill-output/ab-gate/`` so pg-raggraph day-one is "run the experiment,"
not "debug chunkshop output." Skips cleanly when ``CHUNKSHOP_TEST_DSN``
is unset.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

CHUNKSHOP_TEST_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "docs" / "samples" / "bakeoff-scotus" / "bakeoff-scotus-ab.yaml"
SUMMARY_DIR = REPO_ROOT / "skill-output" / "ab-gate"


pytestmark = pytest.mark.skipif(
    not CHUNKSHOP_TEST_DSN,
    reason="CHUNKSHOP_TEST_DSN not set — A/B-gate sanity test requires test PG",
)


@pytest.fixture(scope="module")
def ingested_schema():
    """Run the scotus-ab ingest, yield the schema name, drop on teardown."""
    from chunkshop.cli import _ingest_one_config  # internal helper; if name
    # differs, use `subprocess.run(["chunkshop", "ingest", ...])` instead

    schema = "chunkshop_ab_gate_test"
    # Override schema via env or by editing the config copy on disk for the test.
    # If the runner doesn't support schema override, the implementing engineer
    # may parameterize the config or copy it to a tmp path with the schema
    # rewritten. Either is fine — the goal is isolation from prior runs.
    os.environ["CHUNKSHOP_AB_GATE_SCHEMA_OVERRIDE"] = schema  # if supported
    _ingest_one_config(CONFIG_PATH)
    yield schema
    with psycopg.connect(CHUNKSHOP_TEST_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
        conn.commit()


def test_fact_rows_present_and_well_shaped(ingested_schema):
    """SC-004 part A: kind='fact' rows exist with the documented field set."""
    with psycopg.connect(CHUNKSHOP_TEST_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata FROM {ingested_schema}.scotus_ab "
                "WHERE metadata->>'kind' = 'fact' LIMIT 5;"
            )
            rows = cur.fetchall()

    assert rows, "No kind='fact' rows emitted — consolidation chunker pipeline broken"
    sample = rows[0][0]
    # Contract §1 required fields (update list if the doc's required-field
    # set differs after Task 9):
    for required in ("kind", "subject", "predicate", "object",
                     "support_span", "confidence", "source_chunk_seq"):
        assert required in sample, f"Fact row missing required field: {required}"
    assert sample["kind"] == "fact"


def test_cooccur_edges_present_and_well_shaped(ingested_schema):
    """SC-004 part B: chunks have metadata['cooccur'] with documented shape."""
    with psycopg.connect(CHUNKSHOP_TEST_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->'cooccur' FROM {ingested_schema}.scotus_ab "
                "WHERE metadata ? 'cooccur' "
                "AND jsonb_array_length(metadata->'cooccur') > 0 LIMIT 5;"
            )
            rows = cur.fetchall()

    assert rows, "No chunks with non-empty cooccur — cooccurrence extractor not wired"
    sample = rows[0][0]
    assert isinstance(sample, list)
    edge = sample[0]
    for required in ("a", "b", "weight"):
        assert required in edge, f"Cooccur edge missing required field: {required}"
    # Contract §2 invariant: a < b alphabetical
    assert edge["a"] < edge["b"], f"Cooccur edge violates a<b invariant: {edge}"


def test_dump_summary_for_pg_raggraph_handoff(ingested_schema):
    """SC-004 part C: write a JSON summary that pg-raggraph can sanity-check."""
    with psycopg.connect(CHUNKSHOP_TEST_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT "
                f"  COUNT(*) FILTER (WHERE metadata->>'kind' = 'fact') AS facts, "
                f"  COUNT(*) FILTER (WHERE metadata->>'kind' IS DISTINCT FROM 'fact') AS chunks, "
                f"  COUNT(*) FILTER (WHERE metadata ? 'cooccur') AS chunks_with_cooccur "
                f"FROM {ingested_schema}.scotus_ab;"
            )
            facts, chunks, chunks_with_cooccur = cur.fetchone()

            cur.execute(
                f"SELECT metadata FROM {ingested_schema}.scotus_ab "
                "WHERE metadata->>'kind' = 'fact' LIMIT 1;"
            )
            sample_fact = cur.fetchone()[0]

            cur.execute(
                f"SELECT metadata->'cooccur'->0 FROM {ingested_schema}.scotus_ab "
                "WHERE metadata ? 'cooccur' "
                "AND jsonb_array_length(metadata->'cooccur') > 0 LIMIT 1;"
            )
            sample_edge = cur.fetchone()[0]

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "corpus": "scotus",
        "schema": ingested_schema,
        "counts": {
            "facts": facts,
            "chunks": chunks,
            "chunks_with_cooccur": chunks_with_cooccur,
        },
        "sample_fact": sample_fact,
        "sample_edge": sample_edge,
    }
    out = SUMMARY_DIR / "scotus-emission-summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))

    assert facts > 0
    assert chunks > 0
    assert chunks_with_cooccur > 0
    assert out.exists()
```

- [ ] **Step 2: Run the test (skipped path)**

```bash
cd python
unset CHUNKSHOP_TEST_DSN  # force skip
uv run --no-sync pytest tests/chunkshop/test_ab_gate_emission.py -v
```
Expected: 3 tests SKIPPED (not failed), with the reason message visible.

- [ ] **Step 3: Run the test (real path)**

```bash
cd python
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
uv run --no-sync pytest tests/chunkshop/test_ab_gate_emission.py -v
```
Expected: 3 tests PASS. Output file exists at `skill-output/ab-gate/scotus-emission-summary.json` with positive counts + non-null sample fact + non-null sample edge.

If a test FAILS because the runner helper (`_ingest_one_config`) doesn't exist or has a different name: read `python/src/chunkshop/cli.py` and find the actual programmatic entry point (look near the `ingest` Click command). Adjust the fixture's import.

If the schema-override env var isn't supported: rewrite the fixture to copy the config to a tmp file, sed the schema name, point the runner at the copy. Either is fine — the goal is isolation.

- [ ] **Step 4: Run the regression check from the brief**

```bash
cd python
uv run --no-sync pytest tests/chunkshop/test_cooccurrence.py tests/chunkshop/test_fact_extractors.py -v
```
Expected: existing tests still pass — we haven't touched emission code, but the brief calls for this regression check as drift protection. If you can't find a `test_fact_extractors.py`, run any test matching `*fact*` instead.

- [ ] **Step 5: Commit**

```bash
git add python/tests/chunkshop/test_ab_gate_emission.py
git commit -m "test(ab-gate): sanity test asserts fact + cooccur emission shape (SC-004)

Runs bakeoff-scotus-ab ingest end-to-end, asserts kind='fact' rows + cooccur
edges land with documented shape, dumps JSON summary for pg-raggraph handoff.
Skips cleanly when CHUNKSHOP_TEST_DSN is unset."
```

---

## Task 12: ⛔ DC-FINAL Gate — Coverage audit, then PR

**Files:**
- Read: `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`
- Read: `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` (final pass)

- [ ] **Step 1: Re-read the mission brief one final time**

Read the brief end-to-end. For each SC-001 through SC-005, write (in your head or scratch) the file path + section that satisfies it.

| SC | Evidence (fill in actual paths) |
|---|---|
| SC-001 | `docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md` §1 + §2 |
| SC-002 | `docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml`, `docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml`, validated in Task 9 |
| SC-003 | `…emission-contract.md` §3 |
| SC-004 | `python/tests/chunkshop/test_ab_gate_emission.py` |
| SC-005 | `…emission-contract.md` §4 |

If any row is empty — STOP, go back and fill the gap before opening a PR.

- [ ] **Step 2: Scan for Out-of-Scope drift**

`git diff main...HEAD --stat` should show only:
- The contract doc (new)
- The two A/B-ready configs (new)
- The sanity test (new)
- Plan + brief if you committed them in this branch (allowed)

If you see modifications to `python/src/chunkshop/extractors/`, `python/src/chunkshop/chunkers/`, `rust/`, or anything pg-raggraph-shaped — STOP, revert, the brief forbids it.

- [ ] **Step 3: Run full Python test suite locally**

```bash
cd python
uv run --no-sync pytest -q
```
Expected: all green (or pre-existing failures unrelated to this branch — investigate if unsure).

- [ ] **Step 4: Push branch + open PR**

```bash
git push -u origin feat/ab-gate
gh pr create --title "feat(ab-gate): chunkshop↔pg-raggraph emission contract + A/B-ready configs + sanity test" --body "$(cat <<'EOF'
## Summary
- Adds emission contract doc anchoring the chunkshop↔pg-raggraph A/B experiment (Tier-1.5 facts + Tier-1 cooccur shapes, verdict criteria, required pg-raggraph-side artifacts)
- Adds two A/B-ready single-cell ingest configs (`bakeoff-scotus-ab.yaml`, `bakeoff-ntsb-ab.yaml`)
- Adds sanity test that runs scotus ingest end-to-end + dumps a JSON summary for pg-raggraph handoff

Mission brief: `skill-output/mission-brief/Mission-Brief-ab-gate-graph-vs-naive.md`
Plan: `docs/superpowers/plans/2026-05-28-ab-gate-graph-vs-naive.md`

## SC coverage
- SC-001 → contract doc §1 + §2 (fact + cooccur shapes)
- SC-002 → both A/B configs, validated against test DSN in Task 9
- SC-003 → contract doc §3 (numeric verdict criteria + worked example)
- SC-004 → `python/tests/chunkshop/test_ab_gate_emission.py`
- SC-005 → contract doc §4 (pg-raggraph artifacts checklist)

## Out of scope (per brief)
No changes to PR #33/#34 emission code. No new corpora. No pg-raggraph-side code. No Rust RM-C work.

## Test plan
- [ ] `pytest python/tests/chunkshop/test_ab_gate_emission.py -v` (with `CHUNKSHOP_TEST_DSN` set) passes
- [ ] `pytest python/tests/chunkshop/test_ab_gate_emission.py -v` (without DSN) skips cleanly
- [ ] `pytest python/tests/chunkshop/test_cooccurrence.py` still passes
- [ ] Contract doc handed to pg-raggraph maintainer; can write `resolve_entity()` signature without follow-up questions
EOF
)"
```

- [ ] **Step 5: Move plan to archive on merge (post-merge, after PR is merged)**

```bash
git mv docs/superpowers/plans/2026-05-28-ab-gate-graph-vs-naive.md archive/docs/superpowers/plans/2026-05-28-ab-gate-graph-vs-naive.md
git commit -m "chore: archive shipped A/B-gate plan"
```

Per `CLAUDE.md`: active plans live in `docs/superpowers/plans/`; completed plans move to `archive/docs/superpowers/plans/` once their feature ships.
