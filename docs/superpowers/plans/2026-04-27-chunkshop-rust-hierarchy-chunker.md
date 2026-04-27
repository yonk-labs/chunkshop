# chunkshop Rust Hierarchy Chunker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-hierarchy.md` (in this worktree).

**Goal:** Port `HierarchyChunker` from `python/src/chunkshop/chunkers/hierarchy.py` to Rust so `chunkshop-rs` can run Python's shipped default config (`hierarchy + int8 bge-base`).

**Architecture:** New `HierarchyChunker` struct in `rust/chunkshop/src/chunker.rs` (alongside `SentenceAwareChunker`). New `HierarchyChunkerConfig` variant of `ChunkerConfig`. Reuses the existing `split_to_max_chars` helper (already ported, used by `sentence_aware`). Cross-language byte-identical chunk text proven by a new integration test against committed Python-produced reference fixtures.

**Tech Stack:** Rust 2021 + the `regex` crate (already a dep) + `serde_json` for metadata. Python 3.12 (reference fixture producer only).

---

## Task 1: Add `HierarchyChunkerConfig` and the enum variant

**Files:** `rust/chunkshop/src/config.rs`

- [ ] **Step 1: Add the struct + variant**

In `rust/chunkshop/src/config.rs`, find the existing `ChunkerConfig` enum and `SentenceAwareChunkerConfig`. Add:

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct HierarchyChunkerConfig {
    #[serde(default = "default_prefix_heading")]
    pub prefix_heading: bool,
    #[serde(default = "default_min_section_chars")]
    pub min_section_chars: usize,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
}

fn default_prefix_heading() -> bool {
    true
}
fn default_min_section_chars() -> usize {
    100
}
```

Then extend the enum:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
}
```

(`default_max_chars` already exists at 2000 — share it.)

- [ ] **Step 2: Verify config parses**

```bash
cd rust && cargo build --workspace
```
Expected: clean build, rc=0.

- [ ] **Step 3: Quick parse test**

Add a unit test in `config.rs` (at the bottom):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_hierarchy_chunker() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: hierarchy, prefix_heading: false, max_chars: 500 }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { dsn_env: D, schema: s, table: t, mode: overwrite, hnsw: false }
"#;
        let cfg: CellConfig = serde_yml::from_str(yaml).expect("parse");
        match cfg.chunker {
            ChunkerConfig::Hierarchy(h) => {
                assert!(!h.prefix_heading);
                assert_eq!(h.max_chars, 500);
                assert_eq!(h.min_section_chars, 100);
            }
            _ => panic!("expected hierarchy"),
        }
    }
}
```

(Add to existing `#[cfg(test)] mod tests {}` block in `config.rs` if one exists; otherwise create.)

- [ ] **Step 4: Run unit tests**

```bash
cd rust && cargo test --lib
```
Expected: pass including the new `parses_hierarchy_chunker`.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/config.rs
git commit -m "feat(rust): add HierarchyChunkerConfig + ChunkerConfig::Hierarchy variant"
```

---

## Task 2: Produce Python reference fixtures

**Files:**
- Create: `scripts/produce_rust_hierarchy_reference.py`
- Create: `rust/chunkshop/tests/parity-fixtures/hierarchy_corpus.txt` (a multi-section markdown doc covering edge cases)
- Create: `rust/chunkshop/tests/parity-fixtures/hierarchy_reference.json` (Python's chunked output)

The fixture format is JSON for easy parsing on both sides:

```json
{
  "doc_id": "fixture",
  "doc_title": null,
  "config": {"prefix_heading": true, "min_section_chars": 100, "max_chars": 2000},
  "chunks": [
    {"seq_num": 0, "original_content": "...", "embedded_content": "...",
     "heading": "Section A", "section_part": 0}
  ]
}
```

- [ ] **Step 1: Pick a corpus that exercises the edge cases**

Create `rust/chunkshop/tests/parity-fixtures/hierarchy_corpus.txt`:

```markdown
# Top-Level Title

Intro paragraph that comes before the first nested heading. It is long enough to clear the default min_section_chars threshold of 100. Two sentences.

## Section A

Body of Section A. This section is short on purpose to exercise normal flow.

## Section B

Body of Section B. Longer than A so that the section_part metadata reads zero throughout because we stay under max_chars.

### Section B.1

Subsection body. Still under max_chars.

## Section Tiny

Below threshold.

## Section Big

This section is going to be padded so it exceeds the configured max_chars and triggers split_to_max_chars recursion. Repeat content next: Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Lorem ipsum dolor sit amet, consectetur adipiscing elit.
```

- [ ] **Step 2: Write the producer script**

Create `scripts/produce_rust_hierarchy_reference.py`:

```python
"""Produce reference chunks for the Rust hierarchy chunker parity test.

Run from the chunkshop repo root:
    uv run --project python python scripts/produce_rust_hierarchy_reference.py
"""
from __future__ import annotations

import json
from pathlib import Path

from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.config import HierarchyChunker as Cfg
from chunkshop.sources.base import Document


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"


def main() -> int:
    corpus_path = FIXTURE_DIR / "hierarchy_corpus.txt"
    out_path = FIXTURE_DIR / "hierarchy_reference.json"

    text = corpus_path.read_text(encoding="utf-8")
    cfg = Cfg(type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400)
    chunker = HierarchyChunker(cfg)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)

    payload = {
        "doc_id": doc.id,
        "doc_title": doc.title,
        "config": {
            "prefix_heading": cfg.prefix_heading,
            "min_section_chars": cfg.min_section_chars,
            "max_chars": cfg.max_chars,
        },
        "chunks": [
            {
                "seq_num": c.seq_num,
                "original_content": c.original_content,
                "embedded_content": c.embedded_content,
                "heading": c.metadata.get("heading", ""),
                "section_part": c.metadata.get("section_part", 0),
            }
            for c in chunks
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out_path} ({len(chunks)} chunks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`max_chars=400` is small on purpose — forces the "Section Big" to recurse through `split_to_max_chars`.

- [ ] **Step 3: Run it once**

```bash
cd /home/yonk/yonk-tools/chunkshop-rust-hierarchy
uv run --project python python scripts/produce_rust_hierarchy_reference.py
```

Expected: prints `wrote .../hierarchy_reference.json (N chunks)` where N ≥ 5 (one for the intro, one each for A/B/B.1, multiple for Big). Inspect the JSON file to sanity-check the chunk shape.

- [ ] **Step 4: Commit fixtures + producer**

```bash
git add scripts/produce_rust_hierarchy_reference.py \
        rust/chunkshop/tests/parity-fixtures/hierarchy_corpus.txt \
        rust/chunkshop/tests/parity-fixtures/hierarchy_reference.json
git commit -m "test(rust): commit Python hierarchy reference fixtures + producer"
```

---

## Task 3: Add the failing parity test

**Files:** `rust/chunkshop/tests/hierarchy_parity.rs`

- [ ] **Step 1: Write the test**

Create `rust/chunkshop/tests/hierarchy_parity.rs`:

```rust
//! Cross-language byte-identical chunk parity for HierarchyChunker.
//!
//! Loads `tests/parity-fixtures/hierarchy_corpus.txt` (markdown source) and
//! `hierarchy_reference.json` (Python's chunked output), runs the Rust
//! HierarchyChunker, and asserts every chunk's `original_content`,
//! `embedded_content`, `heading` metadata, and `section_part` match exactly.

use std::path::PathBuf;

use chunkshop::chunker::HierarchyChunker;
use chunkshop::config::HierarchyChunkerConfig;
use chunkshop::source::Document;
use serde::Deserialize;
use serde_json::json;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[derive(Debug, Deserialize)]
struct RefChunk {
    seq_num: usize,
    original_content: String,
    embedded_content: String,
    heading: String,
    section_part: usize,
}

#[derive(Debug, Deserialize)]
struct RefConfig {
    prefix_heading: bool,
    min_section_chars: usize,
    max_chars: usize,
}

#[derive(Debug, Deserialize)]
struct Reference {
    doc_id: String,
    #[serde(default)]
    doc_title: Option<String>,
    config: RefConfig,
    chunks: Vec<RefChunk>,
}

#[test]
fn rust_hierarchy_chunks_match_python() {
    let corpus = std::fs::read_to_string(fixtures_dir().join("hierarchy_corpus.txt"))
        .expect("read corpus");
    let ref_json = std::fs::read_to_string(fixtures_dir().join("hierarchy_reference.json"))
        .expect("read reference");
    let r: Reference = serde_json::from_str(&ref_json).expect("parse reference");

    let cfg = HierarchyChunkerConfig {
        prefix_heading: r.config.prefix_heading,
        min_section_chars: r.config.min_section_chars,
        max_chars: r.config.max_chars,
    };
    let doc = Document {
        id: r.doc_id.clone(),
        content: corpus,
        title: r.doc_title.clone(),
        metadata: json!({}),
    };
    let chunker = HierarchyChunker::new(cfg);
    let actual = chunker.chunk(&doc);

    assert_eq!(
        actual.len(),
        r.chunks.len(),
        "chunk count mismatch: rust={}, python={}",
        actual.len(),
        r.chunks.len()
    );
    for (i, (got, exp)) in actual.iter().zip(r.chunks.iter()).enumerate() {
        assert_eq!(got.seq_num, exp.seq_num, "chunk[{i}] seq_num");
        assert_eq!(
            got.original_content, exp.original_content,
            "chunk[{i}] original_content"
        );
        assert_eq!(
            got.embedded_content, exp.embedded_content,
            "chunk[{i}] embedded_content"
        );
        let h = got
            .metadata
            .get("heading")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let sp = got
            .metadata
            .get("section_part")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as usize;
        assert_eq!(h, exp.heading, "chunk[{i}] heading");
        assert_eq!(sp, exp.section_part, "chunk[{i}] section_part");
    }
}
```

- [ ] **Step 2: Compile, expect FAIL**

```bash
cd rust && cargo test --test hierarchy_parity --no-run 2>&1 | tail -10
```

Expected: compile error — `HierarchyChunker` and `HierarchyChunkerConfig` don't exist yet (they exist as `Hierarchy(...)` enum variant content but no concrete struct). This RED state confirms the test references the not-yet-built types.

If it compiles, it'd panic on `chunker.chunk(&doc)` because `HierarchyChunker::new(cfg)` doesn't exist yet. Either way: RED.

(No commit yet — this test stays uncommitted until Task 4 adds the impl. Or commit and accept a known-failing build for one task. Choose the cleaner path.)

---

## Task 4: Implement `HierarchyChunker`

**Files:** `rust/chunkshop/src/chunker.rs`

- [ ] **Step 1: Add the heading-with-text regex**

In `rust/chunkshop/src/chunker.rs`, find `md_heading_re()`. The existing regex is `r"(?m)^#{1,6}\s+.+$"` — matches the whole heading line. The hierarchy chunker needs the heading **text** (group 2 in Python's `r"^(#{1,6})\s+(.+?)$"`). Add a new helper:

```rust
fn heading_with_text_re() -> Regex {
    // Python: r"^(#{1,6})\s+(.+?)$" with re.MULTILINE.
    Regex::new(r"(?m)^(#{1,6})\s+(.+?)$").unwrap()
}
```

- [ ] **Step 2: Add `HierarchyChunker` and the helper `_emit_section_chunks`**

Append to `rust/chunkshop/src/chunker.rs`:

```rust
use crate::config::HierarchyChunkerConfig;

pub struct HierarchyChunker {
    cfg: HierarchyChunkerConfig,
}

impl HierarchyChunker {
    pub fn new(cfg: HierarchyChunkerConfig) -> Self {
        Self { cfg }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let text = &doc.content;
        let re = heading_with_text_re();
        let headings: Vec<(usize, usize, String)> = re
            .captures_iter(text)
            .map(|c| {
                let m0 = c.get(0).unwrap();
                let h_text = c.get(2).map(|m| m.as_str().trim().to_string()).unwrap_or_default();
                (m0.start(), m0.end(), h_text)
            })
            .collect();

        // No headings: emit body as a single (split if needed) chunk using
        // doc.title as the heading prefix.
        if headings.is_empty() {
            let body = text.trim();
            if body.is_empty() {
                return Vec::new();
            }
            let title = doc.title.clone().unwrap_or_default();
            return self.emit_section_chunks(body, &title, &doc.id, 0);
        }

        let mut chunks: Vec<Chunk> = Vec::new();

        // Pre-heading prefix.
        if headings[0].0 > 0 {
            let body = text[..headings[0].0].trim();
            if body.chars().count() >= self.cfg.min_section_chars {
                let title = doc.title.clone().unwrap_or_default();
                let start_seq = chunks.len();
                chunks.extend(self.emit_section_chunks(body, &title, &doc.id, start_seq));
            }
        }

        for (i, (_h_start, h_end, h_text)) in headings.iter().enumerate() {
            let start = *h_end;
            let end = if i + 1 < headings.len() {
                headings[i + 1].0
            } else {
                text.len()
            };
            let body = text[start..end].trim();
            if body.chars().count() < self.cfg.min_section_chars {
                continue;
            }
            let start_seq = chunks.len();
            chunks.extend(self.emit_section_chunks(body, h_text, &doc.id, start_seq));
        }

        chunks
    }

    fn emit_section_chunks(
        &self,
        body: &str,
        heading_text: &str,
        doc_id: &str,
        start_seq: usize,
    ) -> Vec<Chunk> {
        let parts: Vec<String> = if body.chars().count() > self.cfg.max_chars {
            split_to_max_chars(body, self.cfg.max_chars)
        } else {
            vec![body.to_string()]
        };
        parts
            .into_iter()
            .enumerate()
            .map(|(i, part)| {
                let embedded = if !heading_text.is_empty() && self.cfg.prefix_heading {
                    format!("{heading_text}\n\n{part}")
                } else {
                    part.clone()
                };
                Chunk {
                    doc_id: doc_id.to_string(),
                    seq_num: start_seq + i,
                    original_content: part,
                    embedded_content: embedded,
                    metadata: json!({
                        "strategy": "hierarchy",
                        "heading": heading_text,
                        "section_part": i,
                    }),
                }
            })
            .collect()
    }
}
```

- [ ] **Step 3: Run the parity test**

```bash
cd rust && cargo test --test hierarchy_parity -- --nocapture
```

Expected: PASS. If it fails on a specific chunk, the failure message names which field (original_content/embedded_content/heading/section_part) and which index. Fix by comparing against Python's logic line-by-line in `python/src/chunkshop/chunkers/hierarchy.py`.

- [ ] **Step 4: Commit Tasks 3+4 together**

```bash
git add rust/chunkshop/src/chunker.rs rust/chunkshop/tests/hierarchy_parity.rs
git commit -m "feat(rust): port HierarchyChunker — byte-identical chunks vs Python"
```

---

## Task 5: ⛔ Drift Check DC-001 + DC-002

- [ ] **Step 1:** Re-read `skill-output/mission-brief/Mission-Brief-rust-hierarchy.md`.

- [ ] **Step 2:** Verify scope. We added:
  - `HierarchyChunkerConfig` + the enum variant.
  - `HierarchyChunker` impl in chunker.rs.
  - One parity test.
  - Python fixture producer + fixtures.
  - **Nothing else.** No other chunker, no Python changes, no `split_to_max_chars` modifications.

- [ ] **Step 3:** Verify embedded_content format matches Python's `f"{heading_text}\n\n{part}"` — eyeball one chunk in `rust/chunkshop/tests/parity-fixtures/hierarchy_reference.json` and confirm the Rust chunk's `embedded_content` matches. The parity test does this automatically; this step is a manual sanity belt-and-suspenders.

- [ ] **Step 4:** If any drift, stop and report.

---

## Task 6: Wire `HierarchyChunker` into the runner

**Files:** `rust/chunkshop/src/runner.rs`

- [ ] **Step 1:** Read `runner.rs` to find where `ChunkerConfig` is matched and a chunker constructed.

```bash
grep -n "ChunkerConfig\|SentenceAwareChunker" rust/chunkshop/src/runner.rs
```

- [ ] **Step 2:** Add the `Hierarchy` arm. The dispatch likely looks like:

```rust
let chunker: Box<dyn ...> = match cfg.chunker.clone() {
    ChunkerConfig::SentenceAware(c) => Box::new(SentenceAwareChunker::new(c)),
    ChunkerConfig::Hierarchy(c) => Box::new(HierarchyChunker::new(c)),
};
```

If `runner.rs` uses an enum-of-chunkers approach instead of a trait, adapt accordingly. The two chunkers must produce `Vec<Chunk>` from `&Document` either way; introduce a small `Chunker` trait if `runner.rs` doesn't have one yet.

- [ ] **Step 3:** Build + existing parity test.

```bash
cd rust && cargo test --workspace 2>&1 | tail -10
```

Expected: all tests pass, including the existing `tests/parity.rs` (which uses sentence_aware — must not regress) and the new `hierarchy_parity`.

- [ ] **Step 4:** Commit.

```bash
git add rust/chunkshop/src/runner.rs
git commit -m "feat(rust): wire HierarchyChunker into runner dispatch"
```

---

## Task 7: Cross-language E2E with `scripts/parity_check.py`

**Files:** `scripts/parity_check.py` (modify temporarily to run with `chunker.type: hierarchy` — or fork into a sibling script)

- [ ] **Step 1:** Edit `scripts/parity_check.py`'s `write_config()` to use `hierarchy`:

Find the line `"  type: sentence_aware\n"` in the chunker block and either change it to `"  type: hierarchy\n"` (and adjust max_chars / drop min_chars), or add a `--chunker` flag. The simpler path: hardcode `hierarchy` since the script's job is to prove cross-language parity on the canonical default.

- [ ] **Step 2:** Run end-to-end (Postgres at `localhost:5434/age_bakeoff_pgrg`):

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
cd rust && cargo build --release && cd ..
uv run --project python python scripts/parity_check.py
```

Expected: report shows top-1 match True, content match 100%, max cos dist within MB-1's envelope.

- [ ] **Step 3:** Commit if you modified the script.

```bash
git add scripts/parity_check.py
git commit -m "test(rust): parity_check.py now exercises the hierarchy chunker"
```

---

## Task 8: Update README + CHANGELOG

**Files:** `rust/README.md`, `CHANGELOG.md`

- [ ] **Step 1:** In `rust/README.md`, find the "What works" table and add `hierarchy` next to `sentence_aware`. Also remove `hierarchy` from the "What does NOT work" list, and remove the matching row from the "Implementation roadmap" table.

- [ ] **Step 2:** In `CHANGELOG.md`, append under `## Unreleased / ### Changed`:

```markdown
- **`chunkshop-rs` now ships the `hierarchy` chunker** — Python's shipped
  default. Same logic (heading-prefix prepended to `embedded_content`,
  per-section_part metadata, `min_section_chars` filter, `split_to_max_chars`
  recursion for oversized sections). Cross-language byte-identical chunk
  text verified by `rust/chunkshop/tests/hierarchy_parity.rs` against a
  committed Python reference. With this + the int8 BGE embedder parity
  (above), the canonical `hierarchy + bge-base-int8` config now runs on
  Rust and produces retrieval-equivalent output to Python.
```

- [ ] **Step 3:** Commit.

```bash
git add rust/README.md CHANGELOG.md
git commit -m "docs(rust): hierarchy chunker shipped — update README + CHANGELOG"
```

---

## Task 9: ⛔ DC-FINAL — verify all SC met

- [ ] **Step 1:** Re-read `skill-output/mission-brief/Mission-Brief-rust-hierarchy.md`.

- [ ] **Step 2:** Walk through every SC and write evidence:

```
SC-001 (HierarchyChunkerConfig + variant) — Evidence: ____________________
SC-002 (HierarchyChunker impl) — Evidence: ____________________
SC-003 (parity test passes) — Evidence: cargo test --test hierarchy_parity output: ____________________
SC-004 (runner dispatch) — Evidence: ____________________
SC-005 (no regressions) — Evidence: cargo test --workspace + pytest -q outputs: ____________________
SC-006 (parity_check.py with hierarchy works) — Evidence: ____________________
SC-007 (README + CHANGELOG) — Evidence: ____________________
```

- [ ] **Step 3:** Final tree state check.

```bash
git status --short
git log --oneline main..HEAD
```

- [ ] **Step 4:** Hand off to `superpowers:finishing-a-development-branch`.

---

## Self-review notes

- **Spec coverage:** SC-001 → Task 1; SC-002/003 → Tasks 3+4; SC-004 → Task 6; SC-005 → Tasks 4+6 (regression in Task 6); SC-006 → Task 7; SC-007 → Task 8. Each DC-XXX is a labeled task (DC-001/002 → Task 5; DC-FINAL → Task 9).
- **No placeholders:** every code step has actual code or an actual command + expected output.
- **Type consistency:** `HierarchyChunkerConfig`, `HierarchyChunker`, `Chunk`, `Document` referenced consistently across Tasks 1, 3, 4, 6.
- **Reuse over re-port:** `split_to_max_chars` is reused from `chunker.rs`, not re-ported. `md_heading_re()` is supplemented (not replaced) with a new `heading_with_text_re()` because hierarchy needs capture groups.
- **No Python changes** beyond the one-time fixture producer.
