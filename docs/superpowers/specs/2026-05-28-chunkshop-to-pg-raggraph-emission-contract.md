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

**Upstream-stage keys that ALSO land on fact rows** (verified empirically against the Task 9 ingest of `bakeoff-scotus-ab.yaml`, 2014/2014 fact rows carry each):

| Field | Type | Source line | Notes |
|---|---|---|---|
| `framer` | `str` | `framers/identity.py:12` (and every framer in `framers/`) | Name of the framer stage that produced the source `Document` — defaults to `"identity"` when no framer is configured. Lands on every chunk via `doc.metadata` (`runner.py:136-138`). |
| `frame_seq` | `int` | `framers/identity.py:13` | 0-indexed position within the raw doc the framer produced this frame from. `0` for the identity (1-to-1 pass-through) case. |
| `cooccur` | `list[dict]` | `extractors/cooccurrence.py:50-78` (full schema in §2) | When the cell wires `extractor: cooccurrence`, the runner calls `extractor.extract(c.original_content)` on EVERY chunk including facts (`runner.py:128`). Fact-row cooccur edges are derived from the fact's `support_span`, not from the parent episode — they're rarely useful for graph construction (a single sentence rarely has enough co-occurring keyphrases), but the key is present and consumers shouldn't choke on it. See §2.1. |

Additionally, any keys set by the framer stage upstream of the chunker (e.g. `regex_boundary`, `jsonpath`, `session_episode`, `heading_boundary` framers all add their own metadata) flow through `doc.metadata` and land on every chunk.

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

pg-raggraph's graph leg should ingest fact rows by filtering `metadata->>'kind' = 'fact'`, and the prose-level cooccur edges by reading `metadata->'cooccur'` from `metadata->>'kind' = 'episode'` rows. Fact rows ALSO carry a `cooccur` key (per §1.2 / §2.1) but those edges are derived from a single sentence and are not the corpus-level graph signal — filter to episodes.

### 1.7 Example fact row

Real fact-row metadata captured from the Task 9 ingest of `bakeoff-scotus-ab.yaml` (the `cooccur` list is omitted here for readability — see §2.10 for its shape):

```json
{
  "kind": "fact",
  "framer": "identity",
  "frame_seq": 0,
  "subject": "Devries",
  "predicate": "be",
  "object": "widows",
  "support_span": "Devries and Shirley McAfee are the widows of two US Navy sailors whom they allege developed cancer after they were exposed to asbestos working on Navy ships and in a naval shipyard.",
  "confidence": 1.0,
  "truncated": false,
  "source_chunk_seq": 0,
  "consolidator": "lede_spacy",
  "extractor": "lede_spacy",
  "doc_type": "case_overview",
  "author_id": "j-kavanaugh",
  "project_id": "case-2018_air-and-liquid-systems-corp-v-devries"
}
```

The `doc_type` / `author_id` / `project_id` keys come from the source JSON corpus's per-document metadata and flow through `_strip_transient` (`consolidation.py:37`) — they will differ for every corpus. The `framer` / `frame_seq` / `kind` / `consolidator` / `extractor` / SPO / `support_span` / `confidence` / `truncated` / `source_chunk_seq` keys are the chunkshop-emitted invariants documented in §1.2.

## 2. Tier-1 Cooccur Edges (`metadata['cooccur']`)

**Source files (on `main` HEAD commit `ef2aceb`):**
- Emission: `python/src/chunkshop/extractors/cooccurrence.py` (full file, esp. lines 50-78)
- Config: `python/src/chunkshop/config.py:791-800` (`CooccurrenceExtractor`)
- Persistence: `python/src/chunkshop/sinks/pg.py:444`

### 2.1 Location

Cooccur edges live in `metadata['cooccur']` on **every chunk row** the runner emits — verified empirically against the Task 9 ingest (2786/2786 rows in `scotus_ab` and 80/80 rows in `ntsb_ab` carry the key, including all fact rows). The runner calls `extractor.extract(c.original_content)` on every chunk regardless of `kind` (`runner.py:128`); fact rows therefore get cooccur edges derived from their `support_span`, not from the parent episode's text. The key is always present when the `cooccurrence` extractor is wired into the cell; empty text and no-pair text both produce `[]` rather than a missing key (`cooccurrence.py:50-52, 71-78`).

**Consumers building a corpus-level graph from cooccur should filter to episode rows** (`WHERE metadata->>'kind' = 'episode'`). The fact-row cooccur edges are derived from a single sentence's keyphrases and are not the corpus-level signal the extractor is designed to produce — they exist as a uniformity artifact of the extractor pipeline, not as a designed graph signal.

### 2.2 Edge shape

```json
{"a": "string", "b": "string", "weight": <int>}
```

| Field | Type | Notes |
|---|---|---|
| `a` | `str` | First node of the pair. **`a < b` lexicographic** (case-sensitive on stored bytes). |
| `b` | `str` | Second node of the pair. |
| `weight` | `int` | Co-occurrence count: number of salient sentences in the chunk that contain BOTH `a` and `b` (`cooccurrence.py:63-69`). |

### 2.3 Ordering invariants

- **Per-edge:** `a < b` lexicographic. Enforced at `cooccurrence.py:66-69` via a sorted set + `i < j` iteration. Consumers can rely on this — they do NOT need to call `sorted([a, b])` when keying edges.
- **List-level:** edges are sorted by `(-weight, a, b)` at `cooccurrence.py:77` — strongest pairs first, ties broken alphabetically. Consumers reading the top-N most-coherent pairs can read from the front of the list.

### 2.4 Empty / null semantics

- Empty input text → `metadata['cooccur'] = []` (`cooccurrence.py:51-52`).
- Non-empty text with no pairs meeting `min_pair_count` → `metadata['cooccur'] = []` (`cooccurrence.py:71-75`).
- **The key is always present** when the extractor runs. Treat "key missing" only as "the cell wasn't configured with the cooccurrence extractor."

### 2.5 Phrase normalization

| Surface | Normalization |
|---|---|
| Internal matching | Lowercase + word-boundary-escaped regex (`cooccurrence.py:60-62, 65`). |
| **Stored `a`, `b`, `tags` strings** | **Raw rake-nltk output** — chunkshop does NOT explicitly lowercase or strip them (`cooccurrence.py:39-43, 54, 78`). In practice they ARE lowercase because `rake_nltk.Rake.get_ranked_phrases()` lowercases internally, but this is a rake-nltk property, not a chunkshop guarantee. If the RAKE provider is swapped out in the future, casing may change. |

Consumers that need a case-insensitive entity match should `.lower()` defensively.

### 2.6 Word-boundary correctness (PR #34 fix)

Commit `b2288f7` ("fix(cooccurrence): word-boundary phrase matching to cut substring false-positive edges") wraps the phrase pattern with `\b` anchors at `cooccurrence.py:60-62`. This eliminates substring false-positives like `("data", "database")` or `("test", "testing")` — the comment at `cooccurrence.py:57-59` calls this out explicitly. Consumers can rely on stored edges being whole-phrase matches.

### 2.7 `tags` vs `metadata['cooccur']` relation

| | What it holds | Where |
|---|---|---|
| `tags` | The node set (all keyphrases for the chunk) | `ExtractResult.tags` (`cooccurrence.py:54, 78`) |
| `metadata['cooccur']` | The edge set (filtered by `min_pair_count`) | `ExtractResult.metadata['cooccur']` |

Both are derived from the same RAKE keyphrase list (`cooccurrence.py:54`). pg-raggraph's graph leg should treat `tags` as the candidate node set and `metadata['cooccur']` as the candidate edges.

### 2.8 Default knobs

Defaults from `config.py:791-800` (`CooccurrenceExtractor`):

| Knob | Default | Effect |
|---|---|---|
| `top_k` | `15` | Max keyphrases per chunk. At default, max edges per chunk ≈ `C(15, 2) = 105`. Dense corpora can land a lot of jsonb. |
| `min_chars` | `3` | Drops phrases shorter than 3 chars before the matchers are built (`cooccurrence.py:43`). |
| `max_summary_chars` | `1000` | Lede summary budget that defines the "salient sentence" window (`cooccurrence.py:47`). |
| `min_pair_count` | `1` | Keep every co-occurring pair. Increase to filter weak edges (`cooccurrence.py:74`). |

### 2.9 Persistence

The full `metadata` dict — including nested `cooccur` list — is serialized via `self.backend.json_literal(c.metadata)` and cast `::jsonb` at write time (`pg.py:444`). The list-of-dicts shape survives intact. Query with standard jsonb operators:

```sql
-- Top-5 strongest edges across the whole corpus
SELECT metadata->'cooccur'->edge_idx
FROM chunkshop_ab_gate.scotus_ab,
     LATERAL jsonb_array_length(metadata->'cooccur') AS n,
     LATERAL generate_series(0, LEAST(n-1, 4)) AS edge_idx
WHERE jsonb_array_length(metadata->'cooccur') > 0
ORDER BY (metadata->'cooccur'->edge_idx->>'weight')::int DESC
LIMIT 5;
```

### 2.10 Example

Real cooccur list captured from the Task 9 ingest of `bakeoff-scotus-ab.yaml` (first 3 edges from an episode chunk where the leading edge has `weight=2` — illustrates the `(-weight, a, b)` ordering as well as the `a < b` per-edge invariant):

```json
"cooccur": [
  {"a": "excessive force", "b": "ninth circuit", "weight": 2},
  {"a": "clearly established", "b": "considered whether clearly established law prohibited", "weight": 1},
  {"a": "clearly established", "b": "excessive force", "weight": 1}
]
```

Note the leading edge has `weight=2` (single edge in the chunk with that weight); the next edges all have `weight=1` and are alphabetized by `(a, b)`.

## 3. Verdict Criteria — "Did Graph Beat Naive?"

This section defines the quantitative criteria the A/B experiment is judged against. **Every threshold is a number with a unit.** "Graph wins" is a deterministic function of three metrics, not a vibe.

### 3.1 The three metrics

| # | Metric | Definition | Range |
|---|---|---|---|
| 1 | **Recall@10 lift** | `recall_graph - recall_naive` (in percentage points), where `recall = (# gold-Q with gold_doc_id in top 10 hits) / (total gold-Q)` | -100pp .. +100pp |
| 2 | **MRR delta** | `MRR_graph - MRR_naive`, where `MRR = mean(1 / rank_of_gold_doc_id)` (0 if gold not in top-K cutoff) | -1.0 .. +1.0 |
| 3 | **LLM-judge win-rate delta** | Fraction of gold-Q for which the LLM judge rates the graph-leg answer "acceptable" minus the same for naive. Use chunkshop's existing judge configuration at `python/src/chunkshop/eval/config.py` (`JudgingConfig` / `JudgeProvider`, wired through `python/src/chunkshop/eval/planner.py::_llm_judge_config`) to keep results comparable to the factorial bakeoff. | -1.0 .. +1.0 |

### 3.2 Per-metric thresholds (graph wins this metric if …)

| Metric | Graph wins if … | Tie if … | Naive wins if … |
|---|---|---|---|
| Recall@10 lift | `≥ +5pp` overall | `-5pp < lift < +5pp` | `≤ -5pp` overall |
| MRR delta | `≥ +0.05` overall | `-0.05 < delta < +0.05` | `≤ -0.05` overall |
| LLM-judge win-rate delta | `≥ +0.10` (i.e. graph wins on 10pp more gold-Q than naive) | `-0.10 < delta < +0.10` | `≤ -0.10` overall |

### 3.3 Overall verdict (combining the three)

- **GRAPH WINS** if graph wins ≥ 2 of 3 metrics AND naive wins 0.
- **NAIVE WINS** if naive wins ≥ 2 of 3 metrics AND graph wins 0.
- **INCONCLUSIVE** in all other cases (1-1 with a tie, 1-2, any 0-0-3 all-ties).

Ties on a metric count as 0 votes for both sides. An exact tie on all three → INCONCLUSIVE.

### 3.4 Per-corpus breakdown rule

Compute all three metrics per corpus (`bakeoff-scotus`, `bakeoff-ntsb`) AND combined (questions concatenated, simple union for recall/MRR; concatenated judge tally for the LLM metric). Report all three rollups.

**Asymmetry rule:** to declare GRAPH WINS overall, graph must win the combined rollup AND must NOT lose all three metrics on either single corpus. (If graph wins the combined rollup but loses all three metrics on `bakeoff-ntsb`, that's INCONCLUSIVE — the win is corpus-specific, not general.)

### 3.5 Tie-breaker priority

If the 2-of-3 rule produces a tie (e.g., graph wins recall+LLM, naive wins MRR, but you want a single label), apply this priority:

1. Recall@10 lift (most directly measures retrieval quality)
2. MRR delta (rewards ranking, not just inclusion)
3. LLM-judge win-rate (most subjective, lowest weight)

This priority is for tie-breaks only — the 2-of-3 rule in §3.3 is the primary decision.

### 3.6 Latency is acknowledged, not gating

The graph leg is slower per query (entity-resolve cost). For v1 of this gate, the verdict is **quality-only**. If graph wins quality but is >10× slower, that's a follow-up decision (caching, pre-resolution, indexing strategies) — it does NOT change the v1 verdict. Latency numbers should still be reported alongside the verdict for context.

### 3.7 Worked example

Given hypothetical results:

| Metric | scotus graph | scotus naive | ntsb graph | ntsb naive | combined graph | combined naive | delta |
|---|---|---|---|---|---|---|---|
| Recall@10 | 0.68 | 0.58 | 0.55 | 0.60 | 0.62 | 0.59 | **+3pp** |
| MRR | 0.45 | 0.39 | 0.36 | 0.40 | 0.41 | 0.39 | **+0.02** |
| LLM-judge wins (of 30 combined Q) | 18 | 12 | — | — | 0.60 | 0.40 | **+0.20** |

Apply §3.2:
- Recall@10 lift `+3pp` → between `-5pp` and `+5pp` → **TIE**.
- MRR delta `+0.02` → between `-0.05` and `+0.05` → **TIE**.
- LLM-judge delta `+0.20` → `≥ +0.10` → **GRAPH WINS**.

Apply §3.3: graph wins 1 metric, naive wins 0, two ties. Graph does NOT meet the "≥2 of 3" threshold → **INCONCLUSIVE**.

Apply §3.4 asymmetry check: irrelevant since the combined rollup is already inconclusive.

**Verdict: INCONCLUSIVE.** The LLM judge favors graph, but recall and MRR are too close to call. Recommendation in this case: re-run with a larger gold-Q set or investigate why scotus and ntsb point in opposite directions on recall.

### 3.8 What "INCONCLUSIVE" means for chunkshop's roadmap

- GRAPH WINS → build Tier-2 LLM-validate; greenlight Rust RM-C code-aware port.
- NAIVE WINS → freeze edge-tier work; deprioritize Rust RM-C consumers; reconsider whether the existing facts/cooccur are worth maintaining.
- INCONCLUSIVE → fix the corpus / question coverage and re-run. Do NOT ship more edge tiers on inconclusive evidence.

## 4. Required pg-raggraph-Side Artifacts

This section lists every pg-raggraph deliverable required for the A/B experiment to run. It exists so chunkshop maintainers can track cross-repo dependency status from this doc, and so pg-raggraph maintainers have a single concrete checklist instead of having to derive what's needed from chunkshop's source.

**Status legend:** `[ ]` not yet started · `[~]` in progress · `[x]` complete.

### 4.1 `resolve_entity()` — the entity-resolution primitive

- **Status:** `[x]` (shipped 2026-05-28, pg-raggraph v0.5.0a5 — `resolve_entity_lookup` in `pg_raggraph.resolution`)
- **Purpose:** Collapse a fact endpoint (`subject` / `object`) or a cooccur node (`a` / `b`) onto a canonical node ID so multiple surface strings ("Apple", "Apple Inc.", "apple inc") resolve to the same graph node.
- **Expected signature** (illustrative — pg-raggraph picks the exact shape):
  ```python
  def resolve_entity(
      surface: str,
      *,
      corpus_id: str,
      kind: Literal["fact_endpoint", "cooccur_node"] | None = None,
      ctx: dict | None = None,
  ) -> ResolvedEntity | None
  ```
- **Input contract (from chunkshop's side):** `surface` strings come from `metadata->>'subject'` / `metadata->>'object'` on fact rows, OR from `metadata->'cooccur'->idx->>'a'` / `>>'b'` on prose chunks. They are NOT guaranteed normalized — see §1.2 and §2.5 for casing caveats.
- **Output contract:** a canonical node identifier (string / UUID / row id — pg-raggraph's choice) or `None` for unresolved.
- **Notes:** This is the bottleneck pg-raggraph flagged (`pg_trgm` + vector similarity + embedding lookup per call). The contract does NOT prescribe an implementation strategy — only the signature. Caching / pre-resolution is encouraged.

### 4.2 Retrieval-mode harness

- **Status:** `[x]` (shipped 2026-05-28 — `pg_raggraph.ab_gate.harness.run_harness_mode`; `naive_vector` + `graph_leg` live, `hybrid` deferred)
- **Purpose:** Run the same gold question through multiple retrieval modes against the same corpus, emit comparable result sets so the A/B verdict (§3) can be computed.
- **Required modes:**
  - `naive_vector` — ANN over `embedding` column, excluding fact rows (`metadata->>'kind' IS DISTINCT FROM 'fact'`). The naive baseline.
  - `graph_leg` — entity-resolve the question, walk fact triples + cooccur edges, return chunk hits weighted by graph proximity.
  - `hybrid` (optional but recommended) — combine the two.
- **Expected I/O:**
  - Input: a gold-Q file (chunkshop's `gold-scotus.yaml` / `gold-ntsb.yaml` format) + a corpus table name.
  - Output: per-question top-K result lists, structured so per-mode metrics can be computed independently and combined.
- **Constraint:** the harness MUST query the same row set chunkshop produced — no re-indexing, no shape transformation. If pg-raggraph wants a different shape, that's a contract amendment (see §5).

### 4.3 A/B runner

- **Status:** `[x]` (shipped 2026-05-28 — `pg_raggraph.ab_gate.runner.run_ab_matrix` + `pgrg ab-gate run`)
- **Purpose:** Orchestrate `{harness × corpus × mode}` matrix runs, write structured results to disk for the results writer to consume.
- **Required corpora (chunkshop-provided):**
  - `bakeoff-scotus` — legal prose, 12 gold questions (`docs/samples/bakeoff-scotus/gold-scotus.yaml`). Ingest config: `docs/samples/bakeoff-scotus/bakeoff-scotus-ab.yaml` (added in this PR).
  - `bakeoff-ntsb` — NTSB accident reports, gold questions in `docs/samples/bakeoff-ntsb/gold-ntsb.yaml`. Ingest config: `docs/samples/bakeoff-ntsb/bakeoff-ntsb-ab.yaml` (added in this PR).
- **Expected output:** raw per-question per-mode hit lists with scores and timings.

### 4.4 Results writer

- **Status:** `[x]` (shipped 2026-05-28 — `pg_raggraph.ab_gate.writer.compute_verdict` + `pgrg ab-gate verdict`; judge runtime = `llm-judge`)
- **Purpose:** Apply the verdict criteria (§3) to the A/B runner's raw output and emit a structured results doc (JSON + Markdown summary) that resolves the gate to a single PASS / FAIL.
- **Expected output:** per-metric per-corpus tables, the combined verdict, and the §3.7 worked-example-style step-by-step calculation showing how the verdict was reached.

### 4.5 Dependency tracking

Update the `[ ]` / `[~]` / `[x]` markers as pg-raggraph ships each piece. Chunkshop maintainers can grep this section to see what's still blocking the gate from the chunkshop side:

```bash
grep -A1 "^### 4\." docs/superpowers/specs/2026-05-28-chunkshop-to-pg-raggraph-emission-contract.md | grep "Status:"
```

When all four artifacts land `[x]`, the A/B experiment is ready to run. The verdict it produces is what determines whether more edge tiers (Tier-2 LLM-validate, future RM-C Rust port consumers) are worth building.

### 4.6 Verdict (run 2026-05-28) — ⚠️ PROVISIONAL, `hybrid` mode NOT tested

The four artifacts are `[x]` and the experiment ran end-to-end. **But it tested
only 2 of the 3 modes §4.2 defines** — `naive_vector` and `graph_leg`. The
third, `hybrid` (vector seeds candidates, graph expands/reranks), is the
production-shaped mode this emission was designed to feed, and it was **not
run** (it is `NotImplementedError` in pg-raggraph's harness). So this verdict
answers a narrower question than §3's gate.

**Provisional verdict: NAIVE WINS vs `graph_leg` (graph-as-primary).** This is
NOT the same as "naive beats graph" — the mode where graph was expected to help
was never measured.

| Combined metric | naive | `graph_leg` | Δ | §3.2 label |
|---|---:|---:|---:|---|
| Recall@10 | 0.875 | 0.125 | −75.0pp | NAIVE WINS |
| MRR | 0.623 | 0.088 | −0.535 | NAIVE WINS |
| LLM-judge win-rate | 0.917 | 0.250 | −0.667 | NAIVE WINS |

(Numbers use pg-raggraph's improved query-term encoder, which lifted `graph_leg`
SCOTUS coverage 5/12 → 9/12; an earlier run reported −83.3pp. Both predate any
`hybrid` test.) Latency (§3.6): naive 51 ms p50, `graph_leg` 105 ms.

**Why `graph_leg` losing does NOT settle the gate:** `graph_leg` must
entity-resolve the *question* to seed its walk, so it fails by construction on
queries with weak NER (NTSB's descriptive keyword questions; ~3/12 SCOTUS even
after the encoder fix). `hybrid` has the opposite failure profile — the vector
leg seeds the candidate set and the graph never entity-resolves the question,
only the retrieved chunks. A large part of the −75pp gap is an artifact of
running graph in its worst-fit mode.

**§3.8 is therefore NOT triggered.** Do **not** freeze edge-tier work, drop
RM-C consumers, or abandon facts/cooccur on this evidence — the deciding mode
hasn't been measured. Resolution is gated on pg-raggraph implementing + A/B
testing `hybrid` (tracked: pg-raggraph issue + PR yonk-labs/pg-raggraph#54).
Full report + repro: pg-raggraph `benchmarks/ab-gate/RESULTS.md`.

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
