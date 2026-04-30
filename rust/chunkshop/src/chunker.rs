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
    ChunkerConfig, FastembedEmbedderConfig, FixedOverlapChunkerConfig, HierarchyChunkerConfig,
    SemanticChunkerConfig, SentenceAwareChunkerConfig,
};
use crate::embedder::FastembedEmbedder;
use crate::sentence_split::naive_sentences;
use crate::source::Document;

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
        c.embedded_content.chars().count() > ceiling
            || c.original_content.chars().count() > ceiling
    }
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
}

impl FixedOverlapChunker {
    pub fn new(cfg: FixedOverlapChunkerConfig) -> anyhow::Result<Self> {
        if cfg.window_words == 0 || cfg.step_words == 0 {
            return Err(anyhow::anyhow!(
                "window_words and step_words must be positive"
            ));
        }
        Ok(Self { cfg })
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
        chunks
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
}

impl NeighborExpandChunker {
    /// Construct a wrapper around `base` with the given window size. The full
    /// `NeighborExpandChunkerConfig` lives in YAML; the runner extracts the
    /// `base` ChunkerConfig and recursively builds it before constructing
    /// this wrapper.
    pub fn new(window: usize, base: Box<dyn ChunkerImpl + Send + Sync>) -> Self {
        Self { window, base }
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
        out
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
pub struct SemanticChunker {
    cfg: SemanticChunkerConfig,
    boundary: std::sync::Mutex<FastembedEmbedder>,
}

impl SemanticChunker {
    pub fn new(cfg: SemanticChunkerConfig) -> anyhow::Result<Self> {
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
            // No boundaries possible. Still respect max_chunk_chars.
            let mut chunks = Vec::new();
            for sub in self.split_if_too_large(&sentences[0], (0, 1)) {
                let seq = chunks.len();
                chunks.push(self.mk_chunk(&doc.id, seq, &sub));
            }
            return chunks;
        }

        let texts: Vec<String> = sentences.clone();
        let embeddings = match self.boundary.lock().expect("poisoned mutex").embed(texts) {
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
}

impl SummaryEmbedChunker {
    pub fn new(
        base: Box<dyn ChunkerImpl + Send + Sync>,
        summarizer: Box<dyn crate::summarizer::SummarizerImpl>,
        mode: &'static str,
    ) -> Self {
        Self { base, summarizer, mode }
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
        out
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
    ) -> Self {
        Self {
            base,
            summarizer,
            mode,
            grouping,
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
            coarse_meta.insert(
                "group_id".to_string(),
                serde_json::Value::String(group_id),
            );
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
        out
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
/// Returns the percentile **value** (not the index).
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
/// last span if needed. Mirrors Python's `_merge_small`.
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
