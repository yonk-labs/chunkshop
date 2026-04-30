//! Deterministic key derivation for combo table names. Byte-identical to
//! `python/src/chunkshop/bakeoff/keys.py` — same key in, same key out, so
//! both implementations write to the same per-combo table for cross-language
//! parity tests.
//!
//! Same regex stripping rules: lowercase the model_name's tail, replace
//! every non-`[a-z0-9]` run with `_`, trim leading/trailing `_`.

use std::sync::OnceLock;

use anyhow::{anyhow, Result};
use regex::Regex;

use crate::config::{ChunkerConfig, FastembedEmbedderConfig};

fn id_safe() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[^a-z0-9]+").unwrap())
}

/// `Xenova/bge-base-en-v1.5-int8` → `bge_base_en_v1_5_int8`.
pub fn embedder_key(cfg: &FastembedEmbedderConfig) -> String {
    let short = cfg
        .model_name
        .rsplit('/')
        .next()
        .unwrap_or(&cfg.model_name)
        .to_ascii_lowercase();
    id_safe()
        .replace_all(&short, "_")
        .trim_matches('_')
        .to_string()
}

/// One key per chunker shape. Params that change behavior land in the key
/// so two `fixed_overlap` rows with different windows don't collide on
/// the same table name.
pub fn chunker_key(cfg: &ChunkerConfig) -> Result<String> {
    Ok(match cfg {
        ChunkerConfig::Hierarchy(_) => "hierarchy".to_string(),
        ChunkerConfig::SentenceAware(_) => "sentence_aware".to_string(),
        ChunkerConfig::FixedOverlap(c) => {
            format!("fixed_overlap_w{}_s{}", c.window_words, c.step_words)
        }
        ChunkerConfig::NeighborExpand(c) => {
            format!("neighbor_expand_w{}_over_{}", c.window, chunker_key(&c.base)?)
        }
        ChunkerConfig::Semantic(_) => {
            return Err(anyhow!(
                "semantic chunker is not in the bakeoff matrix today; \
                 it has ~1e-3 ORT cosine drift that flips close combos. \
                 Add it back once the parity envelope is wider."
            ));
        }
        ChunkerConfig::SummaryEmbed(_) | ChunkerConfig::HierarchicalSummary(_) => {
            return Err(anyhow!(
                "summary-wrapping chunkers (summary_embed, hierarchical_summary) \
                 are not supported in the bakeoff matrix yet. They wrap a base \
                 chunker and a summarizer — add a key derivation that includes \
                 both before exposing them."
            ));
        }
    })
}

/// Combo table name: `{chunker_key}__{embedder_key}`.
pub fn combo_table(chunker: &ChunkerConfig, embedder: &FastembedEmbedderConfig) -> Result<String> {
    Ok(format!("{}__{}", chunker_key(chunker)?, embedder_key(embedder)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{
        FastembedEmbedderConfig, FixedOverlapChunkerConfig, HierarchyChunkerConfig,
        NeighborExpandChunkerConfig, SentenceAwareChunkerConfig,
    };

    fn emb(model: &str) -> FastembedEmbedderConfig {
        FastembedEmbedderConfig {
            model_name: model.to_string(),
            dim: 384,
            batch_size: 64,
            threads: None,
            hf_repo: None,
            onnx_path: None,
            pooling: "cls".to_string(),
            additional_files: vec![],
        }
    }

    #[test]
    fn embedder_key_strips_org_and_punctuation() {
        assert_eq!(embedder_key(&emb("Xenova/bge-base-en-v1.5-int8")), "bge_base_en_v1_5_int8");
        assert_eq!(embedder_key(&emb("Xenova/bge-small-en-v1.5-int8")), "bge_small_en_v1_5_int8");
        assert_eq!(embedder_key(&emb("nomic-ai/nomic-embed-text-v1.5-Q")), "nomic_embed_text_v1_5_q");
    }

    #[test]
    fn chunker_key_includes_params() {
        let h = ChunkerConfig::Hierarchy(HierarchyChunkerConfig {
            prefix_heading: true,
            min_section_chars: 100,
            max_chars: 2000,
            if_oversize: None,
        });
        assert_eq!(chunker_key(&h).unwrap(), "hierarchy");

        let s = ChunkerConfig::SentenceAware(SentenceAwareChunkerConfig {
            doc_type: "markdown".into(),
            max_chars: 2000,
            min_chars: 100,
            if_oversize: None,
        });
        assert_eq!(chunker_key(&s).unwrap(), "sentence_aware");

        let f = ChunkerConfig::FixedOverlap(FixedOverlapChunkerConfig {
            window_words: 300,
            step_words: 150,
            max_chars: None,
            if_oversize: None,
        });
        assert_eq!(chunker_key(&f).unwrap(), "fixed_overlap_w300_s150");

        let n = ChunkerConfig::NeighborExpand(NeighborExpandChunkerConfig {
            base: Box::new(ChunkerConfig::Hierarchy(HierarchyChunkerConfig {
                prefix_heading: true,
                min_section_chars: 100,
                max_chars: 2000,
                if_oversize: None,
            })),
            window: 1,
            max_chars: None,
            if_oversize: None,
        });
        assert_eq!(
            chunker_key(&n).unwrap(),
            "neighbor_expand_w1_over_hierarchy"
        );
    }

    #[test]
    fn combo_table_concatenates() {
        let h = ChunkerConfig::Hierarchy(HierarchyChunkerConfig {
            prefix_heading: true,
            min_section_chars: 100,
            max_chars: 2000,
            if_oversize: None,
        });
        assert_eq!(
            combo_table(&h, &emb("Xenova/bge-small-en-v1.5-int8")).unwrap(),
            "hierarchy__bge_small_en_v1_5_int8"
        );
    }
}
