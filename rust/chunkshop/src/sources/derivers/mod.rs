//! Media derivers: turn non-text bytes into text plus metadata. Mirrors
//! `python/src/chunkshop/sources/derivers/`. Every deriver's output is
//! stamped with provenance and memoised on (content_hash, derivation_hash)
//! so nothing is derived twice and nothing is written without attribution.
//!
//! Not `media`-gated itself — `ParseResult`/`Deriver`/`CacheableDeriver` and
//! provenance are always available (see `sources::mod`'s doc comment).
//! `image_meta` IS `media`-gated (it needs the `image`/`png`/`gif` crates);
//! `clip_tags` needs `media` AND `embedder-hub` (hf-hub auto-download of
//! both CLIP towers).

pub mod cache;
pub mod provenance;

#[cfg(feature = "media")]
pub mod image_meta;

#[cfg(all(feature = "media", feature = "embedder-hub"))]
pub mod clip_tags;

use std::rc::Rc;

use crate::config::DeriverConfig;
use crate::sources::parsers::CacheableDeriver;

/// Materialize a `DeriverConfig` into a `CacheableDeriver` trait object.
///
/// `Rc`, not `Box`: a single `derive:` pattern key like `"png|jpg"` fans out
/// to multiple per-extension entries in `FilesSource`'s deriver table, all
/// of which must share ONE constructed deriver instance rather than
/// re-parsing config per extension or requiring `dyn Deriver: Clone` (which
/// trait objects can't derive generically). `Rc` clones cheaply for this
/// fan-out. Sharing one instance also matters for `ClipTagsDeriver`, which
/// caches its two ~0.3GB CLIP towers internally — a per-extension instance
/// would load them once per extension.
///
/// `FilesSource` never crosses a `tokio::spawn` boundary — `run_cell` is
/// `.await`ed directly in `main.rs` and nothing in this codebase calls
/// `tokio::spawn` today — so `Rc`'s `!Send`/`!Sync` is safe here; there is
/// no async trait bound on `AnySource`/`Deriver` that requires `Send`.
///
/// Fallible, and deliberately so: an unbuildable `derive:` block must fail
/// at config-load time with a named reason (empty vocabulary, an
/// unsupported model override, a missing Cargo feature), never silently
/// fall back to a different rung. A rung-0 result stamped with rung-1
/// provenance would be a provenance record that lies.
#[cfg(feature = "media")]
pub fn load_deriver(cfg: &DeriverConfig) -> anyhow::Result<Rc<dyn CacheableDeriver>> {
    Ok(match cfg {
        DeriverConfig::ImageMeta(c) => Rc::new(image_meta::ImageMetaDeriver {
            path_segments: c.path_segments,
            segment_depth: c.segment_depth as usize,
        }),
        #[cfg(feature = "embedder-hub")]
        DeriverConfig::ClipTags(c) => Rc::new(clip_tags::ClipTagsDeriver::new(
            c.vocabulary.clone(),
            c.top_k,
            c.min_score,
            c.vision_model.clone(),
            c.text_model.clone(),
        )?),
        // `media` alone doesn't build the CLIP towers — `embedder-hub`
        // supplies hf-hub + the fastembed download path. Per the `media`
        // feature's own note in Cargo.toml, this is a config-load failure
        // with a clear message (F14/NN-6), not a missing-symbol compile
        // error for users who only wanted `image_meta`, and emphatically
        // not a silent downgrade to rung 0.
        #[cfg(not(feature = "embedder-hub"))]
        DeriverConfig::ClipTags(_) => {
            return Err(missing_feature_error("clip_tags", "media,embedder-hub"))
        }
    })
}

/// Without the `media` feature, `image` (and therefore `image_meta`) isn't
/// compiled in at all, and neither is `clip_tags`. Returns a stub deriver
/// that fails LOUDLY — naming the features to enable — the first time it's
/// actually asked to derive something. Mirrors
/// `parsers::registry::MissingFeatureDeriver`'s pattern for pdf/xlsx/docx
/// in a non-`media` build.
///
/// Deliberately point-of-use rather than load-time, and yes that is
/// asymmetric with the `media`-but-not-`embedder-hub` `clip_tags` arm above
/// (which errors at load). The asymmetry is the one Cargo.toml's `media`
/// feature comment already specifies: `rung: clip_tags` without
/// `embedder-hub` is called out there as a config-load failure. Keeping the
/// non-`media` case at point-of-use preserves the existing behaviour that a
/// cell whose glob happens to match no derive-routed file still runs in a
/// slim build. Neither path can silently downgrade to a different rung,
/// which is the property that actually matters.
#[cfg(not(feature = "media"))]
pub fn load_deriver(cfg: &DeriverConfig) -> anyhow::Result<Rc<dyn CacheableDeriver>> {
    let (rung, features) = match cfg {
        DeriverConfig::ImageMeta(_) => ("image_meta", "media"),
        DeriverConfig::ClipTags(_) => ("clip_tags", "media,embedder-hub"),
    };
    Ok(Rc::new(MissingFeatureDeriver { rung, features }))
}

#[cfg(not(feature = "media"))]
struct MissingFeatureDeriver {
    rung: &'static str,
    features: &'static str,
}

#[cfg(not(feature = "media"))]
impl crate::sources::parsers::Deriver for MissingFeatureDeriver {
    fn derive(
        &self,
        _path: &std::path::Path,
    ) -> anyhow::Result<crate::sources::parsers::ParseResult> {
        Err(missing_feature_error(self.rung, self.features))
    }
}

#[cfg(not(feature = "media"))]
impl CacheableDeriver for MissingFeatureDeriver {
    fn cache_key_parts(&self) -> (String, String, String, serde_json::Value) {
        (
            self.rung.to_string(),
            "unavailable".to_string(),
            "0".to_string(),
            serde_json::json!({}),
        )
    }
}

#[allow(dead_code)]
fn missing_feature_error(rung: &str, features: &str) -> anyhow::Error {
    anyhow::anyhow!(
        "derive rung '{rung}' requires the `{features}` Cargo feature(s); \
         rebuild with --features {features}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Deriver trait objects aren't `Debug`, which rules out
    /// `Result::unwrap_err()` on `load_deriver`'s return type.
    #[allow(dead_code)]
    fn err_of<T>(r: anyhow::Result<T>) -> String {
        match r {
            Ok(_) => panic!("expected an error, got Ok"),
            Err(e) => format!("{e:#}"),
        }
    }

    #[cfg(not(feature = "media"))]
    #[test]
    fn load_deriver_without_media_feature_errors_naming_the_feature_on_use() {
        use crate::sources::parsers::Deriver as _;
        let cfg = DeriverConfig::ImageMeta(crate::config::ImageMetaDeriverConfig {
            path_segments: true,
            segment_depth: 3,
        });
        let deriver = load_deriver(&cfg).unwrap();
        let msg = err_of(deriver.derive(std::path::Path::new("whatever.png")));
        assert!(msg.contains("--features media"), "{msg}");
    }

    #[cfg(not(feature = "media"))]
    #[test]
    fn clip_tags_without_media_names_both_features_on_use() {
        use crate::sources::parsers::Deriver as _;
        let cfg = DeriverConfig::ClipTags(crate::config::ClipTagsDeriverConfig {
            vocabulary: vec!["red".to_string()],
            top_k: 3,
            min_score: 0.2,
            vision_model: "Qdrant/clip-ViT-B-32-vision".to_string(),
            text_model: "Qdrant/clip-ViT-B-32-text".to_string(),
        });
        let deriver = load_deriver(&cfg).unwrap();
        let msg = err_of(deriver.derive(std::path::Path::new("whatever.png")));
        assert!(msg.contains("clip_tags"), "{msg}");
        assert!(msg.contains("embedder-hub"), "{msg}");
    }

    #[cfg(all(feature = "media", not(feature = "embedder-hub")))]
    #[test]
    fn clip_tags_without_embedder_hub_errors_at_load_rather_than_downgrading() {
        let cfg = DeriverConfig::ClipTags(crate::config::ClipTagsDeriverConfig {
            vocabulary: vec!["red".to_string()],
            top_k: 3,
            min_score: 0.2,
            vision_model: "Qdrant/clip-ViT-B-32-vision".to_string(),
            text_model: "Qdrant/clip-ViT-B-32-text".to_string(),
        });
        let msg = err_of(load_deriver(&cfg));
        assert!(msg.contains("clip_tags"), "{msg}");
        assert!(msg.contains("embedder-hub"), "{msg}");
    }

    #[cfg(all(feature = "media", feature = "embedder-hub"))]
    #[test]
    fn clip_tags_config_errors_surface_from_load_deriver() {
        // An empty vocabulary must be rejected at load, not at first image.
        let cfg = DeriverConfig::ClipTags(crate::config::ClipTagsDeriverConfig {
            vocabulary: vec![],
            top_k: 3,
            min_score: 0.2,
            vision_model: "Qdrant/clip-ViT-B-32-vision".to_string(),
            text_model: "Qdrant/clip-ViT-B-32-text".to_string(),
        });
        let msg = err_of(load_deriver(&cfg));
        assert!(msg.contains("vocabulary"), "{msg}");
    }

    #[cfg(all(feature = "media", feature = "embedder-hub"))]
    #[test]
    fn clip_tags_model_override_is_rejected_at_load() {
        let cfg = DeriverConfig::ClipTags(crate::config::ClipTagsDeriverConfig {
            vocabulary: vec!["red".to_string()],
            top_k: 3,
            min_score: 0.2,
            vision_model: "Qdrant/clip-ViT-B-32-vision".to_string(),
            text_model: "some/other-model".to_string(),
        });
        let msg = err_of(load_deriver(&cfg));
        assert!(msg.contains("text_model"), "{msg}");
        assert!(msg.contains("some/other-model"), "{msg}");
    }

    #[cfg(feature = "media")]
    #[test]
    fn image_meta_loads_and_reports_its_cache_key_parts() {
        let cfg = DeriverConfig::ImageMeta(crate::config::ImageMetaDeriverConfig {
            path_segments: false,
            segment_depth: 2,
        });
        let d = load_deriver(&cfg).unwrap();
        let (rung, model, _v, params) = d.cache_key_parts();
        assert_eq!(rung, "image_meta");
        assert_eq!(model, "deterministic");
        assert_eq!(
            params,
            serde_json::json!({"path_segments": false, "segment_depth": 2})
        );
    }
}
