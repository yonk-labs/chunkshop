//! Media derivers: turn non-text bytes into text plus metadata. Mirrors
//! `python/src/chunkshop/sources/derivers/`. Every deriver's output is
//! stamped with provenance and memoised on (content_hash, derivation_hash)
//! so nothing is derived twice and nothing is written without attribution.
//!
//! Not `media`-gated itself — `ParseResult`/`Deriver`/`CacheableDeriver` and
//! provenance are always available (see `sources::mod`'s doc comment).
//! `image_meta` (Task 9) IS `media`-gated (it needs the `image`/`png`/`gif`
//! crates); `clip_tags` (a later task) will be too. Following
//! `parsers/mod.rs`'s established convention, a submodule here is declared
//! only by the task that creates its file.

pub mod cache;
pub mod provenance;

#[cfg(feature = "media")]
pub mod image_meta;

use std::rc::Rc;

use crate::config::DeriverConfig;
use crate::sources::parsers::CacheableDeriver;
#[cfg(not(feature = "media"))]
use crate::sources::parsers::Deriver;

/// Materialize a `DeriverConfig` into a `CacheableDeriver` trait object.
///
/// `Rc`, not `Box`: a single `derive:` pattern key like `"png|jpg"` fans out
/// to multiple per-extension entries in `FilesSource`'s deriver table
/// (Task 9 Step 3b), all of which must share ONE constructed deriver
/// instance rather than re-parsing config per extension or requiring
/// `dyn Deriver: Clone` (which trait objects can't derive generically).
/// `Rc` clones cheaply for this fan-out. `FilesSource` never crosses a
/// `tokio::spawn` boundary — `run_cell` is `.await`ed directly in
/// `main.rs` and nothing in this codebase calls `tokio::spawn` today — so
/// `Rc`'s `!Send`/`!Sync` is safe here; there is no async trait bound on
/// `AnySource`/`Deriver` that requires `Send`.
#[cfg(feature = "media")]
pub fn load_deriver(cfg: &DeriverConfig) -> Rc<dyn CacheableDeriver> {
    match cfg {
        DeriverConfig::ImageMeta(c) => Rc::new(image_meta::ImageMetaDeriver {
            path_segments: c.path_segments,
            segment_depth: c.segment_depth as usize,
        }),
        DeriverConfig::ClipTags(_) => {
            // Task 11 replaces this arm. Left as an explicit panic (not a
            // silent no-op) so a `derive: {rung: clip_tags}` YAML fails
            // loudly if this task lands ahead of Task 11 in a partial
            // checkout, rather than mysteriously returning wrong results.
            unimplemented!("ClipTagsDeriver lands in Task 11")
        }
    }
}

/// Without the `media` feature, `image` (and therefore `image_meta`) isn't
/// compiled in at all. Rather than making `FilesSource::new` fallible (a
/// wider API change touching every caller) or panicking at construction
/// time, this returns a stub deriver that fails LOUDLY — naming the
/// feature to enable — the first time it's actually asked to derive
/// something. Mirrors `parsers::registry::MissingFeatureDeriver`'s pattern
/// for pdf/xlsx/docx in a non-`media` build.
#[cfg(not(feature = "media"))]
pub fn load_deriver(cfg: &DeriverConfig) -> Rc<dyn CacheableDeriver> {
    let rung = match cfg {
        DeriverConfig::ImageMeta(_) => "image_meta",
        DeriverConfig::ClipTags(_) => "clip_tags",
    };
    Rc::new(MediaFeatureRequiredDeriver { rung })
}

#[cfg(not(feature = "media"))]
struct MediaFeatureRequiredDeriver {
    rung: &'static str,
}

#[cfg(not(feature = "media"))]
impl Deriver for MediaFeatureRequiredDeriver {
    fn derive(&self, _path: &std::path::Path) -> anyhow::Result<crate::sources::parsers::ParseResult> {
        Err(anyhow::anyhow!(
            "derive rung '{}' requires the `media` Cargo feature; rebuild with --features media",
            self.rung
        ))
    }
}

#[cfg(not(feature = "media"))]
impl CacheableDeriver for MediaFeatureRequiredDeriver {
    fn cache_key_parts(&self) -> (String, String, String, serde_json::Value) {
        (
            self.rung.to_string(),
            "unavailable".to_string(),
            "0".to_string(),
            serde_json::json!({}),
        )
    }
}

#[cfg(all(test, not(feature = "media")))]
mod tests_without_media {
    use super::*;

    #[test]
    fn load_deriver_without_media_feature_errors_naming_the_feature_on_use() {
        let cfg = DeriverConfig::ImageMeta(crate::config::ImageMetaDeriverConfig {
            path_segments: true,
            segment_depth: 3,
        });
        let deriver = load_deriver(&cfg);
        let err = deriver.derive(std::path::Path::new("whatever.png")).unwrap_err();
        assert!(format!("{err:#}").contains("--features media"));
    }
}
