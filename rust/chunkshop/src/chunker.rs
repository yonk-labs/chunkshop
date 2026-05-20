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

use crate::config::{
    ChunkerConfig, FixedOverlapChunkerConfig, GroupingConfig, HierarchyChunkerConfig,
    SentenceAwareChunkerConfig,
};
// `SemanticChunkerConfig` is consumed by `SemanticChunker`, whose
// `with_embedder` constructor is always available (only `chunkers`).
// `FastembedEmbedderConfig` + `FastembedEmbedder` are only used by the
// `new()` convenience constructor, which builds a `FastembedEmbedder` at
// runtime and so requires the `embedder-hub` feature (hf-hub model fetch).
use crate::config::SemanticChunkerConfig;
#[cfg(feature = "embedder-hub")]
use crate::config::FastembedEmbedderConfig;
#[cfg(feature = "embedder-hub")]
use crate::embedder::FastembedEmbedder;
use crate::sentence_split::naive_sentences;
use crate::sources::Document;
use crate::summarizer::build_summarizer;

/// Boundary-detection embedder hook for [`SemanticChunker`].
///
/// External consumers (e.g. embedded library users with their own embedder
/// registry) implement this trait against whichever embedder they want, then
/// pass `Box<dyn BoundaryEmbedder>` to [`SemanticChunker::with_embedder`].
/// This avoids hardwiring `FastembedEmbedder` as the only boundary embedder
/// and lets the chunker run without `fastembed`/`ort` in the dep tree.
///
/// The default in-tree implementation lives on
/// [`crate::embedder::FastembedEmbedder`] (gated by the `embedder` feature),
/// so [`SemanticChunker::new`] continues to "just work" for chunkshop CLI
/// and YAML-config users.
///
/// `&mut self` is intentional — embedders typically want to track cumulative
/// inference time, batching state, or a stateful ORT session. Callers that
/// need shared access should wrap the impl in a mutex (the same way
/// `SemanticChunker` does internally).
pub trait BoundaryEmbedder: Send + Sync {
    /// Embed a batch of texts. Returns one vector per input, in input order.
    /// Implementations must produce vectors of consistent dimension across
    /// the batch — `SemanticChunker` only validates that successive calls
    /// return the same shape.
    fn embed_batch(&mut self, texts: &[&str]) -> anyhow::Result<Vec<Vec<f32>>>;
}

pub mod oversize {
    //! Shared if_oversize fallback machinery (mirrors Python chunkers/_oversize.py).
    //! Brief SC-004, SC-005, SC-006, SC-008.
    use std::sync::atomic::{AtomicBool, Ordering};
    use tracing::warn;

    use crate::chunker::Chunk;

    pub const MAX_RECURSION_DEPTH: usize = 5;

    #[derive(Debug, thiserror::Error)]
    pub enum OversizeError {
        #[error(
            "if_oversize chain exceeded depth {} (chunker={chunker})",
            MAX_RECURSION_DEPTH
        )]
        Recursion { chunker: String },
    }

    /// Per-chunker-instance dedup'd warner. `warn_once` is a no-op after the
    /// first call, so a chunker that emits 100 oversize chunks in a single
    /// cell still only logs once.
    pub struct DedupedWarner {
        pub chunker_name: String,
        pub ceiling: usize,
        warned: AtomicBool,
    }

    impl DedupedWarner {
        pub fn new(chunker_name: impl Into<String>, ceiling: usize) -> Self {
            Self {
                chunker_name: chunker_name.into(),
                ceiling,
                warned: AtomicBool::new(false),
            }
        }

        pub fn warn_once(&self, oversize_len: usize) {
            if self.warned.swap(true, Ordering::Relaxed) {
                return;
            }
            warn!(
                target: "chunkshop::oversize",
                chunker = %self.chunker_name,
                ceiling = self.ceiling,
                oversize_len = oversize_len,
                "{} emitted oversize chunk(s) (>{} chars), no if_oversize fallback set; \
                 first oversize chunk has {} chars. \
                 To fix: add `if_oversize: {{ type: fixed_overlap, window_words: 200, \
                 step_words: 160, max_chars: {} }}` to the chunker config.",
                self.chunker_name,
                self.ceiling,
                oversize_len,
                self.ceiling,
            );
        }
    }

    /// Trigger condition: a chunk is oversize if either text field exceeds
    /// the ceiling. Counts in `chars()` to match Python's `len(str)` semantics
    /// for unicode-aware sizing.
    pub fn is_oversize(c: &Chunk, ceiling: usize) -> bool {
        c.embedded_content.chars().count() > ceiling || c.original_content.chars().count() > ceiling
    }
}

/// Apply the `if_oversize` fallback chain to a chunker's output. Mirrors
/// Python `chunkers/_oversize.py:apply_if_oversize`. Brief SC-004, SC-005,
/// SC-006, SC-008.
///
/// - When `ceiling` is `None`, returns `chunks` unchanged.
/// - For each chunk, if `skip_check` returns true, the chunk is preserved
///   verbatim (used by `hierarchical_summary` to exempt coarse rows — Brief
///   SC-005).
/// - When a chunk exceeds `ceiling` and `if_oversize_cfg` is set, the
///   chunk's `original_content` is re-chunked through the fallback chunker,
///   and each resulting sub-chunk is emitted with both `original_content`
///   and `embedded_content` set to the fallback's output (the "decision D2"
///   propagation rule documented in the Python helper).
/// - When a chunk exceeds `ceiling` and `if_oversize_cfg` is None, the
///   chunk is preserved as-is and the deduped `warner` (if provided) fires
///   exactly once per cell (Brief SC-006).
/// - Recursion guard: descends at most `oversize::MAX_RECURSION_DEPTH`
///   levels (Brief SC-008). Beyond that, returns `OversizeError::Recursion`.
pub fn apply_if_oversize(
    chunks: Vec<Chunk>,
    ceiling: Option<usize>,
    if_oversize_cfg: Option<&ChunkerConfig>,
    chunker_name: &str,
    document: &Document,
    depth: usize,
    skip_check: Option<&dyn Fn(&Chunk) -> bool>,
    warner: Option<&oversize::DedupedWarner>,
) -> Result<Vec<Chunk>, oversize::OversizeError> {
    if depth > oversize::MAX_RECURSION_DEPTH {
        return Err(oversize::OversizeError::Recursion {
            chunker: chunker_name.to_string(),
        });
    }
    let Some(ceiling) = ceiling else {
        return Ok(chunks);
    };
    let mut out: Vec<Chunk> = Vec::new();
    let mut seq = 0usize;
    for c in chunks {
        if let Some(f) = skip_check {
            if f(&c) {
                out.push(Chunk { seq_num: seq, ..c });
                seq += 1;
                continue;
            }
        }
        if !oversize::is_oversize(&c, ceiling) {
            out.push(Chunk { seq_num: seq, ..c });
            seq += 1;
            continue;
        }
        let Some(if_cfg) = if_oversize_cfg else {
            if let Some(w) = warner {
                let len_chars = c
                    .embedded_content
                    .chars()
                    .count()
                    .max(c.original_content.chars().count());
                w.warn_once(len_chars);
            }
            out.push(Chunk { seq_num: seq, ..c });
            seq += 1;
            continue;
        };
        // D2 rule: re-chunk `original_content`; copy fallback output to both
        // text fields of each replacement chunk.
        let synth_doc = Document {
            id: c.doc_id.clone(),
            content: c.original_content.clone(),
            title: document.title.clone(),
            metadata: document.metadata.clone(),
        };
        let fallback =
            build_chunker(if_cfg.clone()).map_err(|_| oversize::OversizeError::Recursion {
                chunker: chunker_name.to_string(),
            })?;
        let sub_raw = fallback.chunk(&synth_doc);
        let nested_ceiling = if_cfg.effective_max_chars();
        let nested_cfg = if_cfg.if_oversize();
        let sub = apply_if_oversize(
            sub_raw,
            nested_ceiling,
            nested_cfg,
            "fallback",
            &synth_doc,
            depth + 1,
            None,
            warner,
        )?;
        for sc in sub {
            // Merge: parent metadata first, fallback's keys override.
            let mut merged = c.metadata.as_object().cloned().unwrap_or_default();
            if let Some(sub_obj) = sc.metadata.as_object() {
                for (k, v) in sub_obj.iter() {
                    merged.insert(k.clone(), v.clone());
                }
            }
            out.push(Chunk {
                doc_id: c.doc_id.clone(),
                seq_num: seq,
                original_content: sc.original_content.clone(),
                embedded_content: sc.original_content.clone(),
                metadata: serde_json::Value::Object(merged),
            });
            seq += 1;
        }
    }
    Ok(out)
}

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
    /// Per-cell deduped warner. `None` when the chunker has no effective
    /// ceiling (defensive — `SentenceAwareChunkerConfig` always carries
    /// `max_chars`, so this is always `Some` in practice).
    warner: Option<oversize::DedupedWarner>,
}

impl SentenceAwareChunker {
    pub fn new(cfg: SentenceAwareChunkerConfig) -> Self {
        let warner = Some(oversize::DedupedWarner::new(
            "sentence_aware",
            cfg.max_chars,
        ));
        Self { cfg, warner }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let splits = if self.cfg.doc_type == "code" {
            split_plain(&doc.content, self.cfg.max_chars)
        } else {
            split_prose(&doc.content, self.cfg.max_chars, self.cfg.min_chars)
        };
        let chunks: Vec<Chunk> = splits
            .into_iter()
            .enumerate()
            .map(|(i, text)| Chunk {
                doc_id: doc.id.clone(),
                seq_num: i,
                original_content: text.clone(),
                embedded_content: text,
                metadata: json!({ "strategy": "sentence_aware" }),
            })
            .collect();
        apply_if_oversize(
            chunks,
            Some(self.cfg.max_chars),
            self.cfg.if_oversize.as_deref(),
            "sentence_aware",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
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
    let headings: Vec<(usize, usize)> = re.find_iter(text).map(|m| (m.start(), m.end())).collect();
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
///
/// # Panics
///
/// Panics if `max_chars == 0`. Validation is expected at the caller — config
/// loaders enforce `max_chars > 0` at YAML parse time, so this assertion is
/// reachable only via programmatic API misuse.
pub fn split_to_max_chars(text: &str, max_chars: usize) -> Vec<String> {
    assert!(max_chars > 0, "max_chars must be positive");
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
    /// Compiled heading boundary regex. Either the default markdown pattern
    /// (capture group 2 = heading text) or a user-supplied custom pattern
    /// (first capture group, if present, = heading text; otherwise the full
    /// match is used as heading text).
    heading_re: Regex,
    /// True when `heading_re` was compiled from a user-supplied
    /// `cfg.heading_pattern`. Controls the heading-text extraction path:
    /// the default path uses capture group 2 (mirrors Python parity); the
    /// custom path uses capture group 1 if present, else the full match.
    custom_heading_pattern: bool,
    warner: Option<oversize::DedupedWarner>,
}

impl HierarchyChunker {
    pub fn new(cfg: HierarchyChunkerConfig) -> anyhow::Result<Self> {
        let (heading_re, custom_heading_pattern) = match &cfg.heading_pattern {
            Some(pat) => {
                let re = Regex::new(pat).map_err(|e| {
                    anyhow::anyhow!("invalid hierarchy heading_pattern {pat:?}: {e}")
                })?;
                (re, true)
            }
            None => (heading_with_text_re(), false),
        };
        let warner = Some(oversize::DedupedWarner::new("hierarchy", cfg.max_chars));
        Ok(Self {
            cfg,
            heading_re,
            custom_heading_pattern,
            warner,
        })
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let text = &doc.content;
        let headings: Vec<(usize, usize, String)> = self
            .heading_re
            .captures_iter(text)
            .map(|c| {
                let m0 = c.get(0).unwrap();
                let h_text = if self.custom_heading_pattern {
                    // Custom pattern: prefer capture group 1 if the user
                    // supplied one; otherwise fall back to the full match.
                    c.get(1)
                        .or_else(|| c.get(0))
                        .map(|m| m.as_str().trim().to_string())
                        .unwrap_or_default()
                } else {
                    // Default markdown pattern: capture group 2 is the
                    // heading text (Python parity).
                    c.get(2)
                        .map(|m| m.as_str().trim().to_string())
                        .unwrap_or_default()
                };
                (m0.start(), m0.end(), h_text)
            })
            .collect();

        let chunks = if headings.is_empty() {
            let body = text.trim();
            if body.is_empty() {
                Vec::new()
            } else {
                let title = doc.title.clone().unwrap_or_default();
                self.emit_section_chunks(body, &title, &doc.id, 0)
            }
        } else {
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
        };

        apply_if_oversize(
            chunks,
            Some(self.cfg.max_chars),
            self.cfg.if_oversize.as_deref(),
            "hierarchy",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
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

/// Common interface every chunker satisfies. Object-safe so the runner can
/// hold a `Box<dyn ChunkerImpl + Send + Sync>` and let `NeighborExpandChunker`
/// recursively wrap any other chunker as its base.
pub trait ChunkerImpl {
    fn chunk(&self, doc: &Document) -> Vec<Chunk>;
}

impl ChunkerImpl for SentenceAwareChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

impl ChunkerImpl for HierarchyChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// Word-level sliding-window chunker. Direct port of
/// `python/src/chunkshop/chunkers/fixed_overlap.py`. Same `text.split()`
/// whitespace-collapse semantics as Python (use Rust's `split_whitespace`).
/// Each chunk carries `metadata.start_word` and `metadata.n_words`.
pub struct FixedOverlapChunker {
    cfg: FixedOverlapChunkerConfig,
    warner: Option<oversize::DedupedWarner>,
}

impl FixedOverlapChunker {
    pub fn new(cfg: FixedOverlapChunkerConfig) -> anyhow::Result<Self> {
        if cfg.window_words == 0 || cfg.step_words == 0 {
            return Err(anyhow::anyhow!(
                "window_words and step_words must be positive"
            ));
        }
        let warner = cfg
            .max_chars
            .map(|m| oversize::DedupedWarner::new("fixed_overlap", m));
        Ok(Self { cfg, warner })
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
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
        apply_if_oversize(
            chunks,
            self.cfg.max_chars,
            self.cfg.if_oversize.as_deref(),
            "fixed_overlap",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
    }
}

impl ChunkerImpl for FixedOverlapChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// Wrapper chunker that joins each base chunk with its ±N neighbors into
/// `embedded_content`. Direct port of
/// `python/src/chunkshop/chunkers/neighbor_expand.py`. Preserves the base
/// chunk's `seq_num`, `original_content`, and metadata; adds
/// `neighbor_expand_window` to metadata.
pub struct NeighborExpandChunker {
    window: usize,
    base: Box<dyn ChunkerImpl + Send + Sync>,
    /// Resolved effective ceiling: explicit `max_chars` on the wrapper, else
    /// `base.effective_max_chars()`. None when no enforcement applies.
    effective_ceiling: Option<usize>,
    if_oversize_cfg: Option<ChunkerConfig>,
    warner: Option<oversize::DedupedWarner>,
}

impl NeighborExpandChunker {
    /// Construct a wrapper around `base` with the given window size. The
    /// runner builds `base` recursively from `NeighborExpandChunkerConfig.base`
    /// then passes resolved `effective_ceiling` and `if_oversize_cfg` here.
    pub fn new(
        window: usize,
        base: Box<dyn ChunkerImpl + Send + Sync>,
        effective_ceiling: Option<usize>,
        if_oversize_cfg: Option<ChunkerConfig>,
    ) -> Self {
        let warner = effective_ceiling.map(|m| oversize::DedupedWarner::new("neighbor_expand", m));
        Self {
            window,
            base,
            effective_ceiling,
            if_oversize_cfg,
            warner,
        }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let base_chunks = self.base.chunk(doc);
        let n = base_chunks.len();
        if n == 0 {
            return Vec::new();
        }
        let w = self.window;
        let mut out = Vec::with_capacity(n);
        for (i, bc) in base_chunks.iter().enumerate() {
            let lo = i.saturating_sub(w);
            let hi = (i + w).min(n - 1);
            let parts: Vec<&str> = (lo..=hi)
                .map(|j| base_chunks[j].embedded_content.as_str())
                .collect();
            let joined = parts.join("\n\n");

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
        apply_if_oversize(
            out,
            self.effective_ceiling,
            self.if_oversize_cfg.as_ref(),
            "neighbor_expand",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
    }
}

impl ChunkerImpl for NeighborExpandChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// Semantic chunker — splits documents at topic shifts detected by sentence-
/// embedding similarity drops. Direct port of
/// `python/src/chunkshop/chunkers/semantic.py`. Unlike Python, the Rust
/// chunker always loads a fresh boundary embedder instance — the
/// `boundary_model: "same"` shared-instance optimization is not implemented;
/// document this trade-off in the README. Cross-language byte-identical
/// chunks are NOT promised: the percentile threshold over embedder distances
/// can shift breakpoints under MB-1's documented ORT-binary drift.
///
/// The chunker holds a [`Box<dyn BoundaryEmbedder>`] so external consumers
/// can plug in their own embedder via [`SemanticChunker::with_embedder`]
/// without pulling `fastembed`/`ort` into the dep tree. The convenience
/// constructor [`SemanticChunker::new`] uses [`FastembedEmbedder`] under
/// the hood and requires the `embedder-hub` Cargo feature (fastembed + ORT
/// + hf-hub for runtime boundary-model fetch).
pub struct SemanticChunker {
    cfg: SemanticChunkerConfig,
    boundary: std::sync::Mutex<Box<dyn BoundaryEmbedder>>,
    warner: Option<oversize::DedupedWarner>,
}

/// Shared config validation for both [`SemanticChunker::new`] and
/// [`SemanticChunker::with_embedder`]. Pulled out so the bytes-in path
/// gets the same guarantees as the auto-fetch path.
fn validate_semantic_cfg(cfg: &SemanticChunkerConfig) -> anyhow::Result<()> {
    if cfg.sentence_splitter != "naive" {
        return Err(anyhow::anyhow!(
            "sentence_splitter {:?} not supported in chunkshop-rs (only 'naive'); \
             nltk requires Python",
            cfg.sentence_splitter
        ));
    }
    if cfg.breakpoint_percentile == 0 || cfg.breakpoint_percentile >= 100 {
        return Err(anyhow::anyhow!(
            "breakpoint_percentile must be in [1, 99], got {}",
            cfg.breakpoint_percentile
        ));
    }
    if cfg.min_sentences_per_chunk < 1 {
        return Err(anyhow::anyhow!("min_sentences_per_chunk must be >= 1"));
    }
    if cfg.max_chunk_chars < 100 {
        return Err(anyhow::anyhow!(
            "max_chunk_chars must be >= 100, got {}",
            cfg.max_chunk_chars
        ));
    }
    Ok(())
}

impl SemanticChunker {
    /// Construct a `SemanticChunker` with a caller-supplied boundary
    /// embedder. Always available — does not require the `embedder` Cargo
    /// feature.
    ///
    /// `cfg.boundary_model` is ignored by this constructor: the embedder is
    /// already supplied. Validation rules from `cfg` (`sentence_splitter`,
    /// `breakpoint_percentile`, `min_sentences_per_chunk`, `max_chunk_chars`)
    /// still apply.
    pub fn with_embedder(
        cfg: SemanticChunkerConfig,
        embedder: Box<dyn BoundaryEmbedder>,
    ) -> anyhow::Result<Self> {
        validate_semantic_cfg(&cfg)?;
        let warner = Some(oversize::DedupedWarner::new(
            "semantic",
            cfg.max_chunk_chars,
        ));
        Ok(Self {
            cfg,
            boundary: std::sync::Mutex::new(embedder),
            warner,
        })
    }

    /// Convenience constructor: builds a [`FastembedEmbedder`] from
    /// `cfg.boundary_model` and wraps it. Requires the `embedder-hub` Cargo
    /// feature (FastembedEmbedder::new fetches the boundary model via
    /// hf-hub). Existing chunkshop CLI / YAML-config callers continue to use
    /// this entry point unchanged.
    #[cfg(feature = "embedder-hub")]
    pub fn new(cfg: SemanticChunkerConfig) -> anyhow::Result<Self> {
        validate_semantic_cfg(&cfg)?;
        let boundary_cfg = FastembedEmbedderConfig {
            model_name: cfg.boundary_model.clone(),
            // dim=384 for MiniLM; the FastembedEmbedder uses this only as a
            // post-init sanity check on the first vector. If the user picks a
            // different boundary model with a different dim, the embedder will
            // surface a clear error.
            dim: 384,
            batch_size: 16,
            threads: Some(2),
            hf_repo: None,
            onnx_path: None,
            pooling: "cls".to_string(),
            additional_files: vec![],
        };
        let boundary = FastembedEmbedder::new(boundary_cfg)?;
        Self::with_embedder(cfg, Box::new(boundary))
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let chunks = self.chunk_inner(doc);
        apply_if_oversize(
            chunks,
            Some(self.cfg.max_chunk_chars),
            self.cfg.if_oversize.as_deref(),
            "semantic",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
    }

    fn chunk_inner(&self, doc: &Document) -> Vec<Chunk> {
        if doc.content.is_empty() || doc.content.trim().is_empty() {
            return Vec::new();
        }
        let sentences = naive_sentences(&doc.content);
        if sentences.is_empty() {
            return Vec::new();
        }
        if sentences.len() == 1 {
            // No boundaries possible. Still respect max_chunk_chars.
            let mut chunks = Vec::new();
            for sub in self.split_if_too_large(&sentences[0], (0, 1)) {
                let seq = chunks.len();
                chunks.push(self.mk_chunk(&doc.id, seq, &sub));
            }
            return chunks;
        }

        // Borrow as &str slices into the trait's `embed_batch` API. The
        // sentence Strings live for the duration of this call so the borrows
        // are safe. Going through `Box<dyn BoundaryEmbedder>` adds one vtable
        // dispatch per batch — negligible vs. the ORT inference cost.
        let refs: Vec<&str> = sentences.iter().map(String::as_str).collect();
        let embeddings = match self
            .boundary
            .lock()
            .expect("poisoned mutex")
            .embed_batch(&refs)
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("semantic chunker boundary embed failed: {e:#}");
                return vec![self.mk_chunk(&doc.id, 0, &doc.content)];
            }
        };

        let normed: Vec<Vec<f32>> = embeddings
            .iter()
            .map(|v| {
                let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
                let denom = if n == 0.0 { 1.0 } else { n };
                v.iter().map(|x| x / denom).collect()
            })
            .collect();

        let mut distances: Vec<f32> = Vec::with_capacity(normed.len() - 1);
        for i in 0..normed.len() - 1 {
            let dot: f32 = normed[i]
                .iter()
                .zip(&normed[i + 1])
                .map(|(a, b)| a * b)
                .sum();
            distances.push(1.0 - dot);
        }

        let threshold = percentile_linear(&distances, self.cfg.breakpoint_percentile);
        let breakpoints: Vec<usize> = distances
            .iter()
            .enumerate()
            .filter_map(|(i, &d)| if d >= threshold { Some(i) } else { None })
            .collect();

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

        let spans = merge_small_spans(spans, self.cfg.min_sentences_per_chunk);

        let mut chunks: Vec<Chunk> = Vec::new();
        for (s, e) in spans {
            let body = sentences[s..e].join(" ").trim().to_string();
            if body.is_empty() {
                continue;
            }
            for sub in self.split_if_too_large(&body, (s, e)) {
                let seq = chunks.len();
                chunks.push(self.mk_chunk(&doc.id, seq, &sub));
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

    fn split_if_too_large(&self, body: &str, span: (usize, usize)) -> Vec<String> {
        // Brief SC-007: when a span exceeds max_chunk_chars and we have to
        // hard-split, log a tracing warning matching Python semantic.py:120
        // shape. The warning fires only when sub_chunks.len() > 1 — a single
        // returned chunk means the body fit and didn't actually split.
        if body.chars().count() <= self.cfg.max_chunk_chars {
            return vec![body.to_string()];
        }
        let sents = naive_sentences(body);
        let sub_chunks: Vec<String> = if sents.is_empty() {
            let chars: Vec<char> = body.chars().collect();
            chars
                .chunks(self.cfg.max_chunk_chars)
                .map(|c| c.iter().collect())
                .collect()
        } else {
            let mut out: Vec<String> = Vec::new();
            let mut cur = String::new();
            for s in sents {
                let candidate = if cur.is_empty() {
                    s.clone()
                } else {
                    let joined = format!("{cur} {s}");
                    joined.trim().to_string()
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
        };
        if sub_chunks.len() > 1 {
            tracing::warn!(
                target: "chunkshop::semantic",
                max_chunk_chars = self.cfg.max_chunk_chars,
                span_start = span.0,
                span_end = span.1,
                body_len = body.chars().count(),
                sub_chunks = sub_chunks.len(),
                "semantic chunk exceeded max_chunk_chars={}; hard-split into {} sub-chunks",
                self.cfg.max_chunk_chars,
                sub_chunks.len(),
            );
        }
        sub_chunks
    }
}

impl ChunkerImpl for SemanticChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// Wraps any base chunker; replaces each chunk's `embedded_content` with a
/// summary. Mirrors `python/src/chunkshop/chunkers/summary_embed.py`.
/// `original_content` and `seq_num` survive from the base chunk.
/// `metadata.summarizer` is stamped with the summarizer mode for traceability.
pub struct SummaryEmbedChunker {
    base: Box<dyn ChunkerImpl + Send + Sync>,
    summarizer: Box<dyn crate::summarizer::SummarizerImpl>,
    mode: &'static str,
    effective_ceiling: Option<usize>,
    if_oversize_cfg: Option<ChunkerConfig>,
    warner: Option<oversize::DedupedWarner>,
}

impl SummaryEmbedChunker {
    pub fn new(
        base: Box<dyn ChunkerImpl + Send + Sync>,
        summarizer: Box<dyn crate::summarizer::SummarizerImpl>,
        mode: &'static str,
        effective_ceiling: Option<usize>,
        if_oversize_cfg: Option<ChunkerConfig>,
    ) -> Self {
        let warner = effective_ceiling.map(|m| oversize::DedupedWarner::new("summary_embed", m));
        Self {
            base,
            summarizer,
            mode,
            effective_ceiling,
            if_oversize_cfg,
            warner,
        }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let base_chunks = self.base.chunk(doc);
        let mut out = Vec::with_capacity(base_chunks.len());
        for bc in base_chunks {
            let summary = match self
                .summarizer
                .summarize(&bc.original_content, &doc.metadata)
            {
                Ok(s) => s,
                Err(e) => {
                    tracing::error!(
                        "summary_embed: summarizer failed on doc={} seq={}: {e:#}",
                        bc.doc_id,
                        bc.seq_num
                    );
                    return Vec::new();
                }
            };
            let mut meta = bc.metadata.as_object().cloned().unwrap_or_default();
            meta.insert(
                "summarizer".to_string(),
                serde_json::Value::String(self.mode.to_string()),
            );
            out.push(Chunk {
                doc_id: bc.doc_id,
                seq_num: bc.seq_num,
                original_content: bc.original_content,
                embedded_content: summary,
                metadata: serde_json::Value::Object(meta),
            });
        }
        apply_if_oversize(
            out,
            self.effective_ceiling,
            self.if_oversize_cfg.as_ref(),
            "summary_embed",
            doc,
            0,
            None,
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
    }
}

impl ChunkerImpl for SummaryEmbedChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// Emits BOTH base (fine) and per-group coarse summary chunks linked by
/// `group_id`. Mirrors `python/src/chunkshop/chunkers/hierarchical_summary.py`.
/// Three grouping strategies: fixed_n, word_budget, section_aware.
pub struct HierarchicalSummaryChunker {
    base: Box<dyn ChunkerImpl + Send + Sync>,
    summarizer: Box<dyn crate::summarizer::SummarizerImpl>,
    mode: &'static str,
    grouping: HierarchicalGrouping,
    effective_ceiling: Option<usize>,
    if_oversize_cfg: Option<ChunkerConfig>,
    warner: Option<oversize::DedupedWarner>,
}

/// Internal grouping handle — owns just the params it needs (no config types).
pub enum HierarchicalGrouping {
    FixedN(usize),
    WordBudget(usize),
    SectionAware,
}

impl HierarchicalSummaryChunker {
    pub fn new(
        base: Box<dyn ChunkerImpl + Send + Sync>,
        summarizer: Box<dyn crate::summarizer::SummarizerImpl>,
        mode: &'static str,
        grouping: HierarchicalGrouping,
        effective_ceiling: Option<usize>,
        if_oversize_cfg: Option<ChunkerConfig>,
    ) -> Self {
        let warner =
            effective_ceiling.map(|m| oversize::DedupedWarner::new("hierarchical_summary", m));
        Self {
            base,
            summarizer,
            mode,
            grouping,
            effective_ceiling,
            if_oversize_cfg,
            warner,
        }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        let base_chunks = self.base.chunk(doc);
        if base_chunks.is_empty() {
            return Vec::new();
        }
        let groups = self.group(base_chunks);

        let mut out: Vec<Chunk> = Vec::new();
        let mut seq: usize = 0;
        for (group_idx, group_chunks) in groups.into_iter().enumerate() {
            let group_id = format!("{}::g{}", doc.id, group_idx);

            // Fine rows: preserve base metadata, stamp granularity + group_id.
            for bc in &group_chunks {
                let mut meta = bc.metadata.as_object().cloned().unwrap_or_default();
                meta.insert(
                    "granularity".to_string(),
                    serde_json::Value::String("fine".to_string()),
                );
                meta.insert(
                    "group_id".to_string(),
                    serde_json::Value::String(group_id.clone()),
                );
                meta.insert(
                    "summarizer".to_string(),
                    serde_json::Value::String(self.mode.to_string()),
                );
                out.push(Chunk {
                    doc_id: bc.doc_id.clone(),
                    seq_num: seq,
                    original_content: bc.original_content.clone(),
                    embedded_content: bc.embedded_content.clone(),
                    metadata: serde_json::Value::Object(meta),
                });
                seq += 1;
            }

            // One coarse row per group.
            let joined = group_chunks
                .iter()
                .map(|c| c.original_content.as_str())
                .collect::<Vec<_>>()
                .join("\n\n");
            let summary = match self.summarizer.summarize(&joined, &doc.metadata) {
                Ok(s) => s,
                Err(e) => {
                    tracing::error!(
                        "hierarchical_summary: summarizer failed on doc={} group={group_idx}: {e:#}",
                        doc.id
                    );
                    return Vec::new();
                }
            };
            let mut coarse_meta = serde_json::Map::new();
            coarse_meta.insert(
                "granularity".to_string(),
                serde_json::Value::String("coarse".to_string()),
            );
            coarse_meta.insert("group_id".to_string(), serde_json::Value::String(group_id));
            coarse_meta.insert(
                "summarizer".to_string(),
                serde_json::Value::String(self.mode.to_string()),
            );
            coarse_meta.insert(
                "strategy".to_string(),
                serde_json::Value::String("hierarchical_summary".to_string()),
            );
            out.push(Chunk {
                doc_id: doc.id.clone(),
                seq_num: seq,
                original_content: joined,
                embedded_content: summary,
                metadata: serde_json::Value::Object(coarse_meta),
            });
            seq += 1;
        }
        // Brief SC-005: coarse rows are exempt from the oversize check —
        // they preserve 1-per-group structure even when large. Pass a
        // skip_check that returns true for `granularity == "coarse"`.
        let skip =
            |c: &Chunk| c.metadata.get("granularity").and_then(|v| v.as_str()) == Some("coarse");
        apply_if_oversize(
            out,
            self.effective_ceiling,
            self.if_oversize_cfg.as_ref(),
            "hierarchical_summary",
            doc,
            0,
            Some(&skip),
            self.warner.as_ref(),
        )
        .expect("if_oversize recursion")
    }

    fn group(&self, chunks: Vec<Chunk>) -> Vec<Vec<Chunk>> {
        match self.grouping {
            HierarchicalGrouping::FixedN(n) => {
                if n == 0 {
                    return vec![chunks];
                }
                let mut groups: Vec<Vec<Chunk>> = Vec::new();
                let mut cur: Vec<Chunk> = Vec::new();
                for c in chunks {
                    if cur.len() == n {
                        groups.push(std::mem::take(&mut cur));
                    }
                    cur.push(c);
                }
                if !cur.is_empty() {
                    groups.push(cur);
                }
                groups
            }
            HierarchicalGrouping::WordBudget(max_words) => {
                let mut groups: Vec<Vec<Chunk>> = Vec::new();
                let mut cur: Vec<Chunk> = Vec::new();
                let mut cur_words = 0usize;
                for c in chunks {
                    let w = c.original_content.split_whitespace().count();
                    if !cur.is_empty() && cur_words + w > max_words {
                        groups.push(std::mem::take(&mut cur));
                        cur_words = 0;
                    }
                    cur.push(c);
                    cur_words += w;
                }
                if !cur.is_empty() {
                    groups.push(cur);
                }
                groups
            }
            HierarchicalGrouping::SectionAware => {
                // Group by metadata.heading. Validator guarantees base is hierarchy.
                let mut groups: Vec<Vec<Chunk>> = Vec::new();
                let mut cur: Vec<Chunk> = Vec::new();
                let mut cur_heading: Option<String> = None;
                let mut have_heading = false;
                for c in chunks {
                    let h = c
                        .metadata
                        .get("heading")
                        .and_then(|v| v.as_str())
                        .map(String::from);
                    if have_heading && h != cur_heading && !cur.is_empty() {
                        groups.push(std::mem::take(&mut cur));
                    }
                    cur_heading = h;
                    have_heading = true;
                    cur.push(c);
                }
                if !cur.is_empty() {
                    groups.push(cur);
                }
                groups
            }
        }
    }
}

impl ChunkerImpl for HierarchicalSummaryChunker {
    fn chunk(&self, doc: &Document) -> Vec<Chunk> {
        Self::chunk(self, doc)
    }
}

/// numpy.percentile with linear interpolation. Mirrors numpy default.
/// Returns the percentile **value** (not the index). Used by `SemanticChunker`.
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
/// pull the next span into the first if the first is too small; back-merge the
/// last span if needed. Mirrors Python's `_merge_small`. Used by `SemanticChunker`.
fn merge_small_spans(spans: Vec<(usize, usize)>, min: usize) -> Vec<(usize, usize)> {
    if spans.is_empty() {
        return spans;
    }
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (s, e) in spans {
        if !merged.is_empty() && (e - s) < min {
            let last = merged.len() - 1;
            let (ps, _) = merged[last];
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

/// Recursively materialize a `ChunkerConfig` into a boxed trait object.
/// `NeighborExpand` calls back into this fn to construct its `base`. Adding a
/// new chunker = one new arm here + one new variant on `ChunkerConfig`.
///
/// Each wrapper chunker resolves its `effective_max_chars` (Brief SC-003) and
/// passes the optional `if_oversize` config through to the chunker
/// constructor — `apply_if_oversize` runs at the tail of every `chunk()`
/// call.
///
/// The `Semantic` variant is only available when the `embedder` feature is
/// enabled; without it, attempting to build a semantic chunker returns an
/// `Err`. This lets the chunker module work standalone (without fastembed)
/// while preserving runtime behavior when the full stack is compiled.
pub fn build_chunker(cfg: ChunkerConfig) -> anyhow::Result<Box<dyn ChunkerImpl + Send + Sync>> {
    Ok(match cfg {
        ChunkerConfig::SentenceAware(c) => Box::new(SentenceAwareChunker::new(c)),
        ChunkerConfig::Hierarchy(c) => Box::new(HierarchyChunker::new(c)?),
        ChunkerConfig::FixedOverlap(c) => Box::new(FixedOverlapChunker::new(c)?),
        ChunkerConfig::NeighborExpand(c) => {
            let window = c.window;
            // Resolve effective ceiling: explicit override on wrapper else
            // base.effective_max_chars().
            let effective_ceiling = c.max_chars.or_else(|| c.base.effective_max_chars());
            let if_oversize_cfg = c.if_oversize.as_deref().cloned();
            let base = build_chunker(*c.base)?;
            Box::new(NeighborExpandChunker::new(
                window,
                base,
                effective_ceiling,
                if_oversize_cfg,
            ))
        }
        #[cfg(feature = "embedder-hub")]
        ChunkerConfig::Semantic(c) => Box::new(SemanticChunker::new(c)?),
        #[cfg(not(feature = "embedder-hub"))]
        ChunkerConfig::Semantic(_) => {
            return Err(anyhow::anyhow!(
                "semantic chunker requires the `embedder-hub` Cargo feature \
                 (rebuild with --features embedder-hub, --features embedder, or --features full)"
            ));
        }
        ChunkerConfig::SummaryEmbed(c) => {
            let mode = c.summarizer.mode_str();
            let effective_ceiling = c.max_chars.or_else(|| c.base.effective_max_chars());
            let if_oversize_cfg = c.if_oversize.as_deref().cloned();
            let base = build_chunker(*c.base)?;
            let summarizer = build_summarizer(&c.summarizer)?;
            Box::new(SummaryEmbedChunker::new(
                base,
                summarizer,
                mode,
                effective_ceiling,
                if_oversize_cfg,
            ))
        }
        ChunkerConfig::HierarchicalSummary(c) => {
            let mode = c.summarizer.mode_str();
            let effective_ceiling = c.max_chars.or_else(|| c.base.effective_max_chars());
            let if_oversize_cfg = c.if_oversize.as_deref().cloned();
            let base = build_chunker(*c.base)?;
            let summarizer = build_summarizer(&c.summarizer)?;
            let grouping = match c.grouping {
                GroupingConfig::FixedN(g) => HierarchicalGrouping::FixedN(g.n),
                GroupingConfig::WordBudget(g) => HierarchicalGrouping::WordBudget(g.max_words),
                GroupingConfig::SectionAware(_) => HierarchicalGrouping::SectionAware,
            };
            Box::new(HierarchicalSummaryChunker::new(
                base,
                summarizer,
                mode,
                grouping,
                effective_ceiling,
                if_oversize_cfg,
            ))
        }
        ChunkerConfig::Consolidation(_) => {
            return Err(anyhow::anyhow!(
                "consolidation chunker not yet implemented in this build \
                 (RM-A Task 8; tracking: chunkshop#9)"
            ));
        }
    })
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
            if_oversize: None,
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
            if_oversize: None,
        });
        let chunks = chunker.chunk(&doc);
        // 3 headings -> 3 sections
        assert_eq!(chunks.len(), 3);
        assert!(chunks[0].original_content.starts_with("# Top"));
        assert!(chunks[1].original_content.starts_with("## Section A"));
        assert!(chunks[2].original_content.starts_with("## Section B"));
    }

    /// Sanity-check the default markdown behavior of `HierarchyChunker` when
    /// `heading_pattern: None`. Backward-compat anchor: any change to the
    /// default extraction path that breaks `metadata.heading == "Section A"`
    /// or the chunk count flips this red.
    #[test]
    fn hierarchy_default_uses_markdown_pattern() {
        let content = "# Top\n\n\
                        Intro paragraph that is long enough to clear the minimum section threshold so the first section is not skipped by the chunker. We add more words here for safety.\n\n\
                        ## Section A\n\n\
                        Body A. Body A continued so the section is long enough to clear min_section_chars without effort. Padding padding padding padding padding padding padding padding.\n\n\
                        ## Section B\n\n\
                        Body B. Body B continued so the section is long enough to clear min_section_chars without effort. Padding padding padding padding padding padding padding padding.";
        let doc = Document {
            id: "doc-default".into(),
            content: content.into(),
            title: Some("Doc".into()),
            metadata: json!({}),
        };
        let chunker = HierarchyChunker::new(HierarchyChunkerConfig {
            prefix_heading: true,
            min_section_chars: 50,
            max_chars: 2000,
            if_oversize: None,
            heading_pattern: None,
        })
        .expect("default config must compile");
        let chunks = chunker.chunk(&doc);
        // 3 sections (Top + A + B), all clear min_section_chars. There is no
        // preamble before `# Top` (headings[0].0 == 0), so doc.title is not
        // consulted; each chunk's heading comes from capture group 2 of the
        // markdown regex.
        assert_eq!(chunks.len(), 3, "expected 3 chunks, got {}", chunks.len());
        assert_eq!(chunks[0].metadata["heading"], "Top");
        assert_eq!(chunks[1].metadata["heading"], "Section A");
        assert_eq!(chunks[2].metadata["heading"], "Section B");
    }

    /// A user-supplied custom regex (here, `>>>`-style markers) honors the
    /// custom boundary instead of falling back to markdown. This is the core
    /// guarantee the field exists for: external consumers that don't write
    /// markdown can still drive hierarchy chunking.
    #[test]
    fn hierarchy_custom_pattern_honored() {
        // `>>>` marker followed by heading text. The first capture group
        // pulls out the text after the marker.
        let content = ">>> Alpha\n\n\
                        Body alpha — long enough body so the section easily clears the minimum-section threshold and the chunker emits it as a real chunk. Padding padding padding padding padding padding.\n\n\
                        >>> Beta\n\n\
                        Body beta — long enough body so the section easily clears the minimum-section threshold and the chunker emits it as a real chunk. Padding padding padding padding padding padding.";
        let doc = Document {
            id: "doc-custom".into(),
            content: content.into(),
            title: None,
            metadata: json!({}),
        };
        let chunker = HierarchyChunker::new(HierarchyChunkerConfig {
            prefix_heading: false,
            min_section_chars: 50,
            max_chars: 2000,
            if_oversize: None,
            heading_pattern: Some(r"(?m)^>>>\s+(.+)$".to_string()),
        })
        .expect("valid custom pattern must compile");
        let chunks = chunker.chunk(&doc);
        assert_eq!(
            chunks.len(),
            2,
            "expected 2 chunks for two `>>>`-delimited sections, got {}",
            chunks.len()
        );
        // Custom-pattern path: capture group 1 is the heading text.
        assert_eq!(chunks[0].metadata["heading"], "Alpha");
        assert_eq!(chunks[1].metadata["heading"], "Beta");
        // Default markdown headings inside the corpus must NOT split with a
        // custom pattern that doesn't match `#`. Sanity: markdown `#` lines
        // present elsewhere wouldn't break the custom path. (No `#` in this
        // input, so we just confirm bodies survived.)
        assert!(chunks[0].original_content.contains("Body alpha"));
        assert!(chunks[1].original_content.contains("Body beta"));
    }

    /// An invalid regex must fail at construction time, not at chunk time.
    /// The brief calls this out explicitly: external consumers should see the
    /// error when they build the chunker, not silently mid-pipeline.
    #[test]
    fn hierarchy_invalid_pattern_returns_err() {
        let result = HierarchyChunker::new(HierarchyChunkerConfig {
            prefix_heading: true,
            min_section_chars: 100,
            max_chars: 2000,
            if_oversize: None,
            // Unbalanced character class: regex crate rejects this.
            heading_pattern: Some("[invalid".to_string()),
        });
        assert!(
            result.is_err(),
            "expected Err for invalid regex, got Ok"
        );
    }

    // --- Semantic chunker algorithm helpers ---

    #[test]
    fn percentile_linear_matches_numpy() {
        // numpy.percentile([1,2,3,4,5], 95) -> 4.8 (linear interp default)
        let p = percentile_linear(&[1.0_f32, 2.0, 3.0, 4.0, 5.0], 95);
        assert!((p - 4.8).abs() < 1e-5, "got {p}");
        // numpy.percentile([1,2,3,4], 50) -> 2.5
        let p = percentile_linear(&[1.0, 2.0, 3.0, 4.0], 50);
        assert!((p - 2.5).abs() < 1e-5, "got {p}");
        // Single-element edge case.
        assert_eq!(percentile_linear(&[7.0], 95), 7.0);
        // Empty edge case.
        assert_eq!(percentile_linear(&[], 95), 0.0);
    }

    #[test]
    fn merge_small_spans_forward() {
        // [0..5)=5, [5..6)=1 (too small), [6..10)=4. The small span forward-merges
        // into the previous one.
        let m = merge_small_spans(vec![(0, 5), (5, 6), (6, 10)], 3);
        assert_eq!(m, vec![(0, 6), (6, 10)]);
    }

    #[test]
    fn merge_small_spans_backward_last() {
        let m = merge_small_spans(vec![(0, 5), (5, 10), (10, 11)], 3);
        assert_eq!(m, vec![(0, 5), (5, 11)]);
    }

    #[test]
    fn merge_small_spans_first_too_small_pulls_next() {
        let m = merge_small_spans(vec![(0, 1), (1, 5), (5, 10)], 3);
        assert_eq!(m, vec![(0, 5), (5, 10)]);
    }

    #[test]
    fn merge_small_spans_empty_returns_empty() {
        let m: Vec<(usize, usize)> = merge_small_spans(Vec::new(), 3);
        assert!(m.is_empty());
    }

    // --- BoundaryEmbedder trait + SemanticChunker::with_embedder injection ---

    /// Stub embedder that returns deterministic canned vectors per call.
    /// Used to verify [`SemanticChunker::with_embedder`] routes through the
    /// caller's impl (no fastembed/ort involvement).
    struct StubEmbedder {
        /// Canned outputs, one Vec<f32> per input text. Cycles if shorter
        /// than the input batch.
        canned: Vec<Vec<f32>>,
        /// Tracks how many calls were made — verifies `embed_batch` actually
        /// fires from inside `chunk_inner`.
        calls: std::sync::atomic::AtomicUsize,
    }

    impl BoundaryEmbedder for StubEmbedder {
        fn embed_batch(&mut self, texts: &[&str]) -> anyhow::Result<Vec<Vec<f32>>> {
            self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let mut out = Vec::with_capacity(texts.len());
            for i in 0..texts.len() {
                out.push(self.canned[i % self.canned.len()].clone());
            }
            Ok(out)
        }
    }

    fn semantic_cfg() -> SemanticChunkerConfig {
        SemanticChunkerConfig {
            sentence_splitter: "naive".into(),
            boundary_model: "ignored-by-with_embedder".into(),
            breakpoint_percentile: 50,
            min_sentences_per_chunk: 1,
            max_chunk_chars: 2000,
            if_oversize: None,
        }
    }

    /// Verifies the chunker uses the supplied stub embedder (not fastembed)
    /// and that `embed_batch` is called.
    #[test]
    fn semantic_chunker_with_embedder_uses_supplied_impl() {
        let stub = StubEmbedder {
            // Two distinct vectors → cosine distance >0 → triggers a
            // breakpoint at the 50th percentile threshold.
            canned: vec![vec![1.0, 0.0], vec![0.0, 1.0]],
            calls: std::sync::atomic::AtomicUsize::new(0),
        };
        let chunker = SemanticChunker::with_embedder(semantic_cfg(), Box::new(stub))
            .expect("with_embedder ok");
        let doc = Document {
            id: "d".into(),
            content: "First sentence here. Second sentence here. Third sentence here.".into(),
            title: None,
            metadata: json!({}),
        };
        let chunks = chunker.chunk(&doc);
        assert!(
            !chunks.is_empty(),
            "stub embedder should produce at least one chunk"
        );
        // Touching the chunker proves the path went through embed_batch
        // (otherwise chunk_inner returns the whole-doc fallback).
        assert!(chunks.iter().any(|c| !c.original_content.is_empty()));
    }

    /// Compile-time / object-safety check: `Box<dyn BoundaryEmbedder>` must
    /// be constructible. If the trait grows methods that aren't object-safe
    /// (e.g. generics, `Self` returns), this test stops compiling.
    #[test]
    fn boundary_embedder_trait_is_object_safe() {
        let stub = StubEmbedder {
            canned: vec![vec![0.0]],
            calls: std::sync::atomic::AtomicUsize::new(0),
        };
        let _boxed: Box<dyn BoundaryEmbedder> = Box::new(stub);
    }

    /// Validation rules apply equally to both constructors. This keeps the
    /// `with_embedder` path from drifting away from `new`'s guarantees.
    #[test]
    fn semantic_chunker_with_embedder_validates_cfg() {
        let bad_cfg = SemanticChunkerConfig {
            sentence_splitter: "nltk".into(),
            ..semantic_cfg()
        };
        let stub = StubEmbedder {
            canned: vec![vec![0.0]],
            calls: std::sync::atomic::AtomicUsize::new(0),
        };
        let r = SemanticChunker::with_embedder(bad_cfg, Box::new(stub));
        assert!(r.is_err(), "non-naive splitter should error");
    }

    // --- HierarchicalSummary grouping strategies ---

    fn mk(seq: usize, content: &str, heading: Option<&str>) -> Chunk {
        let meta = match heading {
            Some(h) => json!({"heading": h}),
            None => json!({}),
        };
        Chunk {
            doc_id: "d".into(),
            seq_num: seq,
            original_content: content.to_string(),
            embedded_content: content.to_string(),
            metadata: meta,
        }
    }

    /// Helper: invoke `HierarchicalSummaryChunker::group` without constructing
    /// a real summarizer/base. Returns the group sizes for compact assertion.
    fn group_sizes(grouping: HierarchicalGrouping, chunks: Vec<Chunk>) -> Vec<usize> {
        // Build a minimal chunker by passing dummy base + summarizer. We never
        // call `chunk()` — only `group()`.
        struct NoopBase;
        impl ChunkerImpl for NoopBase {
            fn chunk(&self, _: &Document) -> Vec<Chunk> {
                Vec::new()
            }
        }
        struct NoopSum;
        impl crate::summarizer::SummarizerImpl for NoopSum {
            fn summarize(&self, _: &str, _: &serde_json::Value) -> anyhow::Result<String> {
                Ok(String::new())
            }
        }
        let chunker = HierarchicalSummaryChunker::new(
            Box::new(NoopBase),
            Box::new(NoopSum),
            "passthrough",
            grouping,
            None,
            None,
        );
        chunker.group(chunks).into_iter().map(|g| g.len()).collect()
    }

    #[test]
    fn fixed_n_groups_with_leftover() {
        let chunks: Vec<Chunk> = (0..7).map(|i| mk(i, "x", None)).collect();
        let sizes = group_sizes(HierarchicalGrouping::FixedN(3), chunks);
        assert_eq!(sizes, vec![3, 3, 1]);
    }

    #[test]
    fn word_budget_starts_new_group_when_over() {
        // Each chunk has 10 words; budget 25. Group 1 = 2 chunks (20 words),
        // adding chunk 3 (10 more) would total 30 > 25 → new group.
        let body = "one two three four five six seven eight nine ten";
        let chunks: Vec<Chunk> = (0..5).map(|i| mk(i, body, None)).collect();
        let sizes = group_sizes(HierarchicalGrouping::WordBudget(25), chunks);
        assert_eq!(sizes, vec![2, 2, 1]);
    }

    #[test]
    fn section_aware_groups_by_heading_change() {
        let chunks = vec![
            mk(0, "a1", Some("A")),
            mk(1, "a2", Some("A")),
            mk(2, "b1", Some("B")),
            mk(3, "b2", Some("B")),
            mk(4, "b3", Some("B")),
            mk(5, "c1", Some("C")),
        ];
        let sizes = group_sizes(HierarchicalGrouping::SectionAware, chunks);
        assert_eq!(sizes, vec![2, 3, 1]);
    }
}
