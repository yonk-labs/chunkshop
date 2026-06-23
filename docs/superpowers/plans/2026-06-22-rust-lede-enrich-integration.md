# Rust lede / lede-enrich Tier-1 Enrichment Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring chunkshop-rs to parity on the lede-dependent Tier-1 enrichment layer: bump to lede 0.5 + add lede-enrich 0.1, and wire `lede_top_terms`, `lede_report`, `lede_entities` extractors plus a `lede` consolidator mode.

**Architecture:** Each feature follows the crate's "one config variant + one dispatch arm + one impl + tests" extension pattern. lede/lede-enrich types have no serde derives, so each extractor hand-builds `serde_json::Value`. Everything is gated behind the existing optional `lede` cargo feature (widened to also pull `lede-enrich`).

**Tech Stack:** Rust, `serde_json`, `anyhow`, the local `lede` 0.5 + `lede-enrich` 0.1 path crates.

**Spec:** `docs/superpowers/specs/2026-06-22-rust-lede-enrich-integration-design.md`

## Global Constraints

- Work in `rust/chunkshop/` (crate `chunkshop`). All `cargo` commands use `--manifest-path /home/yonk/yonk-tools/chunkshop-rust-lede/rust/chunkshop/Cargo.toml`.
- New code compiles + tests run **only** under `--features lede`. Default build must stay green and untouched.
- `lede` / `lede-enrich` are path deps to the `yonk-tools/`-sibling `lede` repo (`../../../lede/rust`, `../../../lede/lede-enrich`). `--features lede` requires that checkout.
- Config enums: `ExtractorConfig` is `#[serde(tag = "type", rename_all = "snake_case")]`; `ConsolidatorConfig` is `#[serde(tag = "mode", rename_all = "snake_case")]`. Variant name → snake_case literal automatically.
- `ExtractResult { tags: Vec<String>, metadata: serde_json::Map<String, Value> }`. `FactTriple { subject: String, predicate: String, object: String, support_span: Option<String>, confidence: Option<f64> }`.
- Metadata keys & shapes are fixed by the spec: `top_terms` = `[{term,score,kind}]`; `entities` = `{"unlabeled": [str]}`; `lede_report` = `{key_facts:[str], metadata:{dates,amounts,urls,entities:[str]}}`.
- Commit after every task. Conventional commits, scope `rust`.

---

### Task 0: Dependency bump + feature wiring

**Files:**
- Modify: `rust/chunkshop/Cargo.toml:109` (lede dep), `:221` (lede feature), README note.

**Interfaces:**
- Produces: a buildable `--features lede` against lede 0.5 (`lede::extract::top_terms_scored`, `key_facts`, `lede::Mode`, `lede::summarize`) + lede-enrich 0.1 (`lede_enrich::metadata`, `lede_enrich::extract_entities`).

- [ ] **Step 1: Edit the dep lines.** Replace `Cargo.toml:109`:

```toml
# --- Optional lede enrichment integration (summarizer + Tier-1 extractors) ---
# Path deps: lede 0.5 / lede-enrich 0.1 are unpublished; the `version` is for an
# eventual crates.io publish, `path` for local dev. `--features lede` therefore
# requires the `lede` repo checked out as a yonk-tools/ sibling. Default builds
# don't touch this (feature is opt-in).
lede        = { version = "0.5", path = "../../../lede/rust",        optional = true }
lede-enrich = { version = "0.1", path = "../../../lede/lede-enrich", optional = true }
```

- [ ] **Step 2: Widen the feature.** Replace `Cargo.toml:219-221`:

```toml
# Enables the `chunkshop.summarizers.lede` callable summarizer AND the
# lede-backed Tier-1 extractors (lede_top_terms / lede_report / lede_entities)
# + the `lede` consolidator. Pulls lede + lede-enrich (path deps — see above).
# Build with: cargo build --features lede
lede = ["dep:lede", "dep:lede-enrich"]
```

- [ ] **Step 3: Build to verify resolution.**

Run: `cargo build --features lede --manifest-path /home/yonk/yonk-tools/chunkshop-rust-lede/rust/chunkshop/Cargo.toml 2>&1 | tail -20`
Expected: compiles (existing `summarizer.rs` lede path still builds against 0.5 — `summarize`/`Mode` signatures unchanged). If lede 0.5 changed `summarize`'s signature, fix the call in `summarizer.rs:188` to match before proceeding.

- [ ] **Step 4: Confirm default build still green.**

Run: `cargo test --manifest-path /home/yonk/yonk-tools/chunkshop-rust-lede/rust/chunkshop/Cargo.toml 2>&1 | tail -5`
Expected: `test result: ok.` (same 7 tests + doctests as baseline).

- [ ] **Step 5: Commit.**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/Cargo.lock
git commit -m "build(rust): bump lede 0.3->0.5 + add lede-enrich 0.1 (path deps, #76)"
```

---

### Task 1: `lede_top_terms` extractor

**Files:**
- Modify: `rust/chunkshop/src/config.rs` (enum variant + config struct, after `:127`/`:175`)
- Modify: `rust/chunkshop/src/extractor.rs` (use-imports, dispatch arm, impl, test)

**Interfaces:**
- Consumes: `lede::extract::{top_terms_scored, TopTermsOptions, TermScore}`. `TermScore { term: String, score: f64, kind: String }`.
- Produces: extractor writing metadata key `top_terms` = `Vec<{term,score,kind}>`, tags = `Vec<term>`.

- [ ] **Step 1: Write the failing test** in `extractor.rs` `#[cfg(test)] mod tests` (gate it `#[cfg(feature = "lede")]`):

```rust
#[cfg(feature = "lede")]
#[test]
fn lede_top_terms_emits_scored_terms_and_tags() {
    let ex = LedeTopTermsExtractor::new(crate::config::LedeTopTermsExtractorConfig {
        top_k: 5, words: true, phrases: true,
    });
    let r = ex.extract("The quick brown fox. The fox jumps. Foxes are quick animals.").unwrap();
    let tt = r.metadata.get("top_terms").unwrap().as_array().unwrap();
    assert!(!tt.is_empty());
    let first = tt[0].as_object().unwrap();
    assert!(first.contains_key("term") && first.contains_key("score") && first.contains_key("kind"));
    assert_eq!(r.tags.len(), tt.len()); // one tag per term, same order
}

#[cfg(feature = "lede")]
#[test]
fn lede_top_terms_empty_text_is_empty() {
    let ex = LedeTopTermsExtractor::new(crate::config::LedeTopTermsExtractorConfig {
        top_k: 5, words: true, phrases: true,
    });
    let r = ex.extract("   ").unwrap();
    assert!(r.metadata.get("top_terms").unwrap().as_array().unwrap().is_empty());
    assert!(r.tags.is_empty());
}
```

- [ ] **Step 2: Run, verify it fails to compile** (`LedeTopTermsExtractor` undefined).

Run: `cargo test --features lede --manifest-path .../Cargo.toml lede_top_terms 2>&1 | tail -15`
Expected: compile error `cannot find ... LedeTopTermsExtractor`.

- [ ] **Step 3: Add config** in `config.rs` — variant in the enum (after `SpacyEntities`):

```rust
    LedeTopTerms(LedeTopTermsExtractorConfig),
```
and the struct + defaults near the other extractor configs:
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct LedeTopTermsExtractorConfig {
    #[serde(default = "default_top_terms_k")]
    pub top_k: usize,
    #[serde(default = "default_true")]
    pub words: bool,
    #[serde(default = "default_true")]
    pub phrases: bool,
}
fn default_top_terms_k() -> usize { 10 }
fn default_true() -> bool { true }
```

- [ ] **Step 4: Add impl + dispatch** in `extractor.rs`. Add to the `use crate::config::{...}` list: `LedeTopTermsExtractorConfig`. Add dispatch arms (cfg-split) in `build_extractor`:

```rust
        #[cfg(feature = "lede")]
        ExtractorConfig::LedeTopTerms(c) => Ok(Box::new(LedeTopTermsExtractor::new(c))),
        #[cfg(not(feature = "lede"))]
        ExtractorConfig::LedeTopTerms(c) => { let _ = c; Err(anyhow!(
            "lede_top_terms extractor is gated behind the `lede` cargo feature; \
             build with --features lede or run this YAML on Python.")) }
```
Impl (feature-gated), near the other extractor impls:
```rust
#[cfg(feature = "lede")]
pub struct LedeTopTermsExtractor { cfg: crate::config::LedeTopTermsExtractorConfig }

#[cfg(feature = "lede")]
impl LedeTopTermsExtractor {
    pub fn new(cfg: crate::config::LedeTopTermsExtractorConfig) -> Self { Self { cfg } }
}

#[cfg(feature = "lede")]
impl ExtractorImpl for LedeTopTermsExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        let mut metadata = serde_json::Map::new();
        if text.trim().is_empty() {
            metadata.insert("top_terms".into(), Value::Array(vec![]));
            return Ok(ExtractResult { tags: vec![], metadata });
        }
        let opts = lede::extract::TopTermsOptions {
            n: self.cfg.top_k, words: self.cfg.words, phrases: self.cfg.phrases,
            ..Default::default()
        };
        let scored = lede::extract::top_terms_scored(text, &opts);
        let mut tags = Vec::with_capacity(scored.len());
        let arr: Vec<Value> = scored.iter().map(|t| {
            tags.push(t.term.clone());
            let mut o = serde_json::Map::new();
            o.insert("term".into(), Value::String(t.term.clone()));
            o.insert("score".into(), serde_json::Number::from_f64(t.score).map(Value::Number).unwrap_or(Value::Null));
            o.insert("kind".into(), Value::String(t.kind.clone()));
            Value::Object(o)
        }).collect();
        metadata.insert("top_terms".into(), Value::Array(arr));
        Ok(ExtractResult { tags, metadata })
    }
}
```

- [ ] **Step 5: Run tests, verify pass.** `cargo test --features lede ... lede_top_terms` → 2 passed. Also `cargo test ...` (no feature) stays green.
- [ ] **Step 6: Commit.** `git commit -am "feat(rust): lede_top_terms extractor (#76)"`

---

### Task 2: `lede_entities` extractor

**Files:** Modify `config.rs` (variant + unit struct), `extractor.rs` (import, dispatch, impl, test).

**Interfaces:**
- Consumes: `lede_enrich::extract_entities(text) -> Vec<String>`.
- Produces: metadata key `entities` = `{"unlabeled": [str]}` (dict[str,list[str]]); tags = `[]`.

- [ ] **Step 1: Failing test** (`#[cfg(feature = "lede")]`):

```rust
#[cfg(feature = "lede")]
#[test]
fn lede_entities_emits_unlabeled_bucket_dict() {
    let ex = LedeEntitiesExtractor::new(crate::config::LedeEntitiesExtractorConfig {});
    let r = ex.extract("Acme Corp hired Bob Smith in Berlin.").unwrap();
    let ents = r.metadata.get("entities").unwrap().as_object().unwrap();
    let bucket = ents.get("unlabeled").unwrap().as_array().unwrap();
    assert!(!bucket.is_empty());
    assert!(r.tags.is_empty());
}

#[cfg(feature = "lede")]
#[test]
fn lede_entities_empty_text_empty_dict() {
    let ex = LedeEntitiesExtractor::new(crate::config::LedeEntitiesExtractorConfig {});
    let r = ex.extract("").unwrap();
    let ents = r.metadata.get("entities").unwrap().as_object().unwrap();
    assert!(ents.get("unlabeled").unwrap().as_array().unwrap().is_empty());
}
```

- [ ] **Step 2: Run, verify compile-fail.**
- [ ] **Step 3: Config** — enum variant `LedeEntities(LedeEntitiesExtractorConfig)` + `#[derive(Debug, Clone, Deserialize)] pub struct LedeEntitiesExtractorConfig {}`.
- [ ] **Step 4: Impl + dispatch** (same cfg-split dispatch shape as Task 1; error message names `lede_entities`). Impl:

```rust
#[cfg(feature = "lede")]
pub struct LedeEntitiesExtractor;
#[cfg(feature = "lede")]
impl LedeEntitiesExtractor {
    pub fn new(_cfg: crate::config::LedeEntitiesExtractorConfig) -> Self { Self }
}
#[cfg(feature = "lede")]
impl ExtractorImpl for LedeEntitiesExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        let ents = if text.trim().is_empty() { vec![] } else { lede_enrich::extract_entities(text) };
        let bucket: Vec<Value> = ents.into_iter().map(Value::String).collect();
        let mut dict = serde_json::Map::new();
        dict.insert("unlabeled".into(), Value::Array(bucket));
        let mut metadata = serde_json::Map::new();
        metadata.insert("entities".into(), Value::Object(dict));
        Ok(ExtractResult { tags: vec![], metadata })
    }
}
```

- [ ] **Step 5: Run tests pass; default build green.**
- [ ] **Step 6: Commit.** `git commit -am "feat(rust): lede_entities extractor via lede-enrich gazetteer NER (#76)"`

---

### Task 3: `lede_report` extractor (subset assembly)

**Files:** Modify `config.rs` (variant + struct), `extractor.rs` (import, dispatch, impl, test).

**Interfaces:**
- Consumes: `lede::extract::key_facts(text, max_facts) -> Vec<String>`; `lede_enrich::metadata(text) -> Metadata { dates, amounts, urls, entities: Vec<String> }`.
- Produces: metadata key `lede_report` = `{key_facts:[str], metadata:{dates,amounts,urls,entities:[str]}}`; tags = flattened producible `tag_sources`.

- [ ] **Step 1: Failing test** (`#[cfg(feature = "lede")]`):

```rust
#[cfg(feature = "lede")]
#[test]
fn lede_report_emits_subset_shape() {
    let ex = LedeReportExtractor::new(crate::config::LedeReportExtractorConfig {
        max_facts: 5,
        tag_sources: crate::config::default_lede_report_tag_sources(),
    });
    let r = ex.extract("Acme raised $5M on 2023-01-02. Acme grew fast. See https://acme.test.").unwrap();
    let rep = r.metadata.get("lede_report").unwrap().as_object().unwrap();
    assert!(rep.get("key_facts").unwrap().is_array());
    let meta = rep.get("metadata").unwrap().as_object().unwrap();
    for k in ["dates", "amounts", "urls", "entities"] {
        assert!(meta.get(k).unwrap().is_array(), "missing {k}");
    }
    assert!(rep.get("attributes").is_none()); // omitted subset
}
```

- [ ] **Step 2: Run, verify compile-fail.**
- [ ] **Step 3: Config:**

```rust
    LedeReport(LedeReportExtractorConfig),
```
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct LedeReportExtractorConfig {
    #[serde(default = "default_lede_report_max_facts")]
    pub max_facts: usize,
    #[serde(default = "default_lede_report_tag_sources")]
    pub tag_sources: Vec<String>,
}
fn default_lede_report_max_facts() -> usize { 10 }
pub fn default_lede_report_tag_sources() -> Vec<String> {
    // Python default minus `attributes` (not producible in Rust — see spec D1).
    ["key_facts", "dates", "amounts", "entities"].iter().map(|s| s.to_string()).collect()
}
```

- [ ] **Step 4: Impl + dispatch** (cfg-split dispatch as before; error names `lede_report`). Impl:

```rust
#[cfg(feature = "lede")]
pub struct LedeReportExtractor { cfg: crate::config::LedeReportExtractorConfig }
#[cfg(feature = "lede")]
impl LedeReportExtractor {
    pub fn new(cfg: crate::config::LedeReportExtractorConfig) -> Self { Self { cfg } }
}
#[cfg(feature = "lede")]
fn str_array(v: &[String]) -> Value { Value::Array(v.iter().cloned().map(Value::String).collect()) }
#[cfg(feature = "lede")]
impl ExtractorImpl for LedeReportExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        let mut report = serde_json::Map::new();
        if text.trim().is_empty() {
            report.insert("key_facts".into(), Value::Array(vec![]));
            let mut m = serde_json::Map::new();
            for k in ["dates","amounts","urls","entities"] { m.insert(k.into(), Value::Array(vec![])); }
            report.insert("metadata".into(), Value::Object(m));
            let mut metadata = serde_json::Map::new();
            metadata.insert("lede_report".into(), Value::Object(report));
            return Ok(ExtractResult { tags: vec![], metadata });
        }
        let key_facts = lede::extract::key_facts(text, self.cfg.max_facts);
        let md = lede_enrich::metadata(text); // entities filled by gazetteer
        report.insert("key_facts".into(), str_array(&key_facts));
        let mut m = serde_json::Map::new();
        m.insert("dates".into(), str_array(&md.dates));
        m.insert("amounts".into(), str_array(&md.amounts));
        m.insert("urls".into(), str_array(&md.urls));
        m.insert("entities".into(), str_array(&md.entities));
        report.insert("metadata".into(), Value::Object(m));

        // Tags: flatten the producible sources named in tag_sources.
        let mut tags = Vec::new();
        for src in &self.cfg.tag_sources {
            match src.as_str() {
                "key_facts" => tags.extend(key_facts.iter().cloned()),
                "dates" => tags.extend(md.dates.iter().cloned()),
                "amounts" => tags.extend(md.amounts.iter().cloned()),
                "entities" => tags.extend(md.entities.iter().cloned()),
                "urls" => tags.extend(md.urls.iter().cloned()),
                _ => {} // unknown/Python-only source (e.g. attributes) ignored
            }
        }
        let mut metadata = serde_json::Map::new();
        metadata.insert("lede_report".into(), Value::Object(report));
        Ok(ExtractResult { tags, metadata })
    }
}
```

- [ ] **Step 5: Run tests pass; default build green.**
- [ ] **Step 6: Commit.** `git commit -am "feat(rust): lede_report extractor (subset assembly, #76)"`

---

### Task 4: `lede` consolidator mode

**Files:** Modify `config.rs` (`ConsolidatorConfig` variant + `LedeConsolidatorConfig` — locate the existing `ConsolidatorConfig` enum, ~`:788`), `consolidators/mod.rs` (impl, dispatch arm, test).

**Interfaces:**
- Consumes: `lede::extract::key_facts(text, max_facts) -> Vec<String>`. `EpisodeInput.text`.
- Produces: `ConsolidationOutput { summary: "", facts }` where each `FactTriple` has empty SVO strings, `support_span = Some(sentence)`, `confidence = Some(round(1 - i/n, 3))`, filtered by `confidence_floor`. `mode() == "lede"`.

- [ ] **Step 1: Failing test** in `consolidators/mod.rs` tests (`#[cfg(feature = "lede")]`):

```rust
#[cfg(feature = "lede")]
#[test]
fn lede_consolidator_rank_decay_and_floor() {
    let c = LedeConsolidator::new(crate::config::LedeConsolidatorConfig { max_facts: 10, confidence_floor: 0.0 });
    let ep = EpisodeInput {
        text: "Acme raised five million dollars. Acme hired Bob. Acme opened a Berlin office. Revenue tripled.",
        frame_seq: 1, session_id: "s1", episode_start_ts: 0.0, episode_end_ts: 1.0,
    };
    let out = c.consolidate(&ep).unwrap();
    assert_eq!(c.mode(), "lede");
    assert!(!out.facts.is_empty());
    let f0 = &out.facts[0];
    assert_eq!(f0.subject, ""); assert_eq!(f0.predicate, ""); assert_eq!(f0.object, "");
    assert!(f0.support_span.is_some());
    // rank-decay: confidence non-increasing
    let confs: Vec<f64> = out.facts.iter().map(|f| f.confidence.unwrap()).collect();
    assert!(confs.windows(2).all(|w| w[0] >= w[1]));
}

#[cfg(feature = "lede")]
#[test]
fn lede_consolidator_floor_filters() {
    let c = LedeConsolidator::new(crate::config::LedeConsolidatorConfig { max_facts: 10, confidence_floor: 0.99 });
    let ep = EpisodeInput {
        text: "One fact here. Two fact here. Three fact here. Four fact here.",
        frame_seq: 1, session_id: "s1", episode_start_ts: 0.0, episode_end_ts: 1.0,
    };
    let out = c.consolidate(&ep).unwrap();
    assert!(out.facts.iter().all(|f| f.confidence.unwrap() >= 0.99));
}
```

- [ ] **Step 2: Run, verify compile-fail.**
- [ ] **Step 3: Config** — add to `ConsolidatorConfig` enum: `Lede(LedeConsolidatorConfig),` and:

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct LedeConsolidatorConfig {
    #[serde(default = "default_lede_cons_max_facts")]
    pub max_facts: usize,
    #[serde(default)]
    pub confidence_floor: f64,
}
fn default_lede_cons_max_facts() -> usize { 10 }
```
(Match the existing `ConsolidatorConfig` derive/serde attrs — it's `#[serde(tag = "mode", rename_all = "snake_case")]`.)

- [ ] **Step 4: Impl + dispatch** in `consolidators/mod.rs`. Add `use crate::config::LedeConsolidatorConfig;`. Dispatch arm in `build_consolidator` (note: returns `Box<dyn Consolidator>`, not Result — so feature-off errors at `consolidate()`):

```rust
        ConsolidatorConfig::Lede(c) => Box::new(LedeConsolidator::new(c.clone())),
```
Impl — struct + `new` defined in **both** feature states; `consolidate` body cfg-split:
```rust
pub struct LedeConsolidator { #[allow(dead_code)] cfg: crate::config::LedeConsolidatorConfig }
impl LedeConsolidator {
    pub fn new(cfg: crate::config::LedeConsolidatorConfig) -> Self { Self { cfg } }
}
impl Consolidator for LedeConsolidator {
    #[cfg(feature = "lede")]
    fn consolidate(&self, episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput> {
        let cleaned = strip_role_tags(episode.text);
        let facts_text = lede::extract::key_facts(&cleaned, self.cfg.max_facts);
        let n = facts_text.len();
        let facts: Vec<FactTriple> = facts_text.into_iter().enumerate().filter_map(|(i, sentence)| {
            let confidence = if n == 0 { 0.0 } else { ((1.0 - (i as f64 / n as f64)) * 1000.0).round() / 1000.0 };
            if confidence < self.cfg.confidence_floor { return None; }
            Some(FactTriple {
                subject: String::new(), predicate: String::new(), object: String::new(),
                support_span: Some(sentence), confidence: Some(confidence),
            })
        }).collect();
        Ok(ConsolidationOutput { summary: String::new(), facts })
    }
    #[cfg(not(feature = "lede"))]
    fn consolidate(&self, _episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput> {
        anyhow::bail!("`lede` consolidator mode is gated behind the `lede` cargo feature; build with --features lede or run on Python.")
    }
    fn mode(&self) -> &'static str { "lede" }
}
```

- [ ] **Step 5: Run tests pass; default build green.** `cargo test --features lede ... lede_consolidator` → 2 passed; `cargo test ...` green.
- [ ] **Step 6: Commit.** `git commit -am "feat(rust): lede consolidator mode (salient-sentence facts, #76)"`

---

### Task 5: Docs — CHANGELOG + wire-parity note

**Files:** Modify `rust/CHANGELOG.md` (or crate changelog), README "wire-parity caveats" if present.

- [ ] **Step 1:** Add a CHANGELOG entry summarizing: lede 0.3→0.5 + lede-enrich 0.1; new `lede_top_terms` / `lede_report` (subset) / `lede_entities` extractors + `lede` consolidator (all `--features lede`, path-dep on sibling lede repo); `lede_report` omits `attributes`/SVO `fact_records`; `lede_entities` writes `entities` as `{"unlabeled": [...]}` (schema-uniform, content-divergent vs spaCy).
- [ ] **Step 2:** Commit. `git commit -am "docs(rust): changelog + wire-parity notes for lede Tier-1 parity (#76)"`

---

### Task 6: Full feature-gated test sweep

- [ ] **Step 1:** `cargo test --features lede --manifest-path .../Cargo.toml 2>&1 | tail -20` → all green (new + existing lede-gated tests).
- [ ] **Step 2:** `cargo test --manifest-path .../Cargo.toml 2>&1 | tail -5` → default build still green.
- [ ] **Step 3:** `cargo clippy --features lede --manifest-path .../Cargo.toml 2>&1 | tail -20` → no new warnings on touched files (match the crate's lint posture; fix any introduced).
- [ ] No commit (verification only) — proceed to `superpowers:finishing-a-development-branch`.

## Self-Review

**Spec coverage:** Foundation/dep-bump → Task 0. lede_top_terms → T1. lede_entities (D2) → T2. lede_report (D1 subset) → T3. consolidator:lede → T4. Wire-parity docs (D2/D1/D3) → T5. Testing strategy → per-task tests + T6 sweep. All spec sections mapped. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. ✓

**Type consistency:** `LedeTopTermsExtractorConfig{top_k,words,phrases}`, `LedeEntitiesExtractorConfig{}`, `LedeReportExtractorConfig{max_facts,tag_sources}`, `LedeConsolidatorConfig{max_facts,confidence_floor}` used identically in config + impl + tests. `default_lede_report_tag_sources` is `pub` (referenced in T3 test). lede API names (`top_terms_scored`, `TopTermsOptions`, `key_facts`, `lede_enrich::{metadata,extract_entities}`) match the scout-verified surface. ✓

**Open risk flagged for execution:** T0 Step 3 — if lede 0.5 changed `summarize`/`Mode` signatures vs 0.3, the existing `summarizer.rs:188` call must be adjusted; that's the only pre-existing call site and the build will surface it immediately.
