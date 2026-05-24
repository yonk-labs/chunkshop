//! Extractor stage. Mirrors `python/src/chunkshop/extractors/`.
//!
//! Six variants:
//!   - **none**: no-op.
//!   - **composite**: chains other extractors; concatenates tags, merges
//!     metadata with last-child-wins (matches Python's `dict.update`).
//!   - **rake_keywords**: hand-rolled RAKE — phrase = run between stopwords
//!     or punctuation; score = sum(word_degree) / word_freq; return top-k
//!     phrases of length ≥ min_chars. Uses a hardcoded English stopword list
//!     (~150 words). NOT byte-identical to Python's rake-nltk.
//!   - **lang_detect**: via the `whatlang` crate. Returns ISO 639-1 code +
//!     confidence ∈ [0, 1]. NOT byte-identical to Python's langdetect (the
//!     underlying detector is statistically different).
//!   - **keybert_phrases**, **spacy_entities**: stub — error at construction
//!     with "Python-only" message. Not in Rust scope until follow-up.

use anyhow::{anyhow, Result};
use serde_json::Value;
use std::collections::{HashMap, HashSet};

use crate::config::{
    CompositeExtractorConfig, ExtractorConfig, LangDetectExtractorConfig, NoneExtractorConfig,
    RakeKeywordsExtractorConfig,
};

/// Mirrors Python's `ExtractResult`. `tags` flow into the `tags text[]` column;
/// `metadata` is merged into each chunk's `metadata jsonb` per the runner's
/// chunker-wins layering.
#[derive(Debug, Clone, Default)]
pub struct ExtractResult {
    pub tags: Vec<String>,
    pub metadata: serde_json::Map<String, Value>,
}

pub trait ExtractorImpl: Send + Sync {
    fn extract(&self, text: &str) -> Result<ExtractResult>;
}

/// Build a boxed extractor from a config. Mirrors Python's `load_extractor`.
pub fn build_extractor(cfg: ExtractorConfig) -> Result<Box<dyn ExtractorImpl>> {
    match cfg {
        ExtractorConfig::None(c) => Ok(Box::new(NoneExtractor::new(c))),
        ExtractorConfig::Composite(c) => Ok(Box::new(CompositeExtractor::new(c)?)),
        ExtractorConfig::RakeKeywords(c) => Ok(Box::new(RakeKeywordsExtractor::new(c))),
        ExtractorConfig::LangDetect(c) => Ok(Box::new(LangDetectExtractor::new(c))),
        ExtractorConfig::KeybertPhrases(c) => {
            let _ = c;
            Err(anyhow!(
                "keybert_phrases extractor is Python-only in chunkshop-rs (the embedding-\
                 based candidate ranker is not ported yet). Run this YAML on Python or \
                 substitute another extractor."
            ))
        }
        ExtractorConfig::SpacyEntities(c) => {
            let _ = c;
            Err(anyhow!(
                "spacy_entities extractor is Python-only in chunkshop-rs (no spaCy NER pipeline \
                 in Rust). Run this YAML on Python or substitute another extractor."
            ))
        }
    }
}

pub struct NoneExtractor;

impl NoneExtractor {
    pub fn new(_cfg: NoneExtractorConfig) -> Self {
        Self
    }
}

impl ExtractorImpl for NoneExtractor {
    fn extract(&self, _text: &str) -> Result<ExtractResult> {
        Ok(ExtractResult::default())
    }
}

pub struct CompositeExtractor {
    children: Vec<Box<dyn ExtractorImpl>>,
}

impl CompositeExtractor {
    pub fn new(cfg: CompositeExtractorConfig) -> Result<Self> {
        let mut children: Vec<Box<dyn ExtractorImpl>> = Vec::with_capacity(cfg.extractors.len());
        for child_cfg in cfg.extractors {
            children.push(build_extractor(child_cfg)?);
        }
        Ok(Self { children })
    }
}

impl ExtractorImpl for CompositeExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        let mut tags: Vec<String> = Vec::new();
        let mut metadata: serde_json::Map<String, Value> = serde_json::Map::new();
        for child in &self.children {
            let r = child
                .extract(text)
                .map_err(|e| anyhow!("composite extractor: child raised: {e}"))?;
            tags.extend(r.tags);
            // Last-child-wins on key collision (Python's dict.update semantics).
            for (k, v) in r.metadata {
                metadata.insert(k, v);
            }
        }
        Ok(ExtractResult { tags, metadata })
    }
}

pub struct RakeKeywordsExtractor {
    cfg: RakeKeywordsExtractorConfig,
    stopwords: HashSet<&'static str>,
}

impl RakeKeywordsExtractor {
    pub fn new(cfg: RakeKeywordsExtractorConfig) -> Self {
        Self {
            cfg,
            stopwords: english_stopwords(),
        }
    }
}

impl ExtractorImpl for RakeKeywordsExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        if text.trim().is_empty() {
            return Ok(ExtractResult::default());
        }
        let phrases = rake_phrases(text, &self.stopwords);
        if phrases.is_empty() {
            return Ok(ExtractResult::default());
        }

        // Word frequencies + degrees (sum of phrase lengths a word participates in).
        let mut freq: HashMap<String, usize> = HashMap::new();
        let mut degree: HashMap<String, usize> = HashMap::new();
        for phrase in &phrases {
            let words: Vec<&str> = phrase.split_whitespace().collect();
            let len = words.len();
            for w in &words {
                let lower = w.to_lowercase();
                *freq.entry(lower.clone()).or_insert(0) += 1;
                // RAKE definition: degree = sum of phrase lengths (NOT len-1).
                *degree.entry(lower).or_insert(0) += len;
            }
        }

        // Score each phrase = sum of (degree / freq) per word. Phrase de-duped
        // first to match rake-nltk's behavior (it deduplicates pre-scoring).
        let mut seen: HashSet<String> = HashSet::new();
        let mut scored: Vec<(f64, String)> = Vec::new();
        for phrase in &phrases {
            let key = phrase.to_lowercase();
            if !seen.insert(key.clone()) {
                continue;
            }
            let score: f64 = phrase
                .split_whitespace()
                .map(|w| {
                    let lw = w.to_lowercase();
                    let d = *degree.get(&lw).unwrap_or(&0) as f64;
                    let f = *freq.get(&lw).unwrap_or(&1) as f64;
                    d / f.max(1.0)
                })
                .sum();
            scored.push((score, phrase.clone()));
        }
        // Sort desc by score; stable so equal-score phrases keep input order.
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        let tags: Vec<String> = scored
            .into_iter()
            .map(|(_score, phrase)| phrase)
            .filter(|p| p.chars().count() >= self.cfg.min_chars)
            .take(self.cfg.top_k)
            .collect();

        Ok(ExtractResult {
            tags,
            metadata: serde_json::Map::new(),
        })
    }
}

/// Split text into RAKE candidate phrases by walking word-by-word and breaking
/// on stopwords or punctuation. Tokens are kept lowercase-comparable for
/// stopword lookup but the original casing is preserved in the returned phrase.
fn rake_phrases(text: &str, stopwords: &HashSet<&'static str>) -> Vec<String> {
    let mut phrases: Vec<String> = Vec::new();
    let mut cur: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c.is_alphabetic() || c == '\'' || c == '-' || (c == '_' && !buf.is_empty()) {
            buf.push(c);
            continue;
        }
        // Word ended (whitespace, digit, or punctuation).
        if !buf.is_empty() {
            push_word(&mut cur, &mut buf, stopwords);
        }
        // Sentence-terminating punctuation flushes the current phrase.
        if matches!(
            c,
            '.' | ',' | ';' | ':' | '!' | '?' | '(' | ')' | '"' | '\n' | '\r'
        ) {
            flush_phrase(&mut phrases, &mut cur);
        }
        let _ = c;
    }
    if !buf.is_empty() {
        push_word(&mut cur, &mut buf, stopwords);
    }
    flush_phrase(&mut phrases, &mut cur);
    phrases
}

fn push_word(cur: &mut Vec<String>, buf: &mut String, stopwords: &HashSet<&'static str>) {
    let lower = buf.to_lowercase();
    if stopwords.contains(lower.as_str()) {
        // Stopword breaks the phrase.
        let drained = std::mem::take(cur);
        if !drained.is_empty() {
            // We're inside push_word — stash the broken phrase by signalling
            // the caller via cur becoming empty. Push the drained phrase here
            // by re-using `cur`'s storage isn't safe, so we use an out-band
            // accumulator: store it under a static thread-local? Simpler:
            // accept the small inefficiency of going through the same code
            // path as a flush. We emulate that by routing through a helper.
            // To keep the API simple, push directly through the closure below.
            push_built_phrase(drained);
        }
    } else {
        cur.push(buf.clone());
    }
    buf.clear();
}

// Thread-local accumulator for phrases broken by an in-word stopword.
// We use a thread-local because rewiring `push_word` to return phrases
// would touch every caller. This is acceptable for a fast extractor.
thread_local! {
    static RAKE_OUT: std::cell::RefCell<Vec<String>> = const { std::cell::RefCell::new(Vec::new()) };
}

fn push_built_phrase(words: Vec<String>) {
    if words.is_empty() {
        return;
    }
    let phrase = words.join(" ");
    RAKE_OUT.with(|out| out.borrow_mut().push(phrase));
}

fn flush_phrase(phrases: &mut Vec<String>, cur: &mut Vec<String>) {
    // Drain any thread-local-stashed phrases first (from stopword splits).
    RAKE_OUT.with(|out| {
        let mut o = out.borrow_mut();
        phrases.extend(o.drain(..));
    });
    if !cur.is_empty() {
        phrases.push(std::mem::take(cur).join(" "));
    }
}

fn english_stopwords() -> HashSet<&'static str> {
    // Curated English stopword list. Differs from NLTK's (~180 words) but
    // covers the same high-frequency tokens. Cross-language byte-identical
    // RAKE output is NOT promised.
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shall",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    ]
    .iter()
    .copied()
    .collect()
}

pub struct LangDetectExtractor;

impl LangDetectExtractor {
    pub fn new(_cfg: LangDetectExtractorConfig) -> Self {
        Self
    }
}

impl ExtractorImpl for LangDetectExtractor {
    fn extract(&self, text: &str) -> Result<ExtractResult> {
        if text.trim().is_empty() {
            let mut metadata = serde_json::Map::new();
            metadata.insert("language".to_string(), Value::Null);
            metadata.insert("language_confidence".to_string(), Value::from(0.0));
            return Ok(ExtractResult {
                tags: Vec::new(),
                metadata,
            });
        }
        let info = whatlang::detect(text);
        let mut metadata = serde_json::Map::new();
        match info {
            Some(info) => {
                let iso639_1 = lang_to_iso639_1(info.lang());
                let confidence = info.confidence();
                metadata.insert(
                    "language".to_string(),
                    iso639_1
                        .map(|s| Value::String(s.to_string()))
                        .unwrap_or(Value::Null),
                );
                metadata.insert(
                    "language_confidence".to_string(),
                    serde_json::Number::from_f64(confidence)
                        .map(Value::Number)
                        .unwrap_or(Value::from(0.0)),
                );
            }
            None => {
                metadata.insert("language".to_string(), Value::Null);
                metadata.insert("language_confidence".to_string(), Value::from(0.0));
            }
        }
        Ok(ExtractResult {
            tags: Vec::new(),
            metadata,
        })
    }
}

/// Convert whatlang's ISO 639-3 to ISO 639-1 (matches Python's langdetect).
/// Returns None if no 639-1 mapping exists for that language.
fn lang_to_iso639_1(lang: whatlang::Lang) -> Option<&'static str> {
    use whatlang::Lang;
    Some(match lang {
        Lang::Eng => "en",
        Lang::Spa => "es",
        Lang::Fra => "fr",
        Lang::Deu => "de",
        Lang::Por => "pt",
        Lang::Ita => "it",
        Lang::Nld => "nl",
        Lang::Pol => "pl",
        Lang::Rus => "ru",
        Lang::Ukr => "uk",
        Lang::Ces => "cs",
        Lang::Slv => "sl",
        Lang::Swe => "sv",
        Lang::Dan => "da",
        Lang::Nob => "no",
        Lang::Fin => "fi",
        Lang::Hun => "hu",
        Lang::Tur => "tr",
        Lang::Ara => "ar",
        Lang::Heb => "he",
        Lang::Hin => "hi",
        Lang::Ben => "bn",
        Lang::Tha => "th",
        Lang::Vie => "vi",
        Lang::Ind => "id",
        Lang::Jpn => "ja",
        Lang::Kor => "ko",
        Lang::Cmn => "zh",
        Lang::Ell => "el",
        Lang::Ron => "ro",
        Lang::Bul => "bg",
        Lang::Hrv => "hr",
        Lang::Srp => "sr",
        Lang::Slk => "sk",
        Lang::Lit => "lt",
        Lang::Lav => "lv",
        Lang::Est => "et",
        Lang::Mar => "mr",
        Lang::Tam => "ta",
        Lang::Tel => "te",
        Lang::Urd => "ur",
        Lang::Pes => "fa",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{KeybertPhrasesExtractorConfig, SpacyEntitiesExtractorConfig};

    #[test]
    fn none_returns_empty() {
        let e = NoneExtractor::new(NoneExtractorConfig::default());
        let r = e.extract("anything").unwrap();
        assert!(r.tags.is_empty());
        assert!(r.metadata.is_empty());
    }

    #[test]
    fn composite_chains_two_nones_returns_empty() {
        let cfg = CompositeExtractorConfig {
            extractors: vec![
                ExtractorConfig::None(NoneExtractorConfig::default()),
                ExtractorConfig::None(NoneExtractorConfig::default()),
            ],
        };
        let e = CompositeExtractor::new(cfg).unwrap();
        let r = e.extract("text").unwrap();
        assert!(r.tags.is_empty());
        assert!(r.metadata.is_empty());
    }

    #[test]
    fn composite_with_keybert_errors_at_build() {
        let cfg = CompositeExtractorConfig {
            extractors: vec![ExtractorConfig::KeybertPhrases(
                KeybertPhrasesExtractorConfig {
                    top_k: 5,
                    model_name: "x".into(),
                    keyphrase_ngram_range: (1, 2),
                },
            )],
        };
        let err = match CompositeExtractor::new(cfg) {
            Ok(_) => panic!("expected error"),
            Err(e) => e.to_string(),
        };
        assert!(err.contains("keybert_phrases"), "got: {err}");
        assert!(err.contains("Python-only"), "got: {err}");
    }

    #[test]
    fn rake_returns_phrases_for_english_text() {
        let cfg = RakeKeywordsExtractorConfig {
            top_k: 5,
            min_chars: 3,
        };
        let e = RakeKeywordsExtractor::new(cfg);
        let text = "Compatibility of systems of linear constraints over the set of natural \
                    numbers. Criteria of compatibility of a system of linear Diophantine \
                    equations, strict inequations, and nonstrict inequations are considered.";
        let r = e.extract(text).unwrap();
        assert!(!r.tags.is_empty(), "rake should return some phrases");
        assert!(
            r.tags.len() <= 5,
            "expected <= top_k=5 tags, got {}",
            r.tags.len()
        );
        // Each phrase should be at least min_chars=3 chars.
        for tag in &r.tags {
            assert!(
                tag.chars().count() >= 3,
                "tag {tag:?} below min_chars threshold"
            );
        }
    }

    #[test]
    fn rake_empty_input_returns_empty() {
        let e = RakeKeywordsExtractor::new(RakeKeywordsExtractorConfig {
            top_k: 5,
            min_chars: 3,
        });
        assert!(e.extract("").unwrap().tags.is_empty());
        assert!(e.extract("   \n  ").unwrap().tags.is_empty());
    }

    #[test]
    fn lang_detect_english_returns_en() {
        let e = LangDetectExtractor::new(LangDetectExtractorConfig {
            backend: "langdetect".into(),
        });
        let r = e
            .extract(
                "The quick brown fox jumps over the lazy dog. \
                 This is a sample English sentence to identify.",
            )
            .unwrap();
        let lang = r.metadata.get("language").and_then(|v| v.as_str());
        assert_eq!(lang, Some("en"), "expected English, got {lang:?}");
        let conf = r
            .metadata
            .get("language_confidence")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        assert!(conf >= 0.0 && conf <= 1.0);
    }

    #[test]
    fn lang_detect_spanish_returns_es() {
        let e = LangDetectExtractor::new(LangDetectExtractorConfig {
            backend: "langdetect".into(),
        });
        let r = e
            .extract(
                "El zorro marrón rápido salta sobre el perro perezoso. \
                 Esta es una oración de muestra en español para identificar.",
            )
            .unwrap();
        let lang = r.metadata.get("language").and_then(|v| v.as_str());
        assert_eq!(lang, Some("es"), "expected Spanish, got {lang:?}");
    }

    #[test]
    fn lang_detect_empty_returns_null() {
        let e = LangDetectExtractor::new(LangDetectExtractorConfig {
            backend: "langdetect".into(),
        });
        let r = e.extract("").unwrap();
        assert!(r.metadata.get("language").unwrap().is_null());
        assert_eq!(
            r.metadata.get("language_confidence").unwrap().as_f64(),
            Some(0.0)
        );
    }

    #[test]
    fn keybert_phrases_errors_at_build() {
        let cfg = ExtractorConfig::KeybertPhrases(KeybertPhrasesExtractorConfig {
            top_k: 5,
            model_name: "x".into(),
            keyphrase_ngram_range: (1, 2),
        });
        let err = match build_extractor(cfg) {
            Ok(_) => panic!("expected error"),
            Err(e) => e.to_string(),
        };
        assert!(err.contains("Python-only"));
    }

    #[test]
    fn spacy_entities_errors_at_build() {
        let cfg = ExtractorConfig::SpacyEntities(SpacyEntitiesExtractorConfig {
            model: "en_core_web_sm".into(),
            label_whitelist: vec!["ORG".into()],
        });
        let err = match build_extractor(cfg) {
            Ok(_) => panic!("expected error"),
            Err(e) => e.to_string(),
        };
        assert!(err.contains("Python-only"));
    }
}
