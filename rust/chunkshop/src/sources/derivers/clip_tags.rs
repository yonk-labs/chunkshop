//! Rung 1 for images: zero-shot tagging with CLIP, keyless and offline.
//! Mirrors `python/src/chunkshop/sources/derivers/clip_tags.py`. Embeds a
//! label vocabulary with the CLIP TEXT tower, tags each image by nearest
//! label via the CLIP VISION tower. Output is text, landing in the ordinary
//! text plane alongside email and PDF.
//!
//! Requires the `embedder-hub` Cargo feature (hf-hub auto-download of both
//! CLIP towers) in addition to `media`.
//!
//! # Known Python/Rust capability gap: `vision_model` / `text_model`
//!
//! Python does `TextEmbedding(self.text_model)` / `ImageEmbedding(self.vision_model)`
//! and so resolves ANY registry model id by name. fastembed-rs has no
//! equivalent "load an arbitrary stock model by string" API — stock models
//! are selected through the `EmbeddingModel` / `ImageEmbeddingModel` enums,
//! and only `ClipVitB32` is a CLIP pair. Verified against the vendored
//! crate source (`models/text_embedding.rs:385-392` maps
//! `EmbeddingModel::ClipVitB32` -> `Qdrant/clip-ViT-B-32-text`;
//! `models/image_embedding.rs:22-30` maps `ImageEmbeddingModel::ClipVitB32`
//! -> `Qdrant/clip-ViT-B-32-vision`).
//!
//! So a YAML override of either field is REJECTED at construction, loudly,
//! naming the field and the value. It is emphatically NOT silently
//! substituted with the stock pair: that would embed in the wrong vector
//! space, produce the wrong tags, AND stamp a provenance record claiming
//! the user's model id — a provenance record that lies is worse than no
//! derivation at all (NN-3). Same reject-loudly discipline this port applies
//! to every other Python capability Rust lacks.
//!
//! # Image preprocessing is chunkshop's, not `fastembed-rs`'s
//!
//! `score_image` below calls `clip_preprocess::open_for_clip` and then
//! `ImageEmbedding::embed_images`, rather than handing a path to
//! `ImageEmbedding::embed`. `fastembed-rs` 5.13.3 mis-implements CLIP's
//! `size.shortest_edge` semantics (`resize_exact` squashes the aspect ratio
//! instead of scaling the shortest edge and center-cropping), which moved the
//! vision-tower embedding by up to 26 degrees against Python on non-square
//! images. The fix and its measurements live in `clip_preprocess`'s module
//! doc. Do not "simplify" this back to `vision.embed(path)`.
//!
//! # Accepted value-level divergences from Python (do not "fix" these)
//!
//! - **Tie ordering.** Python uses `np.argsort(scores)[::-1][:top_k]`.
//!   numpy's default argsort is introsort (NOT stable), and the `[::-1]`
//!   reverse further permutes equal scores. Rust's `sort_by` below is
//!   stable, so exactly-tied cosine scores come out in a different order.
//!   Cosine ties across a real vocabulary are vanishingly rare, and the
//!   SC-12 contract-level parity test compares metadata KEYS and provenance
//!   FIELDS, not tag order.
//! - **Rounding.** Python's `round(score, 4)` is banker's rounding
//!   (half-to-even); `(x * 10_000.0).round() / 10_000.0` here is
//!   half-away-from-zero. They differ only on an exact `.00005` boundary.
//!
//! One thing that is NOT a divergence: Python selects `top_k` first and
//! then filters by `min_score`; the loop below does the same, but filtering
//! first would be equivalent anyway — the scores are sorted descending and
//! `min_score` is a threshold, so both orders yield "the up-to-`top_k`
//! highest-scoring labels that clear the threshold".

use std::path::Path;
use std::sync::Mutex;

use fastembed::{
    EmbeddingModel, ImageEmbedding, ImageEmbeddingModel, ImageInitOptions, TextEmbedding,
    TextInitOptions,
};
use serde_json::{json, Value};

use super::clip_preprocess::open_for_clip;
use super::image_meta::ImageMetaDeriver;
use super::provenance::stamp_provenance;
use crate::sources::parsers::{CacheableDeriver, Deriver, ParseResult};

pub const VISION_DEFAULT: &str = "Qdrant/clip-ViT-B-32-vision";
pub const TEXT_DEFAULT: &str = "Qdrant/clip-ViT-B-32-text";

/// The `fastembed` crate version that actually links into this binary, and
/// therefore the ONNX weights that actually produced a derivation. Part of
/// the cache key and of the provenance stamp, mirroring Python's
/// `importlib.metadata.version("fastembed")`.
///
/// Rust has no runtime equivalent for reading a dependency's resolved
/// version, so this is a hand-maintained const — which is exactly how it
/// goes stale and starts lying. `model_version_matches_cargo_lock` below
/// is the tripwire: it parses `rust/Cargo.lock` for the RESOLVED fastembed
/// version and fails if this const has drifted from it.
///
/// Deliberately checked against `Cargo.lock`, NOT `Cargo.toml`: the
/// manifest pins `fastembed = "5.13.1"`, a caret requirement that currently
/// RESOLVES to 5.13.3. A const validated against the manifest would record
/// "5.13.1" while 5.13.3's weights did the work — the precise class of
/// silent provenance lie this const exists to prevent. A fastembed upgrade
/// can change the ONNX weights behind a stable model id, so a stale version
/// here serves stale cached tags forever.
const FASTEMBED_VERSION: &str = "5.13.3";

/// Lazily-constructed CLIP towers plus the pre-embedded label matrix.
/// Mirrors Python's `_ensure_models`: nothing downloads at construction, so
/// a config-load or an empty-vocabulary rejection never touches the network
/// (NN-6), and the ~0.6GB of ONNX weights are loaded ONCE per deriver
/// instead of once per image.
struct Models {
    vision: ImageEmbedding,
    /// L2-normalised CLIP text-tower vector per vocabulary entry, in
    /// vocabulary order.
    label_vectors: Vec<Vec<f32>>,
}

pub struct ClipTagsDeriver {
    pub vocabulary: Vec<String>,
    pub top_k: usize,
    pub min_score: f32,
    pub vision_model: String,
    pub text_model: String,
    models: Mutex<Option<Models>>,
}

impl ClipTagsDeriver {
    pub fn new(
        vocabulary: Vec<String>,
        top_k: usize,
        min_score: f32,
        vision_model: String,
        text_model: String,
    ) -> anyhow::Result<Self> {
        if vocabulary.is_empty() {
            anyhow::bail!(
                "ClipTagsDeriver needs a non-empty vocabulary; a generic vocabulary \
                 produces generic tags, so choose it for your corpus."
            );
        }
        check_model_supported("vision_model", &vision_model, VISION_DEFAULT)?;
        check_model_supported("text_model", &text_model, TEXT_DEFAULT)?;
        Ok(Self {
            vocabulary,
            top_k,
            min_score,
            vision_model,
            text_model,
            models: Mutex::new(None),
        })
    }

    /// Build the CLIP towers and the label matrix on first use. Re-validates
    /// the model ids because the fields are `pub` — `new()`'s check would
    /// otherwise be bypassable by mutating a constructed deriver, which is
    /// the one way a wrong-vector-space derivation could still get stamped
    /// with a lying provenance record.
    fn ensure_models(&self, slot: &mut Option<Models>) -> anyhow::Result<()> {
        check_model_supported("vision_model", &self.vision_model, VISION_DEFAULT)?;
        check_model_supported("text_model", &self.text_model, TEXT_DEFAULT)?;
        if slot.is_some() {
            return Ok(());
        }

        let mut text = TextEmbedding::try_new(
            TextInitOptions::new(EmbeddingModel::ClipVitB32).with_show_download_progress(false),
        )?;
        let label_vectors: Vec<Vec<f32>> = text
            .embed(self.vocabulary.clone(), None)?
            .into_iter()
            .map(l2_normalize)
            .collect();

        let vision = ImageEmbedding::try_new(
            ImageInitOptions::new(ImageEmbeddingModel::ClipVitB32)
                .with_show_download_progress(false),
        )?;

        *slot = Some(Models {
            vision,
            label_vectors,
        });
        Ok(())
    }

    /// Score every vocabulary label against one image. Split out from
    /// `derive` so the selection/assembly half (`build_result`) is unit-
    /// testable without downloading 0.6GB of weights.
    fn score_image(&self, path: &Path) -> anyhow::Result<Vec<f32>> {
        let mut guard = self
            .models
            .lock()
            .map_err(|_| anyhow::anyhow!("ClipTagsDeriver model mutex was poisoned"))?;
        self.ensure_models(&mut guard)?;
        let models = guard.as_mut().expect("ensure_models populated the slot");

        // NOT `vision.embed(path)`. `fastembed-rs` 5.13.3's own path applies
        // `resize_exact(224, 224)`, which squashes a non-square image instead
        // of scaling its shortest edge to 224 and center-cropping — a
        // different field of view from Python, worth up to 26 degrees of
        // embedding angle. `open_for_clip` does CLIP's real geometry here and
        // hands over an already-224x224 image, on which fastembed's resize and
        // crop are both provable identities. See `clip_preprocess`'s module
        // doc for the measurements and for why the FILTER is not the problem.
        let img = open_for_clip(path)?;
        let vecs = models.vision.embed_images(vec![img])?;
        let img_vec = l2_normalize(
            vecs.into_iter()
                .next()
                .ok_or_else(|| anyhow::anyhow!("CLIP vision tower returned no vector for {}", path.display()))?,
        );

        Ok(models
            .label_vectors
            .iter()
            .map(|lv| dot(lv, &img_vec))
            .collect())
    }

    /// Turn per-label scores into tags, text, metadata, and provenance.
    ///
    /// If `base.text` is empty AND no label clears `min_score`, the result's
    /// text is empty — deliberately. `FilesSource::iter_documents` checks
    /// `result.text.trim().is_empty()` and routes it through `on_empty`
    /// (error / skip / index), so an empty derivation is a decision the user
    /// configured, never a silently-written empty document.
    fn build_result(&self, base: ParseResult, scores: &[f32]) -> ParseResult {
        let mut ranked: Vec<(usize, f32)> = scores.iter().copied().enumerate().collect();
        // Descending by score. `partial_cmp().unwrap()` would panic on a
        // NaN score; a NaN can only come out of a degenerate (all-zero)
        // embedding, so sort NaNs last rather than aborting the ingest —
        // they can never clear `min_score` anyway.
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

        let mut tags: Vec<String> = Vec::new();
        let mut tag_scores: Vec<f64> = Vec::new();
        for (i, score) in ranked.into_iter().take(self.top_k) {
            if score >= self.min_score {
                tags.push(self.vocabulary[i].clone());
                tag_scores.push(round4(score));
            }
        }

        let mut parts = vec![base.text.clone()];
        parts.extend(tags.iter().cloned());
        let text = parts.join(" ").trim().to_string();

        let mut metadata = base.metadata;
        metadata.insert("clip_tags".to_string(), json!(tags));
        metadata.insert("clip_scores".to_string(), json!(tag_scores));

        let result = ParseResult::with_metadata(text, metadata);
        let (rung, model_id, model_version, params) = self.cache_key_parts();
        stamp_provenance(result, &rung, &model_id, &model_version, "rust", &params)
    }
}

/// Reject — never silently substitute — a model id the Rust port cannot
/// honor. See this module's doc comment for why substitution is the worst
/// available option.
fn check_model_supported(field: &str, configured: &str, default: &str) -> anyhow::Result<()> {
    if configured != default {
        anyhow::bail!(
            "ClipTagsDeriver: {field} {configured:?} is not supported by the Rust port. Only the \
             default CLIP pair is wired to fastembed-rs's stock ClipVitB32 variants \
             (vision_model {VISION_DEFAULT:?}, text_model {TEXT_DEFAULT:?}); fastembed-rs cannot \
             resolve an arbitrary model id by name the way Python's fastembed can. Set {field} to \
             {default:?}, or run this cell with the Python implementation."
        );
    }
    Ok(())
}

/// Python's `round(score, 4)`. Widened to f64 before rounding so the JSON
/// number is `0.2345`, not f32's `0.23450000584125519` artifact.
fn round4(x: f32) -> f64 {
    ((x as f64) * 10_000.0).round() / 10_000.0
}

fn l2_normalize(v: Vec<f32>) -> Vec<f32> {
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm == 0.0 {
        return v;
    }
    v.into_iter().map(|x| x / norm).collect()
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

impl ClipTagsDeriver {
    /// The rung-0 deriver this rung builds on. Constructed in ONE place so
    /// `derive()` and `cache_key_extra()` can never be reading a different
    /// `ImageMetaDeriver` config from each other — the text this rung
    /// inherits and the key material it declares must come from the same
    /// instance. Mirrors Python's `self._meta`.
    fn base_deriver(&self) -> ImageMetaDeriver {
        ImageMetaDeriver::default()
    }
}

impl Deriver for ClipTagsDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let base = self.base_deriver().derive(path)?;
        let scores = self.score_image(path)?;
        Ok(self.build_result(base, &scores))
    }
}

impl CacheableDeriver for ClipTagsDeriver {
    fn cache_key_parts(&self) -> (String, String, String, Value) {
        (
            "clip_tags".to_string(),
            format!("{}+{}", self.vision_model, self.text_model),
            FASTEMBED_VERSION.to_string(),
            json!({
                "vocabulary": self.vocabulary,
                "top_k": self.top_k,
                "min_score": self.min_score,
            }),
        )
    }

    /// Delegated to the wrapped `ImageMetaDeriver`.
    ///
    /// `derive()` builds its text as `base.text + tags`, so this rung
    /// inherits rung 0's path dependence wholesale — and with it rung 0's
    /// collision. Without delegating, two byte-identical images with
    /// different names would share one clip_tags entry and the second
    /// would be served the first's filename text AND the first image's
    /// CLIP tags. This stays delegation rather than a copy of rung 0's
    /// path logic so the contribution tracks whatever config
    /// `base_deriver()` returns, now or later.
    fn cache_key_extra(&self, path: &Path) -> Option<String> {
        self.base_deriver().cache_key_extra(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `ClipTagsDeriver` holds ONNX sessions and so isn't `Debug`, which
    /// rules out `Result::unwrap_err()`. This is the equivalent.
    fn err_of<T>(r: anyhow::Result<T>) -> String {
        match r {
            Ok(_) => panic!("expected an error, got Ok"),
            Err(e) => format!("{e:#}"),
        }
    }

    fn deriver(top_k: usize, min_score: f32) -> ClipTagsDeriver {
        ClipTagsDeriver::new(
            vec!["red".to_string(), "blue".to_string(), "cat".to_string()],
            top_k,
            min_score,
            VISION_DEFAULT.to_string(),
            TEXT_DEFAULT.to_string(),
        )
        .unwrap()
    }

    #[test]
    fn requires_a_non_empty_vocabulary() {
        let msg = err_of(ClipTagsDeriver::new(
            vec![],
            3,
            0.2,
            VISION_DEFAULT.to_string(),
            TEXT_DEFAULT.to_string(),
        ));
        assert!(msg.contains("vocabulary"), "{msg}");
    }

    #[test]
    fn rejects_a_non_default_text_model_instead_of_substituting() {
        // Correction 1. Python honours any model id; Rust cannot. Silently
        // falling back to the stock pair would embed in the wrong vector
        // space and then STAMP PROVENANCE claiming the user's model id.
        let msg = err_of(ClipTagsDeriver::new(
            vec!["red".to_string()],
            3,
            0.2,
            VISION_DEFAULT.to_string(),
            "some/other-model".to_string(),
        ));
        assert!(msg.contains("text_model"), "{msg}");
        assert!(msg.contains("some/other-model"), "{msg}");
        assert!(msg.contains(TEXT_DEFAULT), "{msg}");
        assert!(msg.contains("not supported by the Rust port"), "{msg}");
    }

    #[test]
    fn rejects_a_non_default_vision_model_instead_of_substituting() {
        let msg = err_of(ClipTagsDeriver::new(
            vec!["red".to_string()],
            3,
            0.2,
            "some/other-vision".to_string(),
            TEXT_DEFAULT.to_string(),
        ));
        assert!(msg.contains("vision_model"), "{msg}");
        assert!(msg.contains("some/other-vision"), "{msg}");
        assert!(msg.contains(VISION_DEFAULT), "{msg}");
    }

    #[test]
    fn a_mutated_model_field_is_still_caught_before_any_derivation() {
        // The fields are `pub`, so `new()`'s gate alone is bypassable.
        // `ensure_models` re-checks; `score_image` is the only path to it.
        let mut d = deriver(3, 0.2);
        d.text_model = "sneaky/override".to_string();
        let err = d
            .score_image(std::path::Path::new("nonexistent.png"))
            .unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("text_model"), "{msg}");
        assert!(msg.contains("sneaky/override"), "{msg}");
    }

    #[test]
    fn model_version_matches_cargo_lock() {
        // Correction 2's tripwire. `FASTEMBED_VERSION` is hand-maintained
        // (Rust has no runtime dependency-version introspection), so this
        // parses the RESOLVED version out of the workspace lock file and
        // fails the moment the two drift. Bumping fastembed without bumping
        // the const would otherwise write provenance records naming a
        // version that never ran — invisible, and an NN-3 violation.
        //
        // Cargo.lock is at the workspace root, one level above this crate.
        let lock_path = concat!(env!("CARGO_MANIFEST_DIR"), "/../Cargo.lock");
        let lock = std::fs::read_to_string(lock_path)
            .unwrap_or_else(|e| panic!("cannot read {lock_path}: {e}"));

        // Hand-rolled scan (no toml dependency): find the `[[package]]`
        // block whose `name = "fastembed"`, then the `version` line inside
        // that same block.
        let mut in_fastembed = false;
        let mut resolved: Option<String> = None;
        for line in lock.lines() {
            let t = line.trim();
            if t == "[[package]]" {
                in_fastembed = false;
            } else if t == r#"name = "fastembed""# {
                in_fastembed = true;
            } else if in_fastembed && t.starts_with("version = ") {
                resolved = Some(t.trim_start_matches("version = ").trim_matches('"').to_string());
                break;
            }
        }
        let resolved = resolved.expect("Cargo.lock must contain a fastembed package entry");
        assert_eq!(
            FASTEMBED_VERSION, resolved,
            "FASTEMBED_VERSION is stale: Cargo.lock resolves fastembed to {resolved}. Bump the \
             const in the SAME commit that bumps the dependency, or every derivation from here \
             on records a version that did not produce it."
        );
    }

    #[test]
    fn cache_key_parts_match_the_stamped_provenance_tuple() {
        let d = deriver(3, 0.2);
        let (rung, model, version, params) = d.cache_key_parts();
        assert_eq!(rung, "clip_tags");
        assert_eq!(model, format!("{VISION_DEFAULT}+{TEXT_DEFAULT}"));
        assert_eq!(version, FASTEMBED_VERSION);

        let md = d
            .build_result(ParseResult::new("base"), &[0.9, 0.1, 0.05])
            .metadata;
        assert_eq!(md["derived"]["rung"], json!(rung));
        assert_eq!(md["derived"]["model"], json!(model));
        assert_eq!(md["derived"]["model_version"], json!(version));
        // Correction 6: engine is "rust", so the derivation_hash for an
        // otherwise-identical config DIFFERS from Python's by design — that
        // is what lets both languages share one cache directory without
        // either masquerading as the other. Nothing here asserts hash
        // equality with Python.
        assert_eq!(md["derived"]["engine"], json!("rust"));

        let recomputed = super::super::provenance::derivation_hash(
            md["derived"]["rung"].as_str().unwrap(),
            md["derived"]["model"].as_str().unwrap(),
            md["derived"]["model_version"].as_str().unwrap(),
            md["derived"]["engine"].as_str().unwrap(),
            &params,
        );
        let expected =
            super::super::provenance::derivation_hash(&rung, &model, &version, "rust", &params);
        assert_eq!(recomputed, expected);
    }

    #[test]
    fn tags_are_top_k_descending_and_appended_to_the_base_text() {
        let d = deriver(2, 0.2);
        let r = d.build_result(ParseResult::new("goblin warrior"), &[0.31, 0.85, 0.22]);
        assert_eq!(r.metadata["clip_tags"], json!(["blue", "red"]));
        assert_eq!(r.metadata["clip_scores"], json!([0.85, 0.31]));
        assert_eq!(r.text, "goblin warrior blue red");
    }

    #[test]
    fn min_score_filters_labels_out_of_the_selected_top_k() {
        let d = deriver(3, 0.30);
        let r = d.build_result(ParseResult::new("sprite"), &[0.31, 0.85, 0.22]);
        assert_eq!(r.metadata["clip_tags"], json!(["blue", "red"]));
        assert_eq!(r.text, "sprite blue red");
    }

    #[test]
    fn scores_are_rounded_to_four_places_like_python() {
        let d = deriver(1, 0.0);
        let r = d.build_result(ParseResult::new("x"), &[0.234567_f32, 0.0, 0.0]);
        assert_eq!(r.metadata["clip_scores"], json!([0.2346]));
    }

    #[test]
    fn no_tag_clearing_min_score_leaves_the_base_text_untouched() {
        let d = deriver(3, 0.99);
        let r = d.build_result(ParseResult::new("goblin warrior"), &[0.31, 0.85, 0.22]);
        assert_eq!(r.metadata["clip_tags"], json!([]));
        assert_eq!(r.metadata["clip_scores"], json!([]));
        assert_eq!(r.text, "goblin warrior");
    }

    #[test]
    fn an_empty_derivation_produces_empty_text_for_on_empty_to_route() {
        // Correction 4. Empty rung-0 base text AND no tag clearing
        // `min_score` is a genuinely empty derivation. It must surface as
        // empty text so `FilesSource::iter_documents`'s existing
        // `on_empty` branch (error / skip / index) decides what happens —
        // never a silently-written empty document.
        //
        // `ImageMetaDeriver` currently guarantees non-empty base text (it
        // falls back to "<format> image"), so this is a defence-in-depth
        // path, not a reachable-today one. Asserting it here keeps the
        // contract if that fallback is ever relaxed.
        let d = deriver(3, 0.99);
        let r = d.build_result(ParseResult::new(""), &[0.31, 0.85, 0.22]);
        assert!(r.text.trim().is_empty(), "expected empty text, got {:?}", r.text);
        assert_eq!(r.metadata["clip_tags"], json!([]));
    }

    #[test]
    fn nan_scores_never_become_tags_and_never_panic() {
        // A degenerate all-zero embedding yields NaN cosine scores.
        // `partial_cmp` returns None for NaN — `.unwrap()` there would
        // abort the whole ingest. Sorted last, and NaN >= min_score is
        // false, so they simply never get selected.
        let d = deriver(3, 0.2);
        let r = d.build_result(ParseResult::new("x"), &[f32::NAN, 0.85, f32::NAN]);
        assert_eq!(r.metadata["clip_tags"], json!(["blue"]));
    }

    #[test]
    fn provenance_names_the_configured_model_pair_not_a_substitute() {
        let d = deriver(3, 0.2);
        let md = d.build_result(ParseResult::new("x"), &[0.9, 0.1, 0.0]).metadata;
        assert_eq!(md["content_origin"], json!("derived"));
        assert_eq!(md["derived_rung"], json!("clip_tags"));
        assert_eq!(
            md["derived"]["model"],
            json!(format!("{VISION_DEFAULT}+{TEXT_DEFAULT}"))
        );
        assert_eq!(md["derived"]["model_version"], json!(FASTEMBED_VERSION));
    }

    #[test]
    #[ignore = "downloads ~0.6GB of ONNX weights on first run; run manually with --ignored, not in CI by default"]
    fn tags_are_text_and_correct() {
        let dir = tempfile::tempdir().unwrap();
        let red = dir.path().join("red.png");
        let blue = dir.path().join("blue.png");
        let red_img: image::RgbImage =
            image::ImageBuffer::from_pixel(224, 224, image::Rgb([220, 30, 30]));
        red_img.save(&red).unwrap();
        let blue_img: image::RgbImage =
            image::ImageBuffer::from_pixel(224, 224, image::Rgb([30, 30, 220]));
        blue_img.save(&blue).unwrap();

        let vocab = vec![
            "a solid red square".to_string(),
            "a solid blue square".to_string(),
            "a photograph of a cat".to_string(),
        ];
        let d = ClipTagsDeriver::new(
            vocab,
            1,
            0.20,
            VISION_DEFAULT.to_string(),
            TEXT_DEFAULT.to_string(),
        )
        .unwrap();

        let red_result = d.derive(&red).unwrap();
        let red_tags = red_result.metadata["clip_tags"].as_array().unwrap();
        assert_eq!(
            red_tags[0],
            json!("a solid red square"),
            "if this fails, STOP and investigate — do not weaken the assertion"
        );

        let blue_result = d.derive(&blue).unwrap();
        let blue_tags = blue_result.metadata["clip_tags"].as_array().unwrap();
        assert_eq!(blue_tags[0], json!("a solid blue square"));
    }
}
