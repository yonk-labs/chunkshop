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
            let mut top: Vec<(usize, &String)> =
                scored.into_iter().take(3).map(|(i, s, _)| (i, s)).collect();
            top.sort_by(|a, b| a.0.cmp(&b.0));
            top.into_iter()
                .map(|(_, s)| s.clone())
                .collect::<Vec<_>>()
                .join(" ")
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

// --- LedeConsolidator — salient-sentence facts (feature = "lede") -----------

/// `mode: lede`. Selects salient sentences via lede 0.5 `key_facts` and emits
/// them as facts with empty SVO fields (the lede non-spaCy path produces
/// sentences, not triples — matching Python's `lede_facts`), `support_span` =
/// the sentence, and rank-decay `confidence` = round(1 - i/n, 3). Facts below
/// `confidence_floor` are dropped. `summary` is empty (Python's lede
/// consolidator only fills it when an optional summarizer slot is configured).
pub struct LedeConsolidator {
    #[allow(dead_code)] // cfg used only under feature = "lede"
    cfg: crate::config::LedeConsolidatorConfig,
}

impl LedeConsolidator {
    pub fn new(cfg: crate::config::LedeConsolidatorConfig) -> Self {
        Self { cfg }
    }
}

impl Consolidator for LedeConsolidator {
    #[cfg(feature = "lede")]
    fn consolidate(&self, episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput> {
        let cleaned = strip_role_tags(episode.text);
        let facts_text = lede::extract::key_facts::key_facts(&cleaned, self.cfg.max_facts);
        let n = facts_text.len();
        let facts: Vec<FactTriple> = facts_text
            .into_iter()
            .enumerate()
            .filter_map(|(i, sentence)| {
                let confidence = if n == 0 {
                    0.0
                } else {
                    ((1.0 - (i as f64 / n as f64)) * 1000.0).round() / 1000.0
                };
                if confidence < self.cfg.confidence_floor {
                    return None;
                }
                Some(FactTriple {
                    subject: String::new(),
                    predicate: String::new(),
                    object: String::new(),
                    support_span: Some(sentence),
                    confidence: Some(confidence),
                })
            })
            .collect();
        Ok(ConsolidationOutput {
            summary: String::new(),
            facts,
        })
    }

    #[cfg(not(feature = "lede"))]
    fn consolidate(&self, _episode: &EpisodeInput<'_>) -> Result<ConsolidationOutput> {
        anyhow::bail!(
            "`lede` consolidator mode is gated behind the `lede` cargo feature; \
             build with --features lede or run this YAML on Python."
        )
    }

    fn mode(&self) -> &'static str {
        "lede"
    }
}

/// Factory: build the wired Consolidator from its config variant.
pub fn build_consolidator(cfg: &ConsolidatorConfig) -> Box<dyn Consolidator> {
    match cfg {
        ConsolidatorConfig::Extractive(c) => Box::new(ExtractiveConsolidator::new(c.clone())),
        ConsolidatorConfig::Lede(c) => Box::new(LedeConsolidator::new(c.clone())),
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
        let out = c
            .consolidate(&ep("[user] We use Redis for the queue."))
            .unwrap();
        assert!(out.facts.is_empty());
        assert!(out.summary.contains("Redis"));
        assert!(!out.summary.starts_with("[user]")); // role tag stripped
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
        assert!(
            sentence_count <= 3,
            "expected <=3 sentences; got: {:?}",
            out.summary
        );
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

    #[cfg(feature = "lede")]
    #[test]
    fn lede_consolidator_rank_decay_and_empty_svo() {
        let c = LedeConsolidator::new(crate::config::LedeConsolidatorConfig {
            max_facts: 10,
            confidence_floor: 0.0,
        });
        let out = c
            .consolidate(&ep(
                "[user] Acme raised $5 million in 2023. The team grew to 40 engineers. \
                 Revenue increased 300 percent. The Berlin office opened on 2024-01-15.",
            ))
            .unwrap();
        assert_eq!(c.mode(), "lede");
        assert!(!out.facts.is_empty());
        let f0 = &out.facts[0];
        assert_eq!(f0.subject, "");
        assert_eq!(f0.predicate, "");
        assert_eq!(f0.object, "");
        assert!(f0.support_span.is_some());
        // rank-decay: confidence non-increasing across facts.
        let confs: Vec<f64> = out.facts.iter().map(|f| f.confidence.unwrap()).collect();
        assert!(confs.windows(2).all(|w| w[0] >= w[1]));
        assert!(out.summary.is_empty());
    }

    #[cfg(feature = "lede")]
    #[test]
    fn lede_consolidator_floor_filters() {
        let c = LedeConsolidator::new(crate::config::LedeConsolidatorConfig {
            max_facts: 10,
            confidence_floor: 0.99,
        });
        let out = c
            .consolidate(&ep(
                "[user] Q1 revenue was $10 million. Q2 revenue hit $12 million. \
                 Headcount reached 200 people. Churn fell 5 percent.",
            ))
            .unwrap();
        // Floor 0.99 keeps only the first (confidence 1.0) of the rank-decay set.
        assert!(out.facts.iter().all(|f| f.confidence.unwrap() >= 0.99));
        assert!(!out.facts.is_empty());
    }

    #[cfg(feature = "lede")]
    #[test]
    fn build_consolidator_dispatches_lede() {
        let cfg = ConsolidatorConfig::Lede(crate::config::LedeConsolidatorConfig {
            max_facts: 5,
            confidence_floor: 0.0,
        });
        let c = build_consolidator(&cfg);
        assert_eq!(c.mode(), "lede");
    }
}
