//! RM-A consolidators — the structured-extraction seam called by
//! `ConsolidationChunker` (Task 8). Mirror of Python
//! `chunkshop.consolidators`. v1 ships:
//!
//! - `Consolidator` trait — anyone (LLM, rule-based, anything) implements it.
//! - `ExtractiveConsolidator` — zero-network default. Produces a summary
//!   by selecting top-N sentences; emits no facts (matches Python's
//!   default: structured triples require a user-wired LLM or rule-based
//!   consolidator).
//!
//! Diverges from Python in shape: Python's YAML names a callable via
//! `module:`/`function:`; Rust's YAML names a built-in `mode:` (currently
//! only `extractive`). Custom impls are wired in code by the consumer,
//! not via YAML — see RM-A spec §3.4.

use anyhow::Result;

use crate::config::{ConsolidatorConfig, ExtractiveConsolidatorConfig};

/// Input to a `Consolidator`: one episode (already framed by
/// `SessionEpisodeFramer`).
pub struct EpisodeInput<'a> {
    /// Reconstructed episode text (role-tagged turns joined with `\n`).
    pub text: &'a str,
    /// 1-indexed seq within the session.
    pub frame_seq: u64,
    pub session_id: &'a str,
    /// First/last event epoch seconds for the episode (matches the framer's
    /// `episode_start_ts` / `episode_end_ts`).
    pub episode_start_ts: f64,
    pub episode_end_ts: f64,
}

/// One SPO fact extracted by a `Consolidator`.
#[derive(Debug, Clone)]
pub struct FactTriple {
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub support_span: Option<String>,
    pub confidence: Option<f64>,
}

/// Output of a `Consolidator::consolidate` call.
#[derive(Debug, Clone, Default)]
pub struct ConsolidationOutput {
    pub summary: String,
    pub facts: Vec<FactTriple>,
}

/// User-extensible consolidator interface. `ConsolidationChunker` calls
/// `consolidate` per episode; an `Err` triggers the O4 passthrough fallback
/// (episode chunk only, zero facts, `consolidation_error` metadata stamp).
pub trait Consolidator: Send + Sync {
    fn consolidate(&self, episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput>;
    /// Stable identifier used by `MemorySink` to populate the `extractor`
    /// promote column (pg-raggraph contract).
    fn mode(&self) -> &'static str;
}

// --- ExtractiveConsolidator — zero-network default --------------------------

/// Selects sentences for the summary by length-weighted lexical scoring.
/// No facts emitted (matches Python's default: structured triples require
/// a richer consolidator). Stable & deterministic across runs.
pub struct ExtractiveConsolidator;

impl ExtractiveConsolidator {
    pub fn new(_cfg: ExtractiveConsolidatorConfig) -> Self {
        Self
    }
}

impl Consolidator for ExtractiveConsolidator {
    fn consolidate(&self, episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput> {
        let cleaned = strip_role_tags(episode.text);
        let sentences = split_sentences(&cleaned);
        let n = sentences.len();
        // For short episodes (<=3 sentences), use the full cleaned text. For
        // longer, pick up to 3 highest-scoring sentences in original order.
        let summary = if n <= 3 {
            cleaned.trim().to_string()
        } else {
            let mut scored: Vec<(usize, &String, usize)> = sentences
                .iter()
                .enumerate()
                .map(|(i, s)| (i, s, s.split_whitespace().count()))
                .collect();
            // Sort by length descending then index ascending; take top 3,
            // then re-sort by original index for stable output order.
            scored.sort_by(|a, b| b.2.cmp(&a.2).then(a.0.cmp(&b.0)));
            let mut top: Vec<(usize, &String)> = scored
                .into_iter()
                .take(3)
                .map(|(i, s, _)| (i, s))
                .collect();
            top.sort_by(|a, b| a.0.cmp(&b.0));
            top.into_iter().map(|(_, s)| s.clone()).collect::<Vec<_>>().join(" ")
        };
        Ok(ConsolidationOutput {
            summary,
            facts: Vec::new(),
        })
    }

    fn mode(&self) -> &'static str {
        "extractive"
    }
}

/// Strip leading `[role]` / `[role/tool]` tags inserted by
/// `SessionEpisodeFramer` so the consolidator's input is plain text.
/// Mirror of Python `527f9f4` ("strip role tags even with leading
/// whitespace") — leading whitespace before `[` is tolerated.
fn strip_role_tags(text: &str) -> String {
    text.lines()
        .map(|l| {
            let trimmed = l.trim_start();
            if trimmed.starts_with('[') {
                if let Some(end) = trimmed.find(']') {
                    return trimmed[end + 1..].trim_start().to_string();
                }
            }
            l.to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Cheap sentence splitter: splits on `.`, `!`, `?` followed by whitespace
/// or end-of-string. Matches the granularity Python's extractive default
/// uses; deliberately simple — sentence-aware chunker is the heavy hitter.
fn split_sentences(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        cur.push(c);
        if matches!(c, '.' | '!' | '?') {
            let next_is_ws_or_end = chars.peek().map(|n| n.is_whitespace()).unwrap_or(true);
            if next_is_ws_or_end {
                let s = cur.trim().to_string();
                if !s.is_empty() {
                    out.push(s);
                }
                cur.clear();
            }
        }
    }
    let tail = cur.trim().to_string();
    if !tail.is_empty() {
        out.push(tail);
    }
    out
}

/// Factory: build the wired Consolidator from its config variant.
pub fn build_consolidator(cfg: &ConsolidatorConfig) -> Box<dyn Consolidator> {
    match cfg {
        ConsolidatorConfig::Extractive(c) => Box::new(ExtractiveConsolidator::new(c.clone())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ep(text: &str) -> EpisodeInput<'_> {
        EpisodeInput {
            text,
            frame_seq: 0,
            session_id: "s1",
            episode_start_ts: 0.0,
            episode_end_ts: 0.0,
        }
    }

    #[test]
    fn extractive_emits_summary_no_facts_on_short_text() {
        let c = ExtractiveConsolidator;
        let out = c.consolidate(&ep("[user] We use Redis for the queue.")).unwrap();
        assert!(out.facts.is_empty());
        assert!(out.summary.contains("Redis"));
        assert!(!out.summary.starts_with("[user]"));      // role tag stripped
    }

    #[test]
    fn extractive_is_deterministic_on_same_input() {
        let c = ExtractiveConsolidator;
        let text = "[user] One. Two longer sentence here. Three. Four also longer.";
        let a = c.consolidate(&ep(text)).unwrap();
        let b = c.consolidate(&ep(text)).unwrap();
        assert_eq!(a.summary, b.summary);
        assert_eq!(a.facts.len(), b.facts.len());
    }

    #[test]
    fn extractive_strips_role_tags_with_leading_whitespace() {
        // Mirror of Python fix `527f9f4` — leading whitespace before `[role]`
        // must still get the tag stripped.
        let c = ExtractiveConsolidator;
        let out = c.consolidate(&ep("    [user] hello there")).unwrap();
        assert!(!out.summary.contains("[user]"));
        assert!(out.summary.contains("hello"));
    }

    #[test]
    fn extractive_long_text_selects_top_sentences() {
        let c = ExtractiveConsolidator;
        let text = "short. \
                    A longer sentence with more words present here. \
                    tiny. \
                    Another reasonably long sentence to consider. \
                    The longest sentence in this set goes here with quite a few words.";
        let out = c.consolidate(&ep(text)).unwrap();
        // Output should include "longest" but not necessarily "short" or "tiny".
        assert!(out.summary.contains("longest"));
        // 3-sentence cap on long input.
        let sentence_count = out.summary.matches('.').count();
        assert!(sentence_count <= 3, "expected <=3 sentences; got: {:?}", out.summary);
    }

    #[test]
    fn mode_is_extractive_for_pgrg_extractor_column() {
        let c = ExtractiveConsolidator;
        assert_eq!(c.mode(), "extractive");
    }

    #[test]
    fn build_consolidator_dispatches_extractive() {
        let cfg = ConsolidatorConfig::Extractive(ExtractiveConsolidatorConfig {});
        let c = build_consolidator(&cfg);
        let out = c.consolidate(&ep("[user] hi.")).unwrap();
        assert!(out.facts.is_empty());
        assert_eq!(c.mode(), "extractive");
    }
}
