//! Framer stage. Sits between source and chunker. Each framer's
//! `frame(&raw)` returns 1+ framed `Document`s. Each framed doc carries
//! `metadata.framer` and `metadata.frame_seq`. Mirrors
//! `python/src/chunkshop/framers/`.

use anyhow::{anyhow, Result};
use regex::Regex;
use serde_json::Value;

use crate::config::{
    HeadingBoundaryFramerConfig, IdentityFramerConfig, JsonPathFramerConfig,
    RegexBoundaryFramerConfig, SessionEpisodeFramerConfig,
};
use crate::sources::Document;

pub trait FramerImpl {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>>;
}

/// Copy raw metadata into a fresh JSON object and stamp `framer` + `frame_seq`.
fn stamp_meta(meta: &Value, framer: &str, frame_seq: usize) -> Value {
    let mut m = meta.as_object().cloned().unwrap_or_default();
    m.insert("framer".to_string(), Value::String(framer.to_string()));
    m.insert("frame_seq".to_string(), Value::from(frame_seq as u64));
    Value::Object(m)
}

pub struct IdentityFramer;

impl IdentityFramer {
    pub fn new(_cfg: IdentityFramerConfig) -> Self {
        Self
    }
}

impl FramerImpl for IdentityFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        Ok(vec![Document {
            id: raw.id.clone(),
            content: raw.content.clone(),
            title: raw.title.clone(),
            metadata: stamp_meta(&raw.metadata, "identity", 0),
            fingerprint: None,
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
        // Python: re.compile(cfg.pattern + r".+$", re.MULTILINE).
        let heading_re = Regex::new(&format!("(?m){}.+$", cfg.pattern))
            .map_err(|e| anyhow!("invalid heading pattern: {e}"))?;
        // Pattern alone for stripping the prefix from a matched heading line.
        let pattern_re = Regex::new(&cfg.pattern).map_err(|e| anyhow!("invalid pattern: {e}"))?;
        Ok(Self {
            cfg,
            heading_re,
            pattern_re,
        })
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
                fingerprint: None,
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
                    fingerprint: None,
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
            let heading_text = self
                .pattern_re
                .replace(&heading_line, "")
                .trim()
                .to_string();
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
                fingerprint: None,
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
        Ok(Self {
            cfg,
            split_re,
            title_re,
        })
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
                fingerprint: None,
            }]);
        }

        let mut frames: Vec<Document> = Vec::new();
        for (i, (m_start, m_end)) in matches.iter().enumerate() {
            let start = if self.cfg.body_starts_with_match {
                *m_start
            } else {
                *m_end
            };
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
                fingerprint: None,
            });
        }
        Ok(frames)
    }
}

pub struct JsonPathFramer {
    row_parts: Vec<String>,
    body_parts: Vec<String>,
    title_parts: Option<Vec<String>>,
}

impl JsonPathFramer {
    pub fn new(cfg: JsonPathFramerConfig) -> Self {
        fn parts(p: &str) -> Vec<String> {
            if p == "$" {
                Vec::new()
            } else {
                p.split('.').map(String::from).collect()
            }
        }
        Self {
            row_parts: parts(&cfg.row_path),
            body_parts: parts(&cfg.body_path),
            title_parts: cfg.title_path.as_ref().map(|p| parts(p)),
        }
    }

    /// Traverse a dotted path with `*` for list iteration. Returns flat list.
    /// Mirrors Python's `_walk` in `framers/jsonpath.py`.
    fn walk<'a>(obj: &'a Value, parts: &[String]) -> Vec<&'a Value> {
        if parts.is_empty() {
            return vec![obj];
        }
        let head = &parts[0];
        let rest = &parts[1..];
        if head == "*" {
            let Some(arr) = obj.as_array() else {
                return Vec::new();
            };
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
                fingerprint: None,
            });
        }
        Ok(frames)
    }
}

// --- RM-A Task 6: SessionEpisodeFramer --------------------------------------

/// Stateless episode segmenter for agent-memory sessions. Reads
/// `metadata._session_events` (set by `SessionStagingSource`) and splits the
/// session into episodes on the first of: time gap > max_gap_seconds,
/// turn count >= max_turns, word count >= max_words, or (boundary_on_tool
/// and a tool event follows a non-tool event). Mirror of Python
/// `chunkshop.framers.session_episode.SessionEpisodeFramer`.
pub struct SessionEpisodeFramer {
    cfg: SessionEpisodeFramerConfig,
}

impl SessionEpisodeFramer {
    pub fn new(cfg: SessionEpisodeFramerConfig) -> Self {
        Self { cfg }
    }
}

impl FramerImpl for SessionEpisodeFramer {
    fn frame(&self, raw: &Document) -> Result<Vec<Document>> {
        let events: &[Value] = match raw.metadata.get("_session_events") {
            Some(Value::Array(a)) => a.as_slice(),
            _ => return Ok(vec![]),
        };
        if events.is_empty() {
            return Ok(vec![]);
        }

        // Build episodes by walking events and inserting boundaries.
        let mut episodes: Vec<Vec<&Value>> = vec![Vec::new()];
        let mut words: u32 = 0;
        for e in events {
            let cur = episodes.last_mut().unwrap();
            if let Some(prev) = cur.last().copied() {
                let prev_ts = prev.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let this_ts = e.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let gap = (this_ts - prev_ts).max(0.0) as u64;
                let prev_tool = prev.get("tool").map(|v| !v.is_null()).unwrap_or(false);
                let this_tool = e.get("tool").map(|v| !v.is_null()).unwrap_or(false);
                let tool_boundary = self.cfg.boundary_on_tool && this_tool && !prev_tool;
                if gap > self.cfg.max_gap_seconds
                    || cur.len() as u32 >= self.cfg.max_turns
                    || words >= self.cfg.max_words
                    || tool_boundary
                {
                    episodes.push(Vec::new());
                    words = 0;
                }
            }
            episodes.last_mut().unwrap().push(e);
            let content = e.get("content").and_then(|v| v.as_str()).unwrap_or("");
            words += content.split_whitespace().count() as u32;
        }

        // Materialize Documents for non-empty episodes.
        let base_meta: serde_json::Map<String, Value> = raw
            .metadata
            .as_object()
            .map(|m| {
                m.iter()
                    .filter(|(k, _)| k.as_str() != "_session_events")
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect()
            })
            .unwrap_or_default();
        let mut out = Vec::new();
        let mut frame_seq: u64 = 0;
        for evs in episodes.into_iter().filter(|e| !e.is_empty()) {
            let mut lines = Vec::with_capacity(evs.len());
            for ev in &evs {
                let role = ev.get("role").and_then(|v| v.as_str()).unwrap_or("event");
                let tool = ev.get("tool").and_then(|v| v.as_str());
                let tag = match tool {
                    Some(t) => format!("{role}/{t}"),
                    None => role.to_string(),
                };
                let content = ev.get("content").and_then(|v| v.as_str()).unwrap_or("");
                lines.push(format!("[{tag}] {content}"));
            }
            let start_ts = evs
                .first()
                .and_then(|e| e.get("ts"))
                .cloned()
                .unwrap_or(Value::Null);
            let end_ts = evs
                .last()
                .and_then(|e| e.get("ts"))
                .cloned()
                .unwrap_or(Value::Null);
            let turn_span = evs.len() as u64;

            let mut m = base_meta.clone();
            m.insert("framer".into(), Value::String("session_episode".into()));
            m.insert("frame_seq".into(), Value::from(frame_seq));
            m.insert("episode_start_ts".into(), start_ts);
            m.insert("episode_end_ts".into(), end_ts);
            m.insert("episode_turn_span".into(), Value::from(turn_span));
            m.insert(
                "_episode_events".into(),
                Value::Array(evs.into_iter().cloned().collect()),
            );

            out.push(Document {
                id: raw.id.clone(),
                content: lines.join("\n"),
                title: raw.title.clone(),
                metadata: Value::Object(m),
                fingerprint: None,
            });
            frame_seq += 1;
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn doc(id: &str, content: &str) -> Document {
        Document {
            id: id.into(),
            content: content.into(),
            title: None,
            metadata: json!({}),
            fingerprint: None,
        }
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

    #[test]
    fn regex_boundary_splits_and_extracts_title() {
        // Title pattern uses no `^...$` anchors because Python's default
        // `re.compile` doesn't enable MULTILINE. With multi-line bodies the
        // anchored form would never match (Python: re.search returns None).
        // Using a plain `Title: (.+)` form matches in both languages.
        let cfg = RegexBoundaryFramerConfig {
            split_pattern: r"^Title: ".to_string(),
            title_pattern: Some(r"Title: (.+)".to_string()),
            body_starts_with_match: true,
        };
        let f = RegexBoundaryFramer::new(cfg).unwrap();
        let frames = f
            .frame(&doc("d", "Title: A\nbody A\nTitle: B\nbody B\n"))
            .unwrap();
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0].title.as_deref(), Some("A"));
        assert_eq!(frames[1].title.as_deref(), Some("B"));
        assert_eq!(frames[0].id, "d#0");
        assert_eq!(frames[1].id, "d#1");
    }

    // --- RM-A Task 6: SessionEpisodeFramer ---------------------------------

    fn session_doc(events: Vec<Value>) -> Document {
        Document {
            id: "s1".into(),
            content: String::new(),
            title: None,
            metadata: json!({ "session_id": "s1", "_session_events": events }),
            fingerprint: None,
        }
    }

    fn default_se_cfg() -> SessionEpisodeFramerConfig {
        SessionEpisodeFramerConfig {
            max_gap_seconds: 1800,
            max_turns: 40,
            max_words: 1200,
            boundary_on_tool: true,
        }
    }

    #[test]
    fn session_episode_single_when_under_thresholds() {
        let evs = vec![
            json!({"role": "user", "content": "hi there", "ts": 100.0}),
            json!({"role": "assistant", "content": "hello back", "ts": 101.0}),
        ];
        let f = SessionEpisodeFramer::new(default_se_cfg());
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 1);
        let m = &frames[0].metadata;
        assert_eq!(m["framer"], "session_episode");
        assert_eq!(m["frame_seq"], 0);
        assert_eq!(m["episode_turn_span"], 2);
        assert_eq!(m["episode_start_ts"], 100.0);
        assert_eq!(m["episode_end_ts"], 101.0);
        assert!(frames[0].content.contains("[user] hi there"));
        assert!(frames[0].content.contains("[assistant] hello back"));
    }

    #[test]
    fn session_episode_time_gap_creates_boundary() {
        let mut cfg = default_se_cfg();
        cfg.max_gap_seconds = 10;
        let evs = vec![
            json!({"role": "user", "content": "first", "ts": 100.0}),
            json!({"role": "assistant", "content": "still ep1", "ts": 105.0}),
            json!({"role": "user", "content": "after-gap", "ts": 200.0}),
        ];
        let f = SessionEpisodeFramer::new(cfg);
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0].metadata["episode_turn_span"], 2);
        assert_eq!(frames[1].metadata["episode_turn_span"], 1);
        assert_eq!(frames[1].metadata["frame_seq"], 1);
    }

    #[test]
    fn session_episode_max_turns_creates_boundary() {
        let mut cfg = default_se_cfg();
        cfg.max_turns = 2;
        let evs = vec![
            json!({"role": "user", "content": "a", "ts": 100.0}),
            json!({"role": "assistant", "content": "b", "ts": 101.0}),
            json!({"role": "user", "content": "c", "ts": 102.0}),
        ];
        let f = SessionEpisodeFramer::new(cfg);
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0].metadata["episode_turn_span"], 2);
    }

    #[test]
    fn session_episode_max_words_creates_boundary() {
        let mut cfg = default_se_cfg();
        cfg.max_words = 3;
        let evs = vec![
            json!({"role": "user", "content": "one two three", "ts": 100.0}), // 3 words
            json!({"role": "assistant", "content": "next ep", "ts": 101.0}),  // boundary triggers
        ];
        let f = SessionEpisodeFramer::new(cfg);
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 2);
    }

    #[test]
    fn session_episode_tool_boundary_when_enabled() {
        let evs = vec![
            json!({"role": "user", "content": "ask", "ts": 100.0}),
            json!({"role": "assistant", "content": "calling", "ts": 101.0, "tool": "search"}),
        ];
        let f = SessionEpisodeFramer::new(default_se_cfg());
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 2);
        let tag_line = frames[1].content.lines().next().unwrap();
        assert!(tag_line.starts_with("[assistant/search]"));
    }

    #[test]
    fn session_episode_tool_boundary_disabled() {
        let mut cfg = default_se_cfg();
        cfg.boundary_on_tool = false;
        let evs = vec![
            json!({"role": "user", "content": "ask", "ts": 100.0}),
            json!({"role": "assistant", "content": "calling", "ts": 101.0, "tool": "search"}),
        ];
        let f = SessionEpisodeFramer::new(cfg);
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 1);
    }

    #[test]
    fn session_episode_empty_session_yields_zero() {
        let f = SessionEpisodeFramer::new(default_se_cfg());
        assert_eq!(f.frame(&session_doc(vec![])).unwrap().len(), 0);
        // missing _session_events entirely also yields zero.
        let doc = Document {
            id: "s".into(),
            content: String::new(),
            title: None,
            metadata: json!({"session_id": "s"}),
            fingerprint: None,
        };
        assert_eq!(f.frame(&doc).unwrap().len(), 0);
    }

    #[test]
    fn session_episode_strips_session_events_from_emitted_meta() {
        let evs = vec![json!({"role": "user", "content": "x", "ts": 100.0})];
        let f = SessionEpisodeFramer::new(default_se_cfg());
        let frames = f.frame(&session_doc(evs)).unwrap();
        assert_eq!(frames.len(), 1);
        assert!(
            frames[0].metadata.get("_session_events").is_none(),
            "_session_events must not leak into per-episode metadata"
        );
        // Per-episode events DO appear under the private _episode_events key.
        assert!(frames[0].metadata.get("_episode_events").is_some());
    }
}
