# chunkshop Rust Semantic Chunker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-semantic.md`.

**Goal:** Port `SemanticChunker` from Python so `chunkshop-rs` accepts `chunker.type: semantic` and runs the same boundary-detection algorithm.

**Architecture:** New `sentence_split.rs` module + `SemanticChunker` struct in `chunker.rs`. Boundary embedder is a fresh `FastembedEmbedder` instance constructed by the chunker from `boundary_model`. New `ChunkerConfig::Semantic` variant. The boundary model name `sentence-transformers/all-MiniLM-L6-v2-int8` maps to fastembed-rs's stock `AllMiniLML6V2Q` (quantized + mean pooling) via `resolve_model_name` — close enough for boundary detection (cross-language byte-identical chunks not promised; brief Constraints).

**Tech Stack:** Rust 2021 + existing deps (`regex` for sentence split; the embedder path from MB-1).

---

## Task 1: Sentence splitter + percentile + merge unit tests

**Files:**
- Create: `rust/chunkshop/src/sentence_split.rs`
- Modify: `rust/chunkshop/src/lib.rs` (declare module)

- [ ] **Step 1:** Write `naive_sentences(&str) -> Vec<String>` matching Python's `_TERMINATOR = re.compile(r"(?<=[.!?])\s+")`. Rust's `regex` crate doesn't support lookbehind, so we walk the string manually:

```rust
//! Sentence splitting helpers for the semantic chunker.
//!
//! Mirrors `python/src/chunkshop/chunkers/_sentence_split.py`. Only the
//! `naive` splitter is implemented in Rust — `nltk` is Python-only.

/// Split text into sentences on terminator-then-whitespace boundaries.
/// Mirrors Python's regex `(?<=[.!?])\s+` (lookbehind keeps the terminator
/// attached to the preceding sentence).
pub fn naive_sentences(text: &str) -> Vec<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    let bytes = trimmed.as_bytes();
    let mut out: Vec<String> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'.' || b == b'!' || b == b'?' {
            // Find a run of one-or-more whitespace bytes after the terminator.
            let after_term = i + 1;
            let mut j = after_term;
            while j < bytes.len() && (bytes[j] as char).is_whitespace() {
                j += 1;
            }
            if j > after_term {
                // Have a [.!?]\s+ split. Sentence is bytes[start..after_term].
                let sent = std::str::from_utf8(&bytes[start..after_term])
                    .expect("utf-8 boundary safe at terminator")
                    .trim();
                if !sent.is_empty() {
                    out.push(sent.to_string());
                }
                start = j;
                i = j;
                continue;
            }
        }
        i += 1;
    }
    if start < bytes.len() {
        let tail = std::str::from_utf8(&bytes[start..])
            .expect("utf-8 boundary")
            .trim();
        if !tail.is_empty() {
            out.push(tail.to_string());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_returns_empty() {
        assert!(naive_sentences("").is_empty());
        assert!(naive_sentences("   \n  ").is_empty());
    }

    #[test]
    fn single_sentence_no_terminator() {
        assert_eq!(naive_sentences("just words"), vec!["just words"]);
    }

    #[test]
    fn three_sentences_split() {
        assert_eq!(
            naive_sentences("Hello world. This is two. And three!"),
            vec!["Hello world.", "This is two.", "And three!"]
        );
    }

    #[test]
    fn no_split_when_terminator_lacks_whitespace() {
        // Python's `(?<=[.!?])\s+` requires whitespace AFTER terminator.
        assert_eq!(naive_sentences("U.S.A. is here."), vec!["U.S.A. is here."]);
    }

    #[test]
    fn preserves_internal_punctuation() {
        assert_eq!(
            naive_sentences("Hello, world! How are you?"),
            vec!["Hello, world!", "How are you?"]
        );
    }
}
```

- [ ] **Step 2:** Add `pub mod sentence_split;` to `lib.rs`.

- [ ] **Step 3:** `cargo test --lib` — all sentence-split unit tests pass.

- [ ] **Step 4:** Commit.

---

## Task 2: Add SemanticChunkerConfig variant + resolve_model_name MiniLM mapping

**Files:**
- Modify: `rust/chunkshop/src/config.rs`
- Modify: `rust/chunkshop/src/embedder.rs` (add MiniLM int8 row to `resolve_model_name`)

- [ ] **Step 1:** In `config.rs`, add the variant:

```rust
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
    FixedOverlap(FixedOverlapChunkerConfig),
    NeighborExpand(NeighborExpandChunkerConfig),
    Semantic(SemanticChunkerConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct SemanticChunkerConfig {
    #[serde(default = "default_boundary_model")]
    pub boundary_model: String,
    #[serde(default = "default_breakpoint_percentile")]
    pub breakpoint_percentile: u32,
    #[serde(default = "default_min_sents_per_chunk")]
    pub min_sentences_per_chunk: usize,
    #[serde(default = "default_max_chunk_chars")]
    pub max_chunk_chars: usize,
    #[serde(default = "default_sentence_splitter")]
    pub sentence_splitter: String,
}

fn default_boundary_model() -> String {
    "sentence-transformers/all-MiniLM-L6-v2-int8".to_string()
}
fn default_breakpoint_percentile() -> u32 { 95 }
fn default_min_sents_per_chunk() -> usize { 3 }
fn default_max_chunk_chars() -> usize { 2000 }
fn default_sentence_splitter() -> String { "naive".to_string() }
```

- [ ] **Step 2:** In `embedder.rs`, find `resolve_model_name` and add a row for MiniLM int8 → fastembed-rs's stock `AllMiniLML6V2Q`:

```rust
table.insert(
    "sentence-transformers/all-MiniLM-L6-v2-int8",
    EmbeddingModel::AllMiniLML6V2Q,
);
table.insert(
    "sentence-transformers/all-MiniLM-L6-v2",
    EmbeddingModel::AllMiniLML6V2,
);
```

(The non-int8 row already exists; the int8 → Q mapping is the new bit. Note this is **not** bit-near-exact to Python's Xenova int8 MiniLM. Documented in the brief.)

- [ ] **Step 3:** Build + lib tests.

- [ ] **Step 4:** Commit.

---

## Task 3: SemanticChunker impl + ChunkerImpl

**Files:** `rust/chunkshop/src/chunker.rs` (append).

- [ ] **Step 1:** Add helpers + struct (large block):

```rust
use crate::config::SemanticChunkerConfig;
use crate::embedder::FastembedEmbedder;
use crate::config::FastembedEmbedderConfig;
use crate::sentence_split::naive_sentences;

pub struct SemanticChunker {
    cfg: SemanticChunkerConfig,
    boundary: std::sync::Mutex<FastembedEmbedder>,
}

impl SemanticChunker {
    pub fn new(cfg: SemanticChunkerConfig) -> anyhow::Result<Self> {
        if cfg.sentence_splitter != "naive" {
            return Err(anyhow::anyhow!(
                "sentence_splitter {:?} not supported in chunkshop-rs (only 'naive')",
                cfg.sentence_splitter
            ));
        }
        if cfg.breakpoint_percentile == 0 || cfg.breakpoint_percentile >= 100 {
            return Err(anyhow::anyhow!(
                "breakpoint_percentile must be in [1, 99], got {}",
                cfg.breakpoint_percentile
            ));
        }
        // Construct a small fastembed for boundary detection. Always a fresh
        // instance — no shared-model optimization in Rust (RAM trade-off,
        // documented in mission brief).
        let boundary_cfg = FastembedEmbedderConfig {
            model_name: cfg.boundary_model.clone(),
            // Dim for MiniLM is 384; we don't enforce it here because the
            // boundary embedder isn't compared against `target.dim` — the cell's
            // main embedder is. The dim field on FastembedEmbedderConfig is
            // only used as a sanity post-check on the first vector.
            dim: 384,
            batch_size: 16,
            threads: Some(2),
        };
        let boundary = FastembedEmbedder::new(boundary_cfg)?;
        Ok(Self {
            cfg,
            boundary: std::sync::Mutex::new(boundary),
        })
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        if doc.content.is_empty() || doc.content.trim().is_empty() {
            return Vec::new();
        }
        let sentences = naive_sentences(&doc.content);
        if sentences.is_empty() {
            return Vec::new();
        }
        if sentences.len() == 1 {
            // Single sentence: still respect max_chars.
            let mut chunks = Vec::new();
            for sub in self.split_if_too_large(&sentences[0]) {
                chunks.push(self.mk_chunk(&doc.id, chunks.len(), &sub));
            }
            return chunks;
        }

        // Embed sentences. Lock the inner embedder; .embed mutates internal state.
        let texts: Vec<String> = sentences.clone();
        let embeddings_res = self.boundary.lock().expect("poisoned").embed(texts);
        let Ok(embeddings) = embeddings_res else {
            tracing::error!("semantic chunker boundary embed failed: {:?}", embeddings_res.err());
            // Fall back to single-chunk-per-doc (preserves the doc, just no
            // semantic split).
            return vec![self.mk_chunk(&doc.id, 0, &doc.content)];
        };
        // L2-normalize. fastembed already normalizes BGE/MiniLM by default but
        // this is idempotent and matches Python's belt-and-braces step.
        let normed: Vec<Vec<f32>> = embeddings
            .iter()
            .map(|v| {
                let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
                let denom = if n == 0.0 { 1.0 } else { n };
                v.iter().map(|x| x / denom).collect()
            })
            .collect();

        // Cosine distances between adjacent sentence embeddings.
        let mut distances: Vec<f32> = Vec::with_capacity(normed.len() - 1);
        for i in 0..normed.len() - 1 {
            let dot: f32 = normed[i].iter().zip(&normed[i + 1]).map(|(a, b)| a * b).sum();
            distances.push(1.0 - dot);
        }

        let threshold = percentile_linear(&distances, self.cfg.breakpoint_percentile);
        let breakpoints: Vec<usize> = distances
            .iter()
            .enumerate()
            .filter_map(|(i, &d)| if d >= threshold { Some(i) } else { None })
            .collect();

        // Build spans: starts from 0, then each breakpoint+1.
        let mut starts: Vec<usize> = vec![0];
        for &bp in &breakpoints {
            starts.push(bp + 1);
        }
        let mut spans: Vec<(usize, usize)> = Vec::with_capacity(starts.len());
        for i in 0..starts.len() {
            let s = starts[i];
            let e = if i + 1 < starts.len() {
                starts[i + 1]
            } else {
                sentences.len()
            };
            spans.push((s, e));
        }

        spans = merge_small_spans(spans, self.cfg.min_sentences_per_chunk);

        let mut chunks: Vec<Chunk> = Vec::new();
        for (s, e) in spans {
            let body = sentences[s..e].join(" ").trim().to_string();
            if body.is_empty() {
                continue;
            }
            for sub in self.split_if_too_large(&body) {
                chunks.push(self.mk_chunk(&doc.id, chunks.len(), &sub));
            }
        }
        chunks
    }

    fn mk_chunk(&self, doc_id: &str, seq: usize, text: &str) -> Chunk {
        Chunk {
            doc_id: doc_id.to_string(),
            seq_num: seq,
            original_content: text.to_string(),
            embedded_content: text.to_string(),
            metadata: json!({ "strategy": "semantic" }),
        }
    }

    fn split_if_too_large(&self, body: &str) -> Vec<String> {
        if body.chars().count() <= self.cfg.max_chunk_chars {
            return vec![body.to_string()];
        }
        let sents = naive_sentences(body);
        if sents.is_empty() {
            // No sentence boundaries — fall back to char slicing.
            let chars: Vec<char> = body.chars().collect();
            return chars
                .chunks(self.cfg.max_chunk_chars)
                .map(|c| c.iter().collect())
                .collect();
        }
        let mut out: Vec<String> = Vec::new();
        let mut cur = String::new();
        for s in sents {
            let candidate = if cur.is_empty() {
                s.clone()
            } else {
                format!("{cur} {s}").trim().to_string()
            };
            if candidate.chars().count() > self.cfg.max_chunk_chars && !cur.is_empty() {
                out.push(cur.trim().to_string());
                cur = s;
            } else {
                cur = candidate;
            }
        }
        if !cur.is_empty() {
            if cur.chars().count() > self.cfg.max_chunk_chars {
                let chars: Vec<char> = cur.chars().collect();
                for window in chars.chunks(self.cfg.max_chunk_chars) {
                    out.push(window.iter().collect());
                }
            } else {
                out.push(cur.trim().to_string());
            }
        }
        out
    }
}

impl ChunkerImpl for SemanticChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// numpy.percentile with linear interpolation. Mirrors Python's default.
/// Returns the percentile value (NOT the index).
fn percentile_linear(values: &[f32], p: u32) -> f32 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted: Vec<f32> = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = sorted.len();
    if n == 1 {
        return sorted[0];
    }
    // numpy default: index = (n-1) * p / 100, then linear interp.
    let idx = (n as f64 - 1.0) * (p as f64) / 100.0;
    let lo = idx.floor() as usize;
    let hi = idx.ceil() as usize;
    if lo == hi {
        return sorted[lo];
    }
    let frac = (idx - lo as f64) as f32;
    sorted[lo] * (1.0 - frac) + sorted[hi] * frac
}

/// Merge spans smaller than `min` into neighbors. Forward-merge first;
/// backward-merge the last span if needed; pull the next span into the first
/// if the first ends up too small. Mirrors Python's `_merge_small`.
fn merge_small_spans(spans: Vec<(usize, usize)>, min: usize) -> Vec<(usize, usize)> {
    if spans.is_empty() {
        return spans;
    }
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (s, e) in spans {
        if !merged.is_empty() && (e - s) < min {
            let (ps, _) = merged[merged.len() - 1];
            let last = merged.len() - 1;
            merged[last] = (ps, e);
        } else {
            merged.push((s, e));
        }
    }
    if merged.len() > 1 && (merged[0].1 - merged[0].0) < min {
        let new_first = (merged[0].0, merged[1].1);
        merged[0] = new_first;
        merged.remove(1);
    }
    if merged.len() > 1 && (merged[merged.len() - 1].1 - merged[merged.len() - 1].0) < min {
        let last = merged.len() - 1;
        let (ps, _) = merged[last - 1];
        let (_, pe) = merged[last];
        merged[last - 1] = (ps, pe);
        merged.pop();
    }
    merged
}
```

- [ ] **Step 2:** Add unit tests at the bottom of `chunker.rs`'s `mod tests`:

```rust
#[test]
fn percentile_linear_matches_numpy() {
    // numpy.percentile([1,2,3,4,5], 95) -> 4.8
    let vs = vec![1.0_f32, 2.0, 3.0, 4.0, 5.0];
    let p = percentile_linear(&vs, 95);
    assert!((p - 4.8).abs() < 1e-5, "got {p}");

    // numpy.percentile([1,2,3,4], 50) -> 2.5
    let p = percentile_linear(&vec![1.0, 2.0, 3.0, 4.0], 50);
    assert!((p - 2.5).abs() < 1e-5);
}

#[test]
fn merge_small_spans_forward() {
    let spans = vec![(0, 5), (5, 6), (6, 10)];
    let m = merge_small_spans(spans, 3);
    assert_eq!(m, vec![(0, 6), (6, 10)]);
}

#[test]
fn merge_small_spans_backward_last() {
    let spans = vec![(0, 5), (5, 10), (10, 11)];
    let m = merge_small_spans(spans, 3);
    assert_eq!(m, vec![(0, 5), (5, 11)]);
}

#[test]
fn merge_small_spans_first_too_small_pulls_next() {
    let spans = vec![(0, 1), (1, 5), (5, 10)];
    let m = merge_small_spans(spans, 3);
    assert_eq!(m, vec![(0, 5), (5, 10)]);
}
```

- [ ] **Step 3:** Add `Semantic` arm to `build_chunker` in `runner.rs`:

```rust
ChunkerConfig::Semantic(c) => Box::new(SemanticChunker::new(c)?),
```

- [ ] **Step 4:** Build + run lib tests. All pass.

- [ ] **Step 5:** Commit.

---

## Task 4: ⛔ DC-001 — re-read brief

- [ ] Re-read brief. Confirm scope: only semantic chunker added. No summary_* chunkers; no Python changes.

---

## Task 5: End-to-end smoke test

**Files:** `rust/chunkshop/tests/semantic_smoke.rs`

- [ ] **Step 1:** Write the smoke test:

```rust
//! Smoke test: SemanticChunker runs end-to-end on a sample corpus and
//! produces non-empty, well-formed chunks. Does NOT assert byte-identical
//! cross-language parity (algorithmically infeasible — see brief).

use std::path::PathBuf;

use chunkshop::chunker::SemanticChunker;
use chunkshop::config::SemanticChunkerConfig;
use chunkshop::source::Document;
use serde_json::json;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[test]
fn semantic_chunker_runs_end_to_end() {
    let path = fixtures_dir().join("framer_heading_corpus.md");
    let content = std::fs::read_to_string(&path).expect("read corpus");

    let cfg = SemanticChunkerConfig {
        boundary_model: "sentence-transformers/all-MiniLM-L6-v2-int8".to_string(),
        breakpoint_percentile: 95,
        min_sentences_per_chunk: 3,
        max_chunk_chars: 2000,
        sentence_splitter: "naive".to_string(),
    };

    // Construction triggers boundary-model download. If HF is unreachable,
    // skip the test rather than fail.
    let chunker = match SemanticChunker::new(cfg) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("skipping semantic_smoke: boundary embedder init failed: {e:#}");
            return;
        }
    };

    let doc = Document {
        id: "smoke".into(),
        content,
        title: None,
        metadata: json!({}),
    };
    let chunks = chunker.chunk(&doc);
    assert!(!chunks.is_empty(), "semantic chunker emitted zero chunks");
    for (i, c) in chunks.iter().enumerate() {
        assert_eq!(c.metadata["strategy"], "semantic", "chunk[{i}] strategy");
        assert!(
            c.original_content.chars().count() <= 2000,
            "chunk[{i}] over max_chunk_chars: {} chars",
            c.original_content.chars().count()
        );
        assert!(!c.original_content.is_empty(), "chunk[{i}] empty content");
    }
}
```

- [ ] **Step 2:** Run with HF cache primed:
```bash
cd rust && cargo test --test semantic_smoke -- --nocapture
```
Expected: PASS. Boundary model is small (~22 MB MiniLM int8), should download quickly the first time.

- [ ] **Step 3:** Commit.

---

## Task 6: README + CHANGELOG

- "What works" chunker row: add `semantic`. Mark explicitly that semantic is **NOT** byte-identical to Python (only chunker that isn't, due to embedder drift).
- "What does NOT work" chunker line: drop `semantic`.
- CHANGELOG entry.

---

## Task 7: ⛔ DC-FINAL + finishing-a-development-branch.
