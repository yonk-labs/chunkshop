# Rust lede / lede-enrich integration — design

**Date:** 2026-06-22
**Branch:** `feat/rust-lede-enrich`
**Tracks:** chunkshop#76 (umbrella Rust-parity catch-up) — *Foundation* step + the lede-dependent slice of *Tier 1* (enrichment / fact layer).
**Status:** approved (design-approval delegated by the user to host + external second opinion; ABE `validate` consulted — see Decisions).

## Context

`chunkshop-rs` is version-locked to the Python `chunkshop` package but functionally behind: every release since ~v0.6.0 bumped the Rust crate version in lockstep with **no functional change** (chunkshop#76). The gap is concentrated in the enrichment / fact / code-intel / search layers — not the pipeline spine, which reached parity through RM-A/RM-B/RM-C.

Today the Rust crate touches `lede` only through `lede::summarize(text, max_len, mode)` in `summarizer.rs` (callable-summarizer slot), against `lede = "0.3"` from crates.io. The lede repo has since shipped **`lede` 0.5.0** (now with a byte-identical Rust `extract::top_terms`, `key_facts`, `correlate_facts`, `extract::metadata`) and a new companion crate **`lede-enrich` 0.1.0** (deterministic gazetteer NER + entity-attributed facts — the license-clean replacement for Python's spaCy path). This unlocks Rust parity for the lede-dependent Tier-1 enrichment features.

## Scope

This slice delivers, behind the existing optional `lede` cargo feature:

1. **Dependency bump** — `lede` 0.3 → 0.5 and add `lede-enrich` 0.1 (path deps; see D3).
2. **`lede_top_terms`** extractor — top-N salient words/phrases with scores.
3. **`lede_report`** extractor — assembled fact/metadata report (subset; see D1).
4. **`lede_entities`** extractor — deterministic NER via lede-enrich (see D2).
5. **`consolidator: { mode: lede }`** — salient-sentence propositions with rank-decay confidence.

Each lands as: one `ExtractorConfig`/`ConsolidatorConfig` enum variant + config struct, one dispatch arm in `build_extractor`/`build_consolidator`, one impl, and unit tests. This mirrors the documented "one new file + one new branch + one new model" extension pattern.

### Out of scope (this slice)

Tier 2 (codeparse languages), Tier 3 (search surface), Tier 4 (incremental `files`, symbol_aware hardening, openai embedder) — separate follow-ups under #76. Also out: `keybert_phrases`, `spacy_entities`, `lede_spacy` consolidator, `sumy`, `orchestrate` — Python-only by design (#76), they keep their current "Python-only" error stubs in Rust.

## Decisions (the three judgment calls)

External second opinion (ABE `validate`, reviewer `gemma`) was consulted on D1–D3. D1 and D3 affirmed; **D2 was revised** on its recommendation (schema uniformity over a split-brain key).

### D1 — `lede_report` is a documented subset, not a 1:1 port

lede-rs 0.5 does **not** expose Python's `readable_report().to_dict()` aggregator, nor `attributes` or SVO `fact_records` builders (verified: empty grep for `readable_report`/`to_dict`/`fact_records`/`attributes` in `lede/rust/src`). It exposes the pieces only: `key_facts()`, `extract::metadata()`, `correlate_facts()`.

**Decision:** assemble the report chunkshop-side, emitting **only** the keys lede-rs can produce, matching Python's nested shape exactly for those keys, and omitting the rest:

```jsonc
// metadata key: "lede_report"
{
  "key_facts": ["...", "..."],                 // lede::extract::key_facts(text, max_facts)
  "metadata": {                                // lede_enrich::metadata(text)
    "dates":    ["..."],
    "amounts":  ["..."],
    "urls":     ["..."],
    "entities": ["..."]                        // list[str] — matches Python's report.metadata.entities shape
  }
}
```

Omitted vs Python: `attributes`, SVO `fact_records`, `spacy_metadata`, `spacy_phrases`, `search_text`, `promotion_candidates`. A cross-impl consumer reading `lede_report.key_facts` or `lede_report.metadata.dates` gets parity; one reading `lede_report.attributes` gets a missing key (not wrong data). Rationale: parity is about *schema* (which keys exist and their type), not value *completeness*; fabricating SVO triples or deferring a feature the user asked for are both worse. **Note:** `lede_report` uses `lede_enrich::metadata()` (not `lede::extract::metadata()`) precisely because the latter's `entities` is always empty in pure Rust — lede-enrich's gazetteer fills it.

### D2 — `lede_entities` reuses the `entities` key as a uniform dict (revised after second opinion)

lede-enrich's `extract_entities()` returns an **unlabeled** `Vec<String>`. Python's `spacy_entities` writes `entities` as a **labeled** `dict[str, list[str]]` (`{ORG: [...], PERSON: [...]}`).

**Decision:** a **new, distinct config type `lede_entities`** (opt-in; honest that the engine differs from spaCy), whose output writes the **shared `entities` metadata key** as a uniform dict with a single bucket:

```jsonc
// config: { type: lede_entities }   ->   metadata key: "entities"
{ "unlabeled": ["Acme Corp", "Bob Smith", "Berlin"] }   // dict[str, list[str]], schema-uniform with Python
```

Why not a flat-list `lede_entities` key (host's first instinct): the reviewer's "split-brain schema" argument — a separate flat-list key forces every downstream consumer to branch on two shapes/locations. Keeping `entities` a `dict[str, list[str]]` means one code path (`for label, mentions in entities.items()`), matching Python's *type* while diverging only on *content* (one `unlabeled` bucket vs spaCy labels) — the same documented content-divergence precedent as `rake_keywords`/`lang_detect`. Query-by-specific-label (`entities["PERSON"]`) degrades on the Rust path; that is inherent to label-free NER and is documented, not designed around. The config type stays `lede_entities` (not a silent `spacy_entities` swap) so users explicitly opt into the weaker engine.

### D3 — version+path hybrid deps, feature-gated

`lede` 0.5.0 and `lede-enrich` 0.1.0 are **unpublished** (crates.io serves only `lede` 0.3.0; Cargo.lock confirms the current dep resolves there). The 0.5 API is only available locally.

**Decision:** version+path hybrid, both optional, folded into the existing `lede` feature:

```toml
lede        = { version = "0.5", path = "../../../lede/rust",        optional = true }
lede-enrich = { version = "0.1", path = "../../../lede/lede-enrich", optional = true }
# feature: lede = ["dep:lede", "dep:lede-enrich"]
```

This is exactly the pattern `lede-enrich` itself uses to consume `lede` (`version` for an eventual crates.io publish, `path` for local dev). The relative path resolves identically from the main checkout and any `yonk-tools/`-sibling worktree.

**Consequence (corrected after empirical verification — this is a MERGE BLOCKER, not just a feature cost):** a `path` dependency must exist at **resolution** time even when it is `optional` and its feature is **off** — Cargo reads every path-dep's `Cargo.toml` to build the resolve graph. `--locked` does not change this. Verified: with the sibling `lede` repo moved aside, `cargo test --lib --locked` (default features, no `lede`) fails with `failed to read .../lede/rust/Cargo.toml`. The original `lede = "0.3"` did **not** have this problem because 0.3.0 is a *published* registry crate, resolvable from the index with no local checkout.

Therefore this branch, as committed, **breaks the default CI build** (`release.yml` checks out only `chunkshop`, not the sibling). `cargo package` happens to pass (its verify build uses the version-stripped manifest with default features), but `cargo test --workspace --lib --locked` and `cargo publish` do not.

**There is no committable dependency form that both compiles the `--features lede` code and passes sibling-less CI until lede 0.5 + lede-enrich 0.1 are published to crates.io.** Path form requires the sibling; version-only form (`lede = "0.5"`) fails to resolve against a registry that only has 0.3.0. The path form is correct for **local development** (a developer with the sibling gets a green `--features lede` build + 412 passing tests), and is what this branch carries so the work is reviewable and testable.

**Remediation before merge:** publish `lede` 0.5 + `lede-enrich` 0.1 to crates.io, then switch the deps to **version-only** (drop `path`): `lede = { version = "0.5", optional = true }`, `lede-enrich = { version = "0.1", optional = true }`. That restores the pre-existing CI/release behavior (registry resolution, no sibling needed, publishable). Until then the branch stays a draft.

## Per-feature contracts

All extractors return `ExtractResult { tags: Vec<String>, metadata: serde_json::Map<String, Value> }`. lede/lede-enrich types carry **no serde derives**, so each impl hand-builds `serde_json::Value` (no derive shortcut; no newtype wrappers). Config enums: `ExtractorConfig` is `#[serde(tag = "type", rename_all = "snake_case")]`, `ConsolidatorConfig` is `#[serde(tag = "mode", rename_all = "snake_case")]` — so the variant name maps straight to the literal.

| Feature | Config | lede call | Output |
|---|---|---|---|
| `lede_top_terms` | `type: lede_top_terms` (`top_k`, `words`, `phrases`) | `lede::extract::top_terms_scored(text, &TopTermsOptions)` → `Vec<TermScore{term,score,kind}>` | metadata `top_terms` = `[{term,score,kind}]`; tags = `[term]` |
| `lede_report` | `type: lede_report` (`max_facts`, `tag_sources`) | `lede::extract::key_facts` + `lede_enrich::metadata` | metadata `lede_report` = subset dict (D1); tags from producible `tag_sources` (default: `key_facts,dates,amounts,entities`) |
| `lede_entities` | `type: lede_entities` | `lede_enrich::extract_entities(text)` → `Vec<String>` | metadata `entities` = `{"unlabeled": [...]}` (D2); tags = `[]` |
| consolidator `lede` | `mode: lede` (`max_facts`, `confidence_floor`) | `lede::extract::key_facts(text, max_facts)` | `ConsolidationOutput { summary: "", facts }` |

**Consolidator `lede` mapping.** Rust `FactTriple { subject: String, predicate: String, object: String, support_span: Option<String>, confidence: Option<f64> }`. The lede (non-spaCy) path produces salient *sentences*, not SVO triples, so per fact `i` of `n` (insertion order from `key_facts`):

- `subject` / `predicate` / `object` = `""` (Rust fields are non-Optional; the Python lede path leaves these `None`)
- `support_span` = `Some(sentence)`
- `confidence` = `Some(round(1.0 - i/n, 3))` — rank-decay, matching Python `lede_facts`

Facts with `confidence < confidence_floor` are dropped (write-time floor, default `0.0`). `summary` defaults to `""` (Python's lede consolidator only fills it when an optional summarizer slot is configured; wiring that slot is a deferred nicety, not in this slice).

## Feature gating

Follow the existing `summarizer.rs` lede pattern: struct + impl blocks under `#[cfg(feature = "lede")]`; when the feature is **off**, the four config variants must still parse (they live in the always-compiled config enums) but `build_extractor`/`build_consolidator` return an actionable error (`"… is gated behind the `lede` cargo feature; build with --features lede or run on Python"`) — same shape as today's `keybert_phrases`/`spacy_entities` Python-only errors. This keeps a config-load of a lede YAML from panicking on a default build and gives a clear remedy.

## Testing strategy

TDD per the superpowers workflow; tests gated `#[cfg(feature = "lede")]`, run with `cargo test --features lede` (requires the sibling lede repo — D3).

- **Per extractor:** a fixture-text unit test asserting (a) the exact metadata key, (b) value *shape* (top_terms entries have `term/score/kind`; `lede_report` has `key_facts` + nested `metadata`; `entities` is a dict with an `unlabeled` list), (c) tags, (d) empty-input → empty/typed-empty result (mirror `lang_detect`'s empty-text guard).
- **Consolidator:** assert facts carry empty SVO strings, `support_span = Some`, descending rank-decay confidence, and `confidence_floor` filtering.
- **Config round-trip:** each new `type`/`mode` literal deserializes to its variant; an unknown field is rejected (the enums already use the project's strict serde).
- **Feature-off:** `build_extractor`/`build_consolidator` return the gated-error (not a panic) for each new variant when compiled without `lede`.
- **Parity spot-check (`lede_top_terms`):** lede 0.5 advertises byte-identical `top_terms` vs Python across its fixture walker; we assert our wrapper preserves order + the `{term,score,kind}` mapping, and rely on lede's own parity suite for the numeric core (we do not re-run lede's walker here).
- **Baseline:** default-feature suite stays green (7 tests + doctests, confirmed before changes).

## References

- chunkshop#76 — umbrella Rust-parity catch-up (Foundation + Tier 1).
- Python parity targets: `python/src/chunkshop/extractors/lede_top_terms.py`, `lede_report.py`, `consolidators/lede_facts.py`, `_consolidator.py`, `extractors/spacy_entities.py`; `result.py` (ExtractResult + chunker-wins merge).
- lede-rs 0.5 API: `lede/rust/src/{lib,types}.rs`, `lede/rust/src/extract/{top_terms,key_facts,correlate,metadata}.rs`.
- lede-enrich 0.1 API: `lede/lede-enrich/src/{lib,ner,facts}.rs`.
- Rust plumbing to mirror: `rust/chunkshop/src/config.rs` (`ExtractorConfig`/`ConsolidatorConfig`), `extractor.rs` (trait + dispatch + `lang_detect` template), `consolidators/mod.rs` (`FactTriple`/`ConsolidationOutput`/`extractive`), `summarizer.rs` (lede feature-gate pattern), `runner.rs` (metadata merge).
