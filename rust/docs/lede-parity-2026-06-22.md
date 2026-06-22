# Rust ↔ Python parity: lede enrichment features (2026-06-22)

This is almost entirely a **Rust port** — the four features already exist on the
Python side; the Python code is untouched. This doc records a true
working/not-working status, then runs all four features through **both**
implementations on one identical input and diffs the output.

## How to reproduce

```bash
# Rust (downloads lede 0.5 + lede-enrich 0.1 from crates.io)
cargo run --example lede_parity --features lede \
  --manifest-path rust/chunkshop/Cargo.toml

# Python (chunkshop venv; lede installed)
python/.venv/bin/python scripts/lede_parity.py
```

Both run the same fixed input:

> "Acme Corp raised $5 million in 2023. The company hired 40 engineers and opened a Berlin office on 2024-01-15. Revenue increased 300 percent. CEO Bob Smith said growth would continue."

Versions at time of writing: Rust uses **lede 0.5.0 + lede-enrich 0.1.0**; the
Python venv had **lede 0.4.5** (couldn't be forced to 0.5.0 — uv-managed venv,
no pip). The `top_terms` output was byte-identical across the two lede versions,
so the delta didn't affect these results; it's noted as a caveat.

## Status at a glance

| Feature | Rust | Python | Parity verdict |
|---|---|---|---|
| `lede_top_terms` | ✅ works | ✅ works | **Identical** — same terms, scores, kinds, tags |
| `lede_report` | ✅ works (subset) | ✅ works (full) | **Partial by design** — shared keys identical; Rust omits the rich keys; Rust `entities` is *richer* than Python's regex backend |
| `lede_entities` (Rust) ↔ `spacy_entities` (Python) | ✅ works (gazetteer) | ✅ works (spaCy) | **Same schema, different engine** — uniform `dict`, divergent content |
| `consolidator: mode: lede` | ✅ works | ✅ works | **Matching** — identical sentence selection + confidence; SVO `""` vs `null` (minor type diff) |

Nothing is broken. Two divergences are deliberate (`lede_report` subset,
`lede_entities` engine); one was a real bug found by this comparison and fixed
(the consolidator was selecting the wrong sentences); one minor type-level
divergence remains documented below.

---

## 1. `lede_top_terms` — identical ✅

Both produce, for `n=8`:

```json
"top_terms": [
  {"kind":"word","score":1.0,"term":"acme"}, {"...":"berlin"}, {"...":"bob"},
  {"...":"ceo"}, {"...":"company"}, {"...":"continue"}, {"...":"corp"}, {"...":"engineers"}
]
tags = ["acme","berlin","bob","ceo","company","continue","corp","engineers"]
```

Byte-identical: same terms, same order, same `score` (1.0), same `kind`
(`word`), same tags. Confirms lede 0.5's byte-identical `top_terms` mirror and
that the Rust wrapper preserves it.

**Config note (fixed during this comparison):** the Rust config originally used
`top_k` + `words`/`phrases` bools; Python uses `n` + `kinds: [words, phrases]`.
Rust now matches Python, so the same YAML (`{type: lede_top_terms, n: 8}`) runs
on both.

## 2. `lede_report` — shared keys identical; Rust is a subset, but richer on `entities` ⚠️

Identical between the two:

| key | both produce |
|---|---|
| `key_facts` | the 3 fact-bearing sentences (identical) |
| `metadata.dates` | `["2023","2024-01-15"]` |
| `metadata.amounts` | `["$5"]` |
| `metadata.urls` | `[]` |

Divergences:

- **`metadata.entities`** — Python (regex backend) = `[]`; **Rust = `["Acme Corp","Berlin","CEO Bob Smith"]`**. Rust routes through `lede_enrich::metadata`, whose gazetteer fills entities; Python's `regex` backend leaves them empty (only `backend: spacy` would populate them). So on this one field Rust is *richer* than Python's default backend.
- **Keys Rust omits** (present in Python's full `readable_report().to_dict()`): `attributes`, `fact_records` (4 numeric SVO records: money/date/date/percent), `stats` (4), `spacy_facts`, `spacy_metadata`, `spacy_phrases`, `search_text`, `promotion_candidates`, `summary`. This is the documented D1 subset — lede-rs exposes the *pieces* (`key_facts`, `metadata`) but not the `readable_report` aggregator or the `fact_records`/`attributes` builders.

**Net:** neither is a strict superset. A consumer reading `lede_report.key_facts`
or `.metadata.{dates,amounts}` gets parity; one reading `.fact_records` /
`.stats` / `.attributes` gets nothing in Rust; one reading `.metadata.entities`
gets *more* in Rust. Documented gap, not a bug. (Config also aligned: Rust
`max_facts` default 10→40, added `backend`, `tag_sources` default matches Python.)

## 3. `lede_entities` (Rust) ↔ `spacy_entities` (Python) — same schema, different engine ⚠️

Python has no `lede_entities`; its nearest counterpart is `spacy_entities`.

```json
// Python spacy_entities (spaCy NER — installed and working):
"entities": {"DATE":["2023","2024-01-15"], "GPE":["Berlin"], "ORG":["Acme Corp"], "PERSON":["Bob Smith"]}

// Rust lede_entities (lede-enrich gazetteer):
"entities": {"unlabeled": ["Acme Corp","Berlin","CEO Bob Smith"]}
```

- **Schema: same** — both `dict[str, list[str]]` (the D2 goal: one code path for consumers).
- **Content: different** — Python classifies into DATE/GPE/ORG/PERSON; Rust lumps into one `unlabeled` bucket. Rust grabbed `"CEO Bob Smith"` (title+name) vs Python's cleaner `"Bob Smith"` (PERSON); Python surfaces DATE entities, Rust keeps those in `metadata.dates` instead.

This is exactly the intended D2 trade-off: deterministic, license-clean Rust NER
that's schema-compatible with Python but not content-identical. `lede_entities`
is a Rust-only capability (Python users get spaCy or nothing).

## 4. `consolidator: mode: lede` — matching (after a fix this comparison caught) ✅

Both now produce identical facts:

```json
[ {"confidence":1.0,   "support_span":"Acme Corp raised $5 million in 2023."},
  {"confidence":0.667, "support_span":"The company hired 40 engineers and opened a Berlin office on 2024-01-15."},
  {"confidence":0.333, "support_span":"CEO Bob Smith said growth would continue."} ]
```

Same three sentences, same rank-decay confidence, same support spans.

**Bug this comparison found and fixed:** the Rust consolidator initially used
`lede::extract::key_facts` (stat-bearing sentences), so it picked
*"Revenue increased 300 percent."* as its third fact. Python's
`lede_facts.extract_facts` actually uses `lede.summarize` + sentence-split, which
picks the salient *"CEO Bob Smith…"* quote instead. Rust now uses
`lede::summarize` (`max_length=500`, default mode) + the same sentence split, so
the selection matches.

**Remaining minor divergence (documented, not fixed here):** Rust's `FactTriple`
has non-`Optional` `subject`/`predicate`/`object`, so the absent SVO serializes
as `""`; Python serializes `null`. Semantically equivalent ("no triple"), but a
strict wire diff. Fixing it means making those three fields `Option<String>` on
the shared RM-A `FactTriple` — a memory-subsystem change out of scope for this
lede branch. Tracked as a follow-up.

---

## Output-quality verification (adversarial probes) — are the entities/facts *real*?

Ran the gazetteer NER + fact/amount extraction through deliberately hostile
inputs (lede 0.5.0 both sides). Reviewed with ABE (`validate`, reviewer
`gemma`). **The port is faithful — it accurately mirrors what lede-enrich
returns — but the upstream extraction has real gotchas a consumer must know
about.** This is a verification of *utility*, not just parity.

| Probe | Rust gazetteer (`lede_entities` / report) | Python spaCy | Reading |
|---|---|---|---|
| "The Company released a new Product. Our Team celebrated the Launch in Spring." | `[Company, Product, Our Team, Launch, Spring]` | `{DATE:[Spring]}` | **False-positive storm.** Gazetteer tags ~any Title-Case token + captures the possessive "Our". spaCy rejects all but Spring. |
| "Dr. Jane Doe met CEO Bob Smith and President Lincoln in Washington." | `[Jane Doe, CEO Bob Smith, Lincoln, Washington]` | `{PERSON:[Jane Doe, Bob Smith, Lincoln], GPE:[Washington]}` | **Inconsistent titles**: Dr./President stripped, "CEO" kept; no labels. |
| "We raised $5 million, spent EUR 2.3 billion, sold 1,000 units at 3.5% on 2024-01-15." | amounts `[$5]`, entities `[EUR]` | amounts `[$5]` | **Amount truncation** (`$5 million`→`$5`), shared with Python (lede core); EUR/billion/1,000/% missed; "EUR" mis-tagged as entity (Rust). |
| "the quick brown fox jumps over the lazy dog." | `[]` | (n/a) | Correct — but capitalization-dependent, so lowercase entities ("amazon") are **missed**. |
| "Acme grew. Acme grew again. Acme is Acme." | `[Acme]` | (n/a) | Correct dedup + first-appearance order. |

### What's trustworthy vs not

- ✅ **`lede_top_terms`, consolidator facts, dedup, empty-input** — real and reliable.
- ⚠️ **`entities` (gazetteer path, used by `lede_entities` AND `lede_report.metadata.entities`)** — low precision. It is effectively a **capitalized-noun-phrase extractor**, not spaCy-grade NER. Treating it as named entities feeds noise downstream.
- ⚠️ **Amounts** — lossy in *both* implementations (truncated, currency-unit-dropping). Per ABE, this is a **functional deficiency** for extraction, not a low-priority cosmetic issue — "shared with Python" doesn't make it acceptable.

### Downstream risk (ABE's headline)

The intended consumer is **pg-raggraph**, which builds a knowledge graph from chunk metadata. Feeding low-precision `entities` (e.g. "Our Team", "The Company", "Launch") into graph nodes is **destructively additive** — node explosion, a "hairball" with weak connectivity. The entity noise isn't just misleading; it degrades the graph.

### Resolved (option C) — `entities` key kept + `entities_backend` provenance marker

D2 reused the `entities` key for schema uniformity with Python's `spacy_entities`. ABE flagged that the *quality* gap is large enough that sharing the key silently is dangerous (a graph builder can't tell spaCy-grade NER from gazetteer noise). **Decision: option C.** `lede_entities` keeps the portable `entities` key **and** emits a sibling marker:

```jsonc
"entities": {"unlabeled": ["Acme Corp","Berlin","CEO Bob Smith"]},
"entities_backend": "gazetteer"
```

A consumer branches on `entities_backend`: `"gazetteer"` ⇒ low-precision, filter/down-weight before graphing; **absence ⇒ the high-precision path** (Python `spacy_entities` doesn't mark itself, so missing-marker means trust-as-NER). This preserves shared-key/YAML portability *and* gives the active misuse-defense ABE wanted, without re-opening Python. The marker is scoped to the `lede_entities` extractor (the bare `entities` key, where the collision is); `lede_report.metadata.entities` is left matching Python's report shape since the `lede_report` container is already self-identifying.

### Layer of responsibility

The chunkshop Rust wrapper is correct: it faithfully passes through lede-enrich's output. The precision/truncation issues live in **lede-enrich / lede core**, not the port — so the fixes are (a) upstream tickets and (b) honest docs here, **not** patching NLP heuristics in chunkshop's wrapper. Upstream tickets filed: see lede repo (gazetteer precision; amount truncation; stats/fact_records exposure).

### Caveat on coverage

Probes were English short texts. Long documents, non-English, and source-code inputs were not exercised — entity/amount behavior there is unverified.

## Summary

- **Works in both, identical:** `lede_top_terms`, `consolidator: mode: lede`.
- **Works in both, deliberately different:** `lede_report` (Rust subset + richer entities), `lede_entities`/`spacy_entities` (gazetteer vs spaCy, same schema).
- **Fixed by this comparison:** consolidator sentence selection (`key_facts` → `summarize`); three config-schema mismatches (`n`/`kinds`, `max_facts` default, `backend`) that would have broken shared-YAML portability.
- **Open follow-ups:** `FactTriple` SVO `""` vs `null`; optionally enrich Rust `lede_report` with `stats`/`fact_records` if a consumer needs them.
