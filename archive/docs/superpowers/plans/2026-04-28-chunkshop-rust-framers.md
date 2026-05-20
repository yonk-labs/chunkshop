# chunkshop Rust Framer Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-framers.md`.

**Goal:** Port the framer stage (4 framers + pipeline integration) from Python to Rust.

**Architecture:** New `framer.rs` module with `FramerImpl` trait + 4 implementations. New `FramerConfig` enum on `CellConfig` (default = identity). Runner inserts a frame() call between source.iter_documents() and chunker.chunk(). Two cross-language parity tests against committed Python references.

**Tech Stack:** Rust 2021 + existing deps (`regex`, `serde_json`).

---

## Task 1: Add FramerConfig + 4 variants

**Files:** `rust/chunkshop/src/config.rs`

- [ ] **Step 1:** Add the enum + structs (after the chunker block):

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FramerConfig {
    Identity(IdentityFramerConfig),
    HeadingBoundary(HeadingBoundaryFramerConfig),
    RegexBoundary(RegexBoundaryFramerConfig),
    Jsonpath(JsonPathFramerConfig),
}

impl Default for FramerConfig {
    fn default() -> Self {
        FramerConfig::Identity(IdentityFramerConfig {})
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct IdentityFramerConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct HeadingBoundaryFramerConfig {
    #[serde(default = "default_heading_pattern")]
    pub pattern: String,
    #[serde(default = "default_true")]
    pub title_from_heading: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegexBoundaryFramerConfig {
    pub split_pattern: String,
    #[serde(default)]
    pub title_pattern: Option<String>,
    #[serde(default = "default_true")]
    pub body_starts_with_match: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct JsonPathFramerConfig {
    pub row_path: String,
    #[serde(default)]
    pub title_path: Option<String>,
    #[serde(default = "default_jsonpath_body")]
    pub body_path: String,
}

fn default_heading_pattern() -> String { r"^#+\s".to_string() }
fn default_true() -> bool { true }
fn default_jsonpath_body() -> String { "$".to_string() }
```

- [ ] **Step 2:** Replace the existing `pub framer: Option<serde_yml::Value>` field on `CellConfig` with:

```rust
#[serde(default)]
pub framer: FramerConfig,
```

(Drop the `#[allow(dead_code)]` and `skip_serializing` attributes.)

- [ ] **Step 3:** Build:
```bash
cd rust && cargo build --workspace
```
Expected: clean.

- [ ] **Step 4:** Add a unit test confirming parse:

```rust
#[test]
fn parses_default_identity_framer() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { dsn_env: D, schema: s, table: t, mode: overwrite, hnsw: false }
"#;
    let path = write_yaml(yaml);
    let cfg = load_config(&path).expect("load");
    assert!(matches!(cfg.framer, FramerConfig::Identity(_)));
}

#[test]
fn parses_heading_boundary_framer() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
framer: { type: heading_boundary, pattern: "^##\\s", title_from_heading: true }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { dsn_env: D, schema: s, table: t, mode: overwrite, hnsw: false }
"#;
    let path = write_yaml(yaml);
    let cfg = load_config(&path).expect("load");
    match cfg.framer {
        FramerConfig::HeadingBoundary(h) => {
            assert_eq!(h.pattern, "^##\\s");
            assert!(h.title_from_heading);
        }
        _ => panic!("expected heading_boundary"),
    }
}
```

- [ ] **Step 5:** `cargo test --lib` — pass. Commit.

---

## Task 2: Implement FramerImpl + 4 framers

**Files:**
- Create: `rust/chunkshop/src/framer.rs`
- Modify: `rust/chunkshop/src/lib.rs` (declare new module).

- [ ] **Step 1:** Create `framer.rs`:

```rust
//! Framer stage. Sits between source and chunker. Each framer's `frame(&raw)`
//! returns 1+ framed Documents. Each framed doc carries `metadata.framer` and
//! `metadata.frame_seq`. Mirrors `python/src/chunkshop/framers/`.

use anyhow::{anyhow, Result};
use regex::Regex;
use serde_json::Value;

use crate::config::{
    HeadingBoundaryFramerConfig, IdentityFramerConfig, JsonPathFramerConfig,
    RegexBoundaryFramerConfig,
};
use crate::source::Document;

pub trait FramerImpl {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>>;
}

fn stamp_meta(meta: &Value, framer: &str, frame_seq: usize) -> Value {
    let mut m = meta.as_object().cloned().unwrap_or_default();
    m.insert("framer".to_string(), Value::String(framer.to_string()));
    m.insert("frame_seq".to_string(), Value::from(frame_seq as u64));
    Value::Object(m)
}

pub struct IdentityFramer;

impl IdentityFramer {
    pub fn new(_cfg: IdentityFramerConfig) -> Self { Self }
}

impl FramerImpl for IdentityFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        Ok(vec![Document {
            id: raw.id.clone(),
            content: raw.content.clone(),
            title: raw.title.clone(),
            metadata: stamp_meta(&raw.metadata, "identity", 0),
        }])
    }
}

pub struct HeadingBoundaryFramer {
    cfg: HeadingBoundaryFramerConfig,
    heading_re: Regex,
    pattern_re: Regex,
}

impl HeadingBoundaryFramer {
    pub fn new(cfg: HeadingBoundaryFramerConfig) -> Result<Self> {
        // Python builds: re.compile(cfg.pattern + r".+$", re.MULTILINE).
        let heading_re = Regex::new(&format!("(?m){}.+$", cfg.pattern))
            .map_err(|e| anyhow!("invalid heading pattern: {e}"))?;
        let pattern_re = Regex::new(&cfg.pattern)
            .map_err(|e| anyhow!("invalid pattern: {e}"))?;
        Ok(Self { cfg, heading_re, pattern_re })
    }
}

impl FramerImpl for HeadingBoundaryFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        let content = &raw.content;
        let matches: Vec<(usize, usize)> = self
            .heading_re
            .find_iter(content)
            .map(|m| (m.start(), m.end()))
            .collect();

        if matches.is_empty() {
            return Ok(vec![Document {
                id: raw.id.clone(),
                content: content.clone(),
                title: raw.title.clone(),
                metadata: stamp_meta(&raw.metadata, "heading_boundary", 0),
            }]);
        }

        let mut frames: Vec<Document> = Vec::new();

        // Preamble before first heading.
        if matches[0].0 > 0 {
            let preamble = content[..matches[0].0].trim();
            if !preamble.is_empty() {
                let frame_seq = frames.len();
                frames.push(Document {
                    id: format!("{}#{}", raw.id, frame_seq),
                    content: preamble.to_string(),
                    title: raw.title.clone(),
                    metadata: stamp_meta(&raw.metadata, "heading_boundary", frame_seq),
                });
            }
        }

        // One frame per heading-delimited section.
        for (i, (h_start, h_end)) in matches.iter().enumerate() {
            let start = *h_end;
            let end = if i + 1 < matches.len() {
                matches[i + 1].0
            } else {
                content.len()
            };
            let heading_line = content[*h_start..*h_end].trim().to_string();
            // Strip the pattern prefix to extract the heading text.
            let heading_text = self.pattern_re.replace(&heading_line, "").trim().to_string();
            let body = content[start..end].trim();
            let full = if body.is_empty() {
                heading_line.clone()
            } else {
                format!("{heading_line}\n\n{body}")
            };
            let frame_seq = frames.len();
            let title = if self.cfg.title_from_heading {
                Some(heading_text)
            } else {
                raw.title.clone()
            };
            frames.push(Document {
                id: format!("{}#{}", raw.id, frame_seq),
                content: full,
                title,
                metadata: stamp_meta(&raw.metadata, "heading_boundary", frame_seq),
            });
        }
        Ok(frames)
    }
}

pub struct RegexBoundaryFramer {
    cfg: RegexBoundaryFramerConfig,
    split_re: Regex,
    title_re: Option<Regex>,
}

impl RegexBoundaryFramer {
    pub fn new(cfg: RegexBoundaryFramerConfig) -> Result<Self> {
        let split_re = Regex::new(&format!("(?m){}", cfg.split_pattern))
            .map_err(|e| anyhow!("invalid split_pattern: {e}"))?;
        let title_re = cfg
            .title_pattern
            .as_ref()
            .map(|p| Regex::new(p).map_err(|e| anyhow!("invalid title_pattern: {e}")))
            .transpose()?;
        Ok(Self { cfg, split_re, title_re })
    }
}

impl FramerImpl for RegexBoundaryFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        let content = &raw.content;
        let matches: Vec<(usize, usize)> = self
            .split_re
            .find_iter(content)
            .map(|m| (m.start(), m.end()))
            .collect();

        if matches.is_empty() {
            return Ok(vec![Document {
                id: raw.id.clone(),
                content: content.clone(),
                title: raw.title.clone(),
                metadata: stamp_meta(&raw.metadata, "regex_boundary", 0),
            }]);
        }

        let mut frames: Vec<Document> = Vec::new();
        for (i, (m_start, m_end)) in matches.iter().enumerate() {
            let start = if self.cfg.body_starts_with_match { *m_start } else { *m_end };
            let end = if i + 1 < matches.len() {
                matches[i + 1].0
            } else {
                content.len()
            };
            let body = content[start..end].trim().to_string();
            if body.is_empty() {
                continue;
            }
            let mut title = raw.title.clone();
            if let Some(re) = &self.title_re {
                if let Some(c) = re.captures(&body) {
                    if let Some(g) = c.get(1) {
                        title = Some(g.as_str().trim().to_string());
                    }
                }
            }
            let frame_seq = frames.len();
            frames.push(Document {
                id: format!("{}#{}", raw.id, frame_seq),
                content: body,
                title,
                metadata: stamp_meta(&raw.metadata, "regex_boundary", frame_seq),
            });
        }
        Ok(frames)
    }
}

pub struct JsonPathFramer {
    cfg: JsonPathFramerConfig,
    row_parts: Vec<String>,
    body_parts: Vec<String>,
    title_parts: Option<Vec<String>>,
}

impl JsonPathFramer {
    pub fn new(cfg: JsonPathFramerConfig) -> Self {
        fn parts(p: &str) -> Vec<String> {
            if p == "$" { Vec::new() } else { p.split('.').map(String::from).collect() }
        }
        let row_parts = parts(&cfg.row_path);
        let body_parts = parts(&cfg.body_path);
        let title_parts = cfg.title_path.as_ref().map(|p| parts(p));
        Self { cfg, row_parts, body_parts, title_parts }
    }

    fn walk<'a>(obj: &'a Value, parts: &[String]) -> Vec<&'a Value> {
        if parts.is_empty() {
            return vec![obj];
        }
        let head = &parts[0];
        let rest = &parts[1..];
        if head == "*" {
            let Some(arr) = obj.as_array() else { return Vec::new() };
            let mut out = Vec::new();
            for item in arr {
                out.extend(Self::walk(item, rest));
            }
            return out;
        }
        if let Some(o) = obj.as_object() {
            if let Some(v) = o.get(head) {
                return Self::walk(v, rest);
            }
        }
        Vec::new()
    }
}

impl FramerImpl for JsonPathFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        let parsed: Value = serde_json::from_str(&raw.content)
            .map_err(|e| anyhow!("JSONPathFramer: raw.content is not valid JSON: {e}"))?;
        let rows: Vec<&Value> = if self.row_parts.is_empty() {
            vec![&parsed]
        } else {
            Self::walk(&parsed, &self.row_parts)
        };

        let mut frames: Vec<Document> = Vec::new();
        for row in rows {
            let body_values: Vec<&Value> = if self.body_parts.is_empty() {
                vec![row]
            } else {
                Self::walk(row, &self.body_parts)
            };
            if body_values.is_empty() {
                continue;
            }
            let body_value = body_values[0];
            let body = if let Some(s) = body_value.as_str() {
                s.to_string()
            } else {
                serde_json::to_string(body_value).unwrap_or_default()
            };
            let mut title = raw.title.clone();
            if let Some(tp) = &self.title_parts {
                let tvs = Self::walk(row, tp);
                if let Some(t) = tvs.first() {
                    if let Some(s) = t.as_str() {
                        title = Some(s.to_string());
                    }
                }
            }
            let frame_seq = frames.len();
            frames.push(Document {
                id: format!("{}#{}", raw.id, frame_seq),
                content: body,
                title,
                metadata: stamp_meta(&raw.metadata, "jsonpath", frame_seq),
            });
        }
        Ok(frames)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn doc(id: &str, content: &str) -> Document {
        Document { id: id.into(), content: content.into(), title: None, metadata: json!({}) }
    }

    #[test]
    fn identity_returns_one_frame_with_meta() {
        let f = IdentityFramer::new(IdentityFramerConfig {});
        let frames = f.frame(&doc("d", "body")).unwrap();
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].id, "d");
        assert_eq!(frames[0].metadata["framer"], "identity");
        assert_eq!(frames[0].metadata["frame_seq"], 0);
    }

    #[test]
    fn regex_boundary_no_matches_returns_one_frame() {
        let cfg = RegexBoundaryFramerConfig {
            split_pattern: r"^---$".to_string(),
            title_pattern: None,
            body_starts_with_match: true,
        };
        let f = RegexBoundaryFramer::new(cfg).unwrap();
        let frames = f.frame(&doc("d", "no separators here")).unwrap();
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].metadata["framer"], "regex_boundary");
    }
}
```

- [ ] **Step 2:** Add `pub mod framer;` to `lib.rs` alongside the other modules.

- [ ] **Step 3:** `cargo build --workspace` + `cargo test --lib`. Both clean.

- [ ] **Step 4:** Commit.

---

## Task 3: Wire framer into the runner

**Files:** `rust/chunkshop/src/runner.rs`

- [ ] **Step 1:** Add `build_framer()` parallel to `build_chunker()`:

```rust
use crate::config::FramerConfig;
use crate::framer::{
    FramerImpl, HeadingBoundaryFramer, IdentityFramer, JsonPathFramer, RegexBoundaryFramer,
};

fn build_framer(cfg: FramerConfig) -> Result<Box<dyn FramerImpl + Send + Sync>> {
    Ok(match cfg {
        FramerConfig::Identity(c) => Box::new(IdentityFramer::new(c)),
        FramerConfig::HeadingBoundary(c) => Box::new(HeadingBoundaryFramer::new(c)?),
        FramerConfig::RegexBoundary(c) => Box::new(RegexBoundaryFramer::new(c)?),
        FramerConfig::Jsonpath(c) => Box::new(JsonPathFramer::new(c)),
    })
}
```

- [ ] **Step 2:** In `run_cell`, after building source/chunker/embedder, build framer, and change the doc-iteration loop:

```rust
let framer = build_framer(cfg.framer)?;
// ...
for raw in raw_docs.into_iter().take(limit) {
    let framed_docs = framer.frame(&raw)?;
    for doc in framed_docs {
        let chunks = chunker.chunk(&doc);
        // ...embed + sink as before
        docs_processed += 1;  // count framed, not raw, to match Python's docs_processed semantics
    }
}
```

(Keep the existing heartbeat logic; it counts iterations, framing increases iteration count which is the semantically correct thing.)

- [ ] **Step 3:** `cargo build --workspace` + `cargo test --workspace`. The existing `cross_language_append_with_promote_column` test must still pass — it uses default identity framer, so the new pipeline shape shouldn't change its output.

- [ ] **Step 4:** Commit.

---

## Task 4: ⛔ DC-001 + DC-002

- [ ] Re-read brief. Confirm scope: 4 framers + pipeline integration. Verify `cross_language_append_with_promote_column` still GREEN.

---

## Task 5: Cross-language parity tests for heading_boundary + jsonpath

**Files:**
- Create: `scripts/produce_rust_framer_reference.py`
- Create: `rust/chunkshop/tests/parity-fixtures/{framer_heading_corpus.md, framer_heading_reference.json, framer_jsonpath_corpus.json, framer_jsonpath_reference.json}`
- Create: `rust/chunkshop/tests/heading_boundary_parity.rs`
- Create: `rust/chunkshop/tests/jsonpath_parity.rs`

The producer script runs Python's `HeadingBoundaryFramer` and `JSONPathFramer` against committed corpora and dumps the framed Documents (id, content, title, metadata) as JSON. The Rust tests load the reference and assert byte-identical match.

heading_boundary corpus: a markdown doc with `# Top`, then `## A` body, `## B` body, `### B.1` body, `## C` body. Use `pattern: "^##\\s"` so `# Top` is preamble.

jsonpath corpus: `{"items": [{"title": "a", "body": "A"}, {"title": "b", "body": "B"}]}` with `row_path: items.*`, `title_path: title`, `body_path: body`.

The integration tests follow the exact shape of `tests/hierarchy_parity.rs` from MB-2.

- [ ] **Step 1:** Write the corpora and producer script.

- [ ] **Step 2:** Run the producer:
```bash
/home/yonk/yonk-tools/chunkshop/python/.venv/bin/python /home/yonk/yonk-tools/chunkshop-rust-framers/scripts/produce_rust_framer_reference.py
```

- [ ] **Step 3:** Write the two Rust parity tests. Compare per-frame: `id`, `content`, `title`, `metadata`.

- [ ] **Step 4:** `cargo test --test heading_boundary_parity --test jsonpath_parity` — both GREEN.

- [ ] **Step 5:** Commit.

---

## Task 6: README + CHANGELOG

- "What works" gets a new `framer` row listing all four.
- "What does NOT work" no longer mentions framers.
- CHANGELOG entry under `## Unreleased / ### Changed`.

---

## Task 7: ⛔ DC-FINAL + finishing-a-development-branch.
