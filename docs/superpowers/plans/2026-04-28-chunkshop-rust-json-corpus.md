# chunkshop Rust JSON Corpus Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-json-corpus.md` (in this worktree).

**Goal:** Port `JsonCorpusSource` from Python so `chunkshop-rs` accepts YAMLs using `source.type: json_corpus`.

**Architecture:** Add `JsonCorpusSourceConfig` enum variant to `SourceConfig`. Add `JsonCorpusSource` struct in `source.rs` alongside `FilesSource`. Wrap the two sources in an `AnySource` enum (parallel to the `AnyChunker` enum that landed in MB-2) so the runner can dispatch. New integration test against a 3-document JSON fixture.

**Tech Stack:** Rust 2021 + existing deps (`serde_json`).

---

## Task 1: Add config variant + JsonCorpusSource impl + AnySource dispatch

**Files:**
- Modify: `rust/chunkshop/src/config.rs`
- Modify: `rust/chunkshop/src/source.rs`
- Modify: `rust/chunkshop/src/runner.rs`

- [ ] **Step 1: Add config variant**

In `rust/chunkshop/src/config.rs`, find:
```rust
pub enum SourceConfig {
    Files(FilesSourceConfig),
}
```
Replace with:
```rust
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
}
```

Then add the struct (with sensible defaults matching Python's pydantic class):
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct JsonCorpusSourceConfig {
    pub path: String,
    #[serde(default = "default_documents_key")]
    pub documents_key: String,
    #[serde(default = "default_id_field")]
    pub id_field: String,
    #[serde(default = "default_content_field")]
    pub content_field: String,
    #[serde(default = "default_title_field")]
    pub title_field: Option<String>,
}

fn default_documents_key() -> String {
    "documents".to_string()
}
fn default_id_field() -> String {
    "id".to_string()
}
fn default_content_field() -> String {
    "content".to_string()
}
fn default_title_field() -> Option<String> {
    Some("title".to_string())
}
```

- [ ] **Step 2: Implement JsonCorpusSource in source.rs**

Append to `rust/chunkshop/src/source.rs`:
```rust
use crate::config::JsonCorpusSourceConfig;

pub struct JsonCorpusSource {
    cfg: JsonCorpusSourceConfig,
}

impl JsonCorpusSource {
    pub fn new(cfg: JsonCorpusSourceConfig) -> Self {
        Self { cfg }
    }

    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let bytes = std::fs::read(&self.cfg.path)
            .with_context(|| format!("reading {}", self.cfg.path))?;
        let parsed: serde_json::Value = serde_json::from_slice(&bytes)
            .with_context(|| format!("parsing JSON from {}", self.cfg.path))?;
        let arr = parsed
            .get(&self.cfg.documents_key)
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                anyhow!(
                    "no array at key {:?} in {}",
                    self.cfg.documents_key,
                    self.cfg.path
                )
            })?;

        let mut out = Vec::with_capacity(arr.len());
        for (i, row_value) in arr.iter().enumerate() {
            let row = row_value.as_object().ok_or_else(|| {
                anyhow!(
                    "row {i} in {} is not a JSON object",
                    self.cfg.path
                )
            })?;
            let id = row
                .get(&self.cfg.id_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.id_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let content = row
                .get(&self.cfg.content_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.content_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let title = self
                .cfg
                .title_field
                .as_ref()
                .and_then(|tf| row.get(tf).and_then(|v| v.as_str()).map(String::from));
            // Metadata = row keys minus id/content/title fields. Preserve raw
            // JSON values so downstream extractors / promote_metadata can
            // pull typed values.
            let mut meta = serde_json::Map::new();
            for (k, v) in row.iter() {
                if k == &self.cfg.id_field {
                    continue;
                }
                if k == &self.cfg.content_field {
                    continue;
                }
                if let Some(tf) = &self.cfg.title_field {
                    if k == tf {
                        continue;
                    }
                }
                meta.insert(k.clone(), v.clone());
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
            });
        }
        Ok(out)
    }
}
```

- [ ] **Step 3: Add AnySource dispatch in runner.rs**

In `rust/chunkshop/src/runner.rs`, replace:
```rust
use crate::source::FilesSource;
```
with:
```rust
use crate::source::{Document as _SrcDoc, FilesSource, JsonCorpusSource};
```
(The `_SrcDoc` import is unused at runtime — but the existing import of `Document` from MB-2 should already be there. If it isn't, add it; the chunker dispatch from MB-2 takes a `&Document`.)

Replace:
```rust
let source = match cfg.source {
    SourceConfig::Files(fc) => FilesSource::new(fc),
};
```
with:
```rust
let source: AnySource = match cfg.source {
    SourceConfig::Files(fc) => AnySource::Files(FilesSource::new(fc)),
    SourceConfig::JsonCorpus(jc) => AnySource::JsonCorpus(JsonCorpusSource::new(jc)),
};
```

Add the `AnySource` enum near the existing `AnyChunker` enum:
```rust
enum AnySource {
    Files(FilesSource),
    JsonCorpus(JsonCorpusSource),
}

impl AnySource {
    fn iter_documents(&self) -> Result<Vec<crate::source::Document>> {
        match self {
            AnySource::Files(s) => s.iter_documents(),
            AnySource::JsonCorpus(s) => s.iter_documents(),
        }
    }
}
```

- [ ] **Step 4: Build + lib tests**

```bash
cd rust && cargo build --workspace 2>&1 | tail -10
cd rust && cargo test --lib 2>&1 | tail -10
```
Expected: clean.

- [ ] **Step 5: Commit Task 1**

```bash
git add rust/chunkshop/src/config.rs rust/chunkshop/src/source.rs rust/chunkshop/src/runner.rs
git commit -m "feat(rust): port json_corpus source — config + impl + runner dispatch"
```

---

## Task 2: Integration test against a JSON fixture

**Files:**
- Create: `rust/chunkshop/tests/parity-fixtures/json_corpus_sample.json`
- Create: `rust/chunkshop/tests/json_corpus_source.rs`

- [ ] **Step 1: Write the fixture**

Create `rust/chunkshop/tests/parity-fixtures/json_corpus_sample.json`:
```json
{
  "documents": [
    {
      "id": "doc-1",
      "content": "First document body.",
      "title": "First",
      "tags": ["a", "b"],
      "score": 0.91
    },
    {
      "id": "doc-2",
      "content": "Second document body, longer.",
      "title": "Second",
      "tags": ["c"]
    },
    {
      "id": "doc-3",
      "content": "Third.",
      "tags": []
    }
  ],
  "schema_version": 1
}
```

(The third row has no `title` — verifies the optional-title path. The top-level `schema_version` is ignored — verifies we read only `documents`.)

- [ ] **Step 2: Write the test**

Create `rust/chunkshop/tests/json_corpus_source.rs`:
```rust
//! Unit-style integration test for JsonCorpusSource. No Postgres needed.

use std::path::PathBuf;

use chunkshop::config::JsonCorpusSourceConfig;
use chunkshop::source::JsonCorpusSource;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

#[test]
fn reads_3_documents_with_metadata() {
    let path = fixtures_dir().join("json_corpus_sample.json");
    let cfg = JsonCorpusSourceConfig {
        path: path.to_string_lossy().to_string(),
        documents_key: "documents".to_string(),
        id_field: "id".to_string(),
        content_field: "content".to_string(),
        title_field: Some("title".to_string()),
    };
    let source = JsonCorpusSource::new(cfg);
    let docs = source.iter_documents().expect("iter");
    assert_eq!(docs.len(), 3);

    assert_eq!(docs[0].id, "doc-1");
    assert_eq!(docs[0].content, "First document body.");
    assert_eq!(docs[0].title.as_deref(), Some("First"));
    let meta0 = docs[0].metadata.as_object().expect("object");
    // `id`, `content`, `title` removed; `tags` and `score` survive.
    assert!(meta0.get("id").is_none());
    assert!(meta0.get("content").is_none());
    assert!(meta0.get("title").is_none());
    let tags = meta0.get("tags").and_then(|v| v.as_array()).expect("tags array");
    assert_eq!(tags.len(), 2);
    assert_eq!(tags[0].as_str(), Some("a"));
    let score = meta0.get("score").and_then(|v| v.as_f64()).expect("score");
    assert!((score - 0.91).abs() < 1e-9);

    assert_eq!(docs[1].id, "doc-2");
    assert_eq!(docs[1].title.as_deref(), Some("Second"));

    // Third row has no title.
    assert_eq!(docs[2].id, "doc-3");
    assert_eq!(docs[2].title, None);
    let tags2 = docs[2]
        .metadata
        .as_object()
        .and_then(|m| m.get("tags").and_then(|v| v.as_array()))
        .expect("tags array");
    assert_eq!(tags2.len(), 0);
}

#[test]
fn errors_when_documents_key_missing() {
    let path = fixtures_dir().join("json_corpus_sample.json");
    let cfg = JsonCorpusSourceConfig {
        path: path.to_string_lossy().to_string(),
        documents_key: "rows_typo".to_string(),
        id_field: "id".to_string(),
        content_field: "content".to_string(),
        title_field: Some("title".to_string()),
    };
    let source = JsonCorpusSource::new(cfg);
    let err = source.iter_documents().unwrap_err().to_string();
    assert!(err.contains("rows_typo"), "expected key name in error: {err}");
}
```

- [ ] **Step 3: Run**

```bash
cd rust && cargo test --test json_corpus_source -- --nocapture
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/parity-fixtures/json_corpus_sample.json \
        rust/chunkshop/tests/json_corpus_source.rs
git commit -m "test(rust): json_corpus source — fixture + integration test"
```

---

## Task 3: ⛔ DC-001 + DC-FINAL

- [ ] **Step 1:** Re-read `skill-output/mission-brief/Mission-Brief-rust-json-corpus.md`.

- [ ] **Step 2:** Walk every SC with evidence:
```
SC-001 (config variant) — Evidence: ____________________
SC-002 (impl matches Python) — Evidence: cargo test --test json_corpus_source ____________________
SC-003 (runner dispatch) — Evidence: ____________________
SC-004 (integration test) — Evidence: ____________________
SC-005 (no regressions) — Evidence: cargo test --workspace + pytest -q outputs ____________________
SC-006 (README + CHANGELOG) — Evidence: see Task 4 ____________________
```

- [ ] **Step 3:** Run full regression:
```bash
cd rust && cargo test --workspace 2>&1 | tail -10
/home/yonk/yonk-tools/chunkshop/python/.venv/bin/python -m pytest -q /home/yonk/yonk-tools/chunkshop/python/tests 2>&1 | tail -3
```
Expected: all green; pytest still 172/8.

---

## Task 4: README + CHANGELOG

**Files:** `rust/README.md`, `CHANGELOG.md`

- [ ] **Step 1:** In `rust/README.md` "What works", change the source row to:
```markdown
| source    | `files` (glob + `id_from: path \| stem \| sha1`), `json_corpus` (path + `documents_key` + configurable id/content/title field names) |
```
And in "What does NOT work", change:
```markdown
- Sources: `json_corpus`, `pg_table`, `http`, `s3` — not ported.
```
to:
```markdown
- Sources: `pg_table`, `http`, `s3` — not ported.
```
And in the "Implementation roadmap" table, change `Sources (json_corpus, pg_table, http, s3)` to `Sources (pg_table, http, s3)`.

- [ ] **Step 2:** CHANGELOG entry under `## Unreleased / ### Changed`:

```markdown
- **`chunkshop-rs` now ships the `json_corpus` source** — same shape as
  Python: reads a JSON file, takes the array under `documents_key` (default
  `"documents"`), pulls `id` / `content` / `title` from configured fields,
  and stuffs the remaining row keys into `metadata` as raw JSON values
  (preserving types for downstream `promote_metadata` casts). Verified by
  `rust/chunkshop/tests/json_corpus_source.rs`. With this, any Python YAML
  with `source.type: json_corpus` runs unchanged on Rust.
```

- [ ] **Step 3:** Commit.

```bash
git add rust/README.md CHANGELOG.md
git commit -m "docs(rust): json_corpus source shipped — README + CHANGELOG"
```

---

## Task 5: Hand off to finishing-a-development-branch.
