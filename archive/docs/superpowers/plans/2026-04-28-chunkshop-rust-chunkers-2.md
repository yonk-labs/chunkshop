# chunkshop Rust Chunker Batch 2 (fixed_overlap + neighbor_expand) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-chunkers-2.md`.

**Goal:** Port `FixedOverlapChunker` and `NeighborExpandChunker` from Python to Rust.

**Architecture:** Two new structs in `rust/chunkshop/src/chunker.rs`. Two new variants on `ChunkerConfig` (the neighbor_expand variant uses `Box<ChunkerConfig>` for its recursive `base` field). `AnyChunker` enum gets two new arms; the `NeighborExpand` arm constructs its base by recursively dispatching on `ChunkerConfig`. Two new integration tests against committed Python fixtures.

**Tech Stack:** Rust 2021 + existing deps.

---

## Task 1: Add config variants

**Files:** `rust/chunkshop/src/config.rs`

- [ ] **Step 1:** Find `ChunkerConfig` enum and append two variants:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
    FixedOverlap(FixedOverlapChunkerConfig),
    NeighborExpand(NeighborExpandChunkerConfig),
}
```

- [ ] **Step 2:** Add the two structs:

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct FixedOverlapChunkerConfig {
    #[serde(default = "default_window_words")]
    pub window_words: usize,
    #[serde(default = "default_step_words")]
    pub step_words: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NeighborExpandChunkerConfig {
    pub base: Box<ChunkerConfig>,
    #[serde(default = "default_neighbor_window")]
    pub window: usize,
}

fn default_window_words() -> usize { 300 }
fn default_step_words() -> usize { 150 }
fn default_neighbor_window() -> usize { 1 }
```

- [ ] **Step 3:** Build + unit test that nested neighbor_expand parses.

```bash
cd rust && cargo build --workspace
```

- [ ] **Step 4:** Commit.

---

## Task 2: Implement both chunkers

**Files:** `rust/chunkshop/src/chunker.rs`

- [ ] **Step 1:** Append `FixedOverlapChunker`:

```rust
use crate::config::FixedOverlapChunkerConfig;

pub struct FixedOverlapChunker {
    cfg: FixedOverlapChunkerConfig,
}

impl FixedOverlapChunker {
    pub fn new(cfg: FixedOverlapChunkerConfig) -> Result<Self> {
        if cfg.window_words == 0 || cfg.step_words == 0 {
            return Err(anyhow::anyhow!(
                "window_words and step_words must be positive"
            ));
        }
        Ok(Self { cfg })
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        // Match Python's text.split() — split on whitespace runs, drop empties.
        let words: Vec<&str> = doc.content.split_whitespace().collect();
        let window = self.cfg.window_words;
        let step = self.cfg.step_words;
        let mut chunks: Vec<Chunk> = Vec::new();
        let mut seq = 0usize;
        let mut i = 0usize;
        while i < words.len() {
            let end = (i + window).min(words.len());
            let slice = &words[i..end];
            let text = slice.join(" ");
            chunks.push(Chunk {
                doc_id: doc.id.clone(),
                seq_num: seq,
                original_content: text.clone(),
                embedded_content: text,
                metadata: json!({
                    "strategy": "fixed_overlap",
                    "start_word": i,
                    "n_words": slice.len(),
                }),
            });
            seq += 1;
            if i + window >= words.len() {
                break;
            }
            i += step;
        }
        chunks
    }
}
```

- [ ] **Step 2:** Append `NeighborExpandChunker`. Note this wraps a base chunker; its constructor takes the constructed base, not the config — recursive dispatch happens in the runner.

```rust
use crate::config::NeighborExpandChunkerConfig;

pub struct NeighborExpandChunker {
    window: usize,
    base: Box<dyn ChunkerImpl + Send + Sync>,
}

trait ChunkerImpl {
    fn chunk(&self, doc: &Document) -> Vec<Chunk>;
}

impl ChunkerImpl for SentenceAwareChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> { Self::chunk(self, doc) }
}
impl ChunkerImpl for HierarchyChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> { Self::chunk(self, doc) }
}
impl ChunkerImpl for FixedOverlapChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> { Self::chunk(self, doc) }
}

impl NeighborExpandChunker {
    pub fn new(cfg: NeighborExpandChunkerConfig, base: Box<dyn ChunkerImpl + Send + Sync>) -> Self {
        Self { window: cfg.window, base }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let base_chunks = self.base.chunk(doc);
        let mut out = Vec::with_capacity(base_chunks.len());
        let w = self.window;
        let n = base_chunks.len();
        for (i, bc) in base_chunks.iter().enumerate() {
            let lo = i.saturating_sub(w);
            let hi = (i + w).min(n.saturating_sub(1));
            let parts: Vec<&str> = (lo..=hi)
                .map(|j| base_chunks[j].embedded_content.as_str())
                .collect();
            let joined = parts.join("\n\n");

            // Merge {neighbor_expand_window: w} into the base chunk's metadata.
            let mut merged = bc.metadata.as_object().cloned().unwrap_or_default();
            merged.insert(
                "neighbor_expand_window".to_string(),
                serde_json::Value::from(w as u64),
            );

            out.push(Chunk {
                doc_id: bc.doc_id.clone(),
                seq_num: bc.seq_num,
                original_content: bc.original_content.clone(),
                embedded_content: joined,
                metadata: serde_json::Value::Object(merged),
            });
        }
        out
    }
}
```

- [ ] **Step 3:** Build:
```bash
cd rust && cargo build --workspace
```

---

## Task 3: Update runner dispatch

**Files:** `rust/chunkshop/src/runner.rs`

- [ ] **Step 1:** Replace `AnyChunker` with a version that includes both new variants. NeighborExpand needs a recursive constructor — extract a helper `fn build_chunker(cfg: ChunkerConfig) -> Box<dyn ChunkerImpl + Send + Sync>` that recursively constructs base.

```rust
fn build_chunker_box(cfg: ChunkerConfig) -> Box<dyn chunker::ChunkerImpl + Send + Sync> {
    use crate::config::ChunkerConfig as C;
    use crate::chunker::*;
    match cfg {
        C::SentenceAware(c) => Box::new(SentenceAwareChunker::new(c)),
        C::Hierarchy(c) => Box::new(HierarchyChunker::new(c)),
        C::FixedOverlap(c) => Box::new(FixedOverlapChunker::new(c).expect("valid fixed_overlap")),
        C::NeighborExpand(c) => {
            let base = build_chunker_box(*c.base);
            Box::new(NeighborExpandChunker::new(c, base))
        }
    }
}
```

(`ChunkerImpl` needs to be `pub(crate)` or `pub`. Make it `pub` in the chunker module so the runner can name it.)

Replace the existing `AnyChunker` match in `run_cell` with:
```rust
let chunker = build_chunker_box(cfg.chunker);
```

And drop the old `AnyChunker` enum entirely — `Box<dyn ChunkerImpl>` replaces it cleanly.

- [ ] **Step 2:** Build + run all unit tests. Existing tests must continue to pass.

```bash
cd rust && cargo test --lib && cargo test --test hierarchy_parity --test parity --test embedding_parity --test json_corpus_source --test sink_modes_parity
```

- [ ] **Step 3:** Commit.

---

## Task 4: Reference fixtures + parity tests

**Files:**
- Create: `scripts/produce_rust_chunker_batch2_reference.py`
- Create: `rust/chunkshop/tests/parity-fixtures/{fixed_overlap_corpus.txt, fixed_overlap_reference.json, neighbor_expand_corpus.txt, neighbor_expand_reference.json}`
- Create: `rust/chunkshop/tests/fixed_overlap_parity.rs`
- Create: `rust/chunkshop/tests/neighbor_expand_parity.rs`

For the producer script, use a corpus with enough words to exercise the windowing (≥1000 words), and a markdown-with-headings file for neighbor_expand wrapping a hierarchy base. JSON shape mirrors the hierarchy fixture from MB-2.

The integration tests are direct ports of `tests/hierarchy_parity.rs` with the chunker swapped in.

---

## Task 5: README + CHANGELOG

**Files:** `rust/README.md`, `CHANGELOG.md`

Update the chunker row in "What works" and remove fixed_overlap/neighbor_expand from "What does NOT work" / roadmap.

---

## Task 6: ⛔ DC-FINAL + finishing-a-development-branch.
