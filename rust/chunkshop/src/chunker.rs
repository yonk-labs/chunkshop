//! Sentence-aware chunker. Direct port of
//! `python/src/chunkshop/chunkers/sentence_aware.py` +
//! `python/src/chunkshop/chunkers/_splitting.py`.
//!
//! Strategy for prose:
//!   1. Find markdown headings (`^#{1,6}\s+.+$`). If any exist, split on them.
//!   2. Within each section (or over the whole doc if no headings), pack
//!      paragraphs until `max_chars` would be exceeded, then flush.
//!   3. When a single paragraph exceeds `max_chars`, drop to sentence-level
//!      splitting (`([.!?]\s+)` boundaries), preserving trailing whitespace.
//!   4. When a single sentence still exceeds `max_chars`, hard char-slice.
//!
//! For `doc_type: "code"`, skips heading detection and goes straight to the
//! plain paragraph packer.

use regex::Regex;
use serde_json::json;

use crate::config::{HierarchyChunkerConfig, SentenceAwareChunkerConfig};
use crate::source::Document;

/// Chunk emitted by the chunker. `embedded_content` is what gets embedded;
/// `original_content` is retained for grep / audit.
#[derive(Debug, Clone)]
pub struct Chunk {
    pub doc_id: String,
    pub seq_num: usize,
    pub original_content: String,
    pub embedded_content: String,
    pub metadata: serde_json::Value,
}

pub struct SentenceAwareChunker {
    cfg: SentenceAwareChunkerConfig,
}

impl SentenceAwareChunker {
    pub fn new(cfg: SentenceAwareChunkerConfig) -> Self {
        Self { cfg }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let splits = if self.cfg.doc_type == "code" {
            split_plain(&doc.content, self.cfg.max_chars)
        } else {
            split_prose(&doc.content, self.cfg.max_chars, self.cfg.min_chars)
        };
        splits
            .into_iter()
            .enumerate()
            .map(|(i, text)| Chunk {
                doc_id: doc.id.clone(),
                seq_num: i,
                original_content: text.clone(),
                embedded_content: text,
                metadata: json!({ "strategy": "sentence_aware" }),
            })
            .collect()
    }
}

fn md_heading_re() -> Regex {
    // Python: `r"^#{1,6}\s+.+$"` with re.MULTILINE.
    // Rust `regex` uses (?m) inline flag for multi-line.
    Regex::new(r"(?m)^#{1,6}\s+.+$").unwrap()
}

/// Heading regex with capture groups — group 1 = `#` count, group 2 = heading
/// text. Used by `HierarchyChunker` to extract heading text for the embedded-
/// content prefix and the `metadata.heading` field. Mirrors Python's
/// `r"^(#{1,6})\s+(.+?)$"`.
fn heading_with_text_re() -> Regex {
    Regex::new(r"(?m)^(#{1,6})\s+(.+?)$").unwrap()
}

fn para_break_re() -> Regex {
    Regex::new(r"\n\s*\n").unwrap()
}

fn sent_boundary_re() -> Regex {
    // Python captures `([.!?]\s+)` so split() keeps trailing whitespace.
    Regex::new(r"([.!?]\s+)").unwrap()
}

/// `_split_plain` from Python: pack paragraphs into <= max_chars buffers, and
/// recurse for oversized paragraphs via `split_to_max_chars`.
fn split_plain(text: &str, max_chars: usize) -> Vec<String> {
    let paragraphs: Vec<String> = para_break_re()
        .split(text)
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();

    let mut result: Vec<String> = Vec::new();
    let mut buffer = String::new();
    for para in paragraphs {
        if para.chars().count() > max_chars {
            if !buffer.is_empty() {
                result.push(buffer.trim().to_string());
                buffer.clear();
            }
            result.extend(split_to_max_chars(&para, max_chars));
        } else if !buffer.is_empty()
            && buffer.chars().count() + para.chars().count() + 2 > max_chars
        {
            result.push(buffer.trim().to_string());
            buffer = para;
        } else if buffer.is_empty() {
            buffer = para;
        } else {
            buffer = format!("{buffer}\n\n{para}");
        }
    }
    if !buffer.is_empty() {
        result.push(buffer.trim().to_string());
    }
    result
}

/// `_split_prose` from Python.
fn split_prose(text: &str, max_chars: usize, min_chars: usize) -> Vec<String> {
    let re = md_heading_re();
    let headings: Vec<(usize, usize)> = re
        .find_iter(text)
        .map(|m| (m.start(), m.end()))
        .collect();
    if headings.is_empty() {
        return split_plain(text, max_chars);
    }

    let mut result: Vec<String> = Vec::new();
    for i in 0..headings.len() {
        let start = headings[i].0;
        let end = if i + 1 < headings.len() {
            headings[i + 1].0
        } else {
            text.len()
        };
        let section = text[start..end].trim();
        if !section.is_empty() {
            result.extend(split_to_max_chars(section, max_chars));
        }
    }
    if headings[0].0 > 0 {
        let prefix = text[..headings[0].0].trim();
        if !prefix.is_empty() {
            let mut pre = split_to_max_chars(prefix, max_chars);
            pre.extend(result);
            result = pre;
        }
    }
    if text.chars().count() <= max_chars {
        return result.into_iter().filter(|s| !s.is_empty()).collect();
    }
    result
        .into_iter()
        .filter(|s| s.chars().count() >= min_chars)
        .collect()
}

/// `split_to_max_chars` from Python. Paragraph → sentence → char cascade.
///
/// The trailing "\n" flush-marker is kept byte-for-byte with Python so chunks
/// concatenate back into something close to the input.
pub fn split_to_max_chars(text: &str, max_chars: usize) -> Vec<String> {
    if max_chars == 0 {
        panic!("max_chars must be positive");
    }
    if text.chars().count() <= max_chars {
        return vec![text.to_string()];
    }
    let paragraphs: Vec<String> = para_break_re()
        .split(text)
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();

    let mut out: Vec<String> = Vec::new();
    let mut buf = String::new();
    for para in paragraphs {
        if para.chars().count() > max_chars {
            if !buf.is_empty() {
                out.push(format!("{buf}\n"));
                buf.clear();
            }
            out.extend(split_sentences(&para, max_chars));
            continue;
        }
        let budget = max_chars - 1;
        let candidate = if buf.is_empty() {
            para.clone()
        } else {
            format!("{buf}\n\n{para}")
        };
        if candidate.chars().count() > budget {
            if !buf.is_empty() {
                out.push(format!("{buf}\n"));
            }
            buf = para;
        } else {
            buf = candidate;
        }
    }
    if !buf.is_empty() {
        out.push(buf);
    }
    out
}

/// Python's `_sentence_tokens`: split on `([.!?]\s+)` keeping whitespace so
/// joining all tokens reproduces the input.
fn sentence_tokens(text: &str) -> Vec<String> {
    let re = sent_boundary_re();
    let mut parts: Vec<String> = Vec::new();
    let mut last = 0usize;
    for m in re.find_iter(text) {
        // Body before the match (the sentence body minus its terminator).
        parts.push(text[last..m.start()].to_string());
        // The captured separator itself (terminator + whitespace).
        parts.push(text[m.start()..m.end()].to_string());
        last = m.end();
    }
    parts.push(text[last..].to_string());

    // Python's loop pairs up [body, separator] and joins them.
    let mut tokens: Vec<String> = Vec::new();
    let mut i = 0;
    while i < parts.len() {
        let body = parts[i].clone();
        let tail = if i + 1 < parts.len() {
            parts[i + 1].clone()
        } else {
            String::new()
        };
        let token = format!("{body}{tail}");
        if !token.is_empty() {
            tokens.push(token);
        }
        i += 2;
    }
    tokens
}

fn char_slice(text: &str, max_chars: usize) -> Vec<String> {
    // Python slices by char, not byte. Match that to avoid UTF-8 panics.
    let chars: Vec<char> = text.chars().collect();
    chars
        .chunks(max_chars)
        .map(|c| c.iter().collect::<String>())
        .collect()
}

fn split_sentences(text: &str, max_chars: usize) -> Vec<String> {
    let tokens = sentence_tokens(text);
    let mut out: Vec<String> = Vec::new();
    let mut buf = String::new();
    for s in tokens {
        if s.is_empty() {
            continue;
        }
        if s.chars().count() > max_chars {
            if !buf.is_empty() {
                out.push(buf.clone());
                buf.clear();
            }
            out.extend(char_slice(&s, max_chars));
            continue;
        }
        let candidate = if buf.is_empty() {
            s.clone()
        } else {
            format!("{buf}{s}")
        };
        if candidate.chars().count() > max_chars {
            if !buf.is_empty() {
                out.push(buf.clone());
            }
            buf = s;
        } else {
            buf = candidate;
        }
    }
    if !buf.is_empty() {
        out.push(buf);
    }
    out
}

/// Hierarchy chunker — direct port of
/// `python/src/chunkshop/chunkers/hierarchy.py`. Splits a markdown doc on its
/// `^#{1,6}` headings, emits one chunk per section (skipping sections shorter
/// than `min_section_chars`), recursing into `split_to_max_chars` for sections
/// exceeding `max_chars`. When `prefix_heading` is true, the heading text is
/// prepended to `embedded_content` (separated by `\n\n`) — the bakeoff-winning
/// trick that turns each section's heading into free framing context for the
/// embedder.
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
                let h_text = c
                    .get(2)
                    .map(|m| m.as_str().trim().to_string())
                    .unwrap_or_default();
                (m0.start(), m0.end(), h_text)
            })
            .collect();

        if headings.is_empty() {
            let body = text.trim();
            if body.is_empty() {
                return Vec::new();
            }
            let title = doc.title.clone().unwrap_or_default();
            return self.emit_section_chunks(body, &title, &doc.id, 0);
        }

        let mut chunks: Vec<Chunk> = Vec::new();

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_text_single_chunk() {
        let doc = Document {
            id: "t".into(),
            content: "Just a short sentence.".into(),
            title: None,
            metadata: json!({}),
        };
        let chunker = SentenceAwareChunker::new(SentenceAwareChunkerConfig {
            doc_type: "prose".into(),
            max_chars: 2000,
            min_chars: 200,
        });
        let chunks = chunker.chunk(&doc);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].original_content, "Just a short sentence.");
        assert_eq!(chunks[0].embedded_content, chunks[0].original_content);
    }

    #[test]
    fn markdown_headings_split_sections() {
        let content = "# Top\n\nIntro para.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.";
        let doc = Document {
            id: "h".into(),
            content: content.into(),
            title: None,
            metadata: json!({}),
        };
        let chunker = SentenceAwareChunker::new(SentenceAwareChunkerConfig {
            doc_type: "prose".into(),
            max_chars: 2000,
            min_chars: 0,
        });
        let chunks = chunker.chunk(&doc);
        // 3 headings -> 3 sections
        assert_eq!(chunks.len(), 3);
        assert!(chunks[0].original_content.starts_with("# Top"));
        assert!(chunks[1].original_content.starts_with("## Section A"));
        assert!(chunks[2].original_content.starts_with("## Section B"));
    }
}
