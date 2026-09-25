//! Regression tests for the derivation cache's silent path-collision bug.
//!
//! The cache key was `H(content_hash, derivation_hash)` with the file path
//! in NEITHER half, while `ImageMetaDeriver` derives its output TEXT from
//! the filename and path segments. Two byte-identical images with different
//! names therefore collided and the second silently received the FIRST's
//! derived text — embedded, indexed and searchable under the wrong
//! document, with no warning and no error. `ClipTagsDeriver` inherited it
//! (its text is `base.text + tags`), so the expensive rung users will
//! certainly cache was affected too, and its tags came from the wrong image.
//!
//! Realistic triggers: duplicate sprites/icons/placeholders, the same
//! screenshot saved twice, blank swatches — and simply RENAMING a file and
//! re-running the cell, which served the old name's text forever.
//!
//! Fixed by giving each deriver a per-document cache-key contribution
//! (`CacheableDeriver::cache_key_extra`), so a deriver declares what
//! non-content material it consumes instead of the cache assuming none.
//! Mirrors `python/tests/chunkshop/test_files_derive_cache.py`'s
//! path-collision block.
//!
//! `media`-gated: `ImageMetaDeriver` needs the `image`/`png`/`gif` crates.
#![cfg(feature = "media")]
#![cfg(feature = "source")]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use chunkshop::config::{
    DerivationCacheSettings, DeriverConfig, FilesSourceConfig, ImageMetaDeriverConfig, OnEmpty,
};
use chunkshop::sources::files::FilesSource;

/// A real PNG already in the repo, copied rather than re-encoded so the two
/// fixtures are genuinely byte-identical and the collision is a true
/// content-hash match, not an artefact of two separate encodes.
const SOURCE_PNG: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../docs/samples/images/sprite_knight.png"
);

fn copy_as(dir: &Path, name: &str) -> PathBuf {
    let p = dir.join(name);
    std::fs::copy(SOURCE_PNG, &p).unwrap();
    p
}

fn cfg(glob_dir: &Path, cache_root: &Path, path_segments: bool) -> FilesSourceConfig {
    let mut derive = BTreeMap::new();
    derive.insert(
        "png".to_string(),
        DeriverConfig::ImageMeta(ImageMetaDeriverConfig {
            path_segments,
            segment_depth: 3,
        }),
    );
    FilesSourceConfig {
        glob: format!("{}/*.png", glob_dir.display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: Some(derive),
        cache: DerivationCacheSettings {
            enabled: true,
            root: Some(cache_root.display().to_string()),
        },
    }
}

fn count_entries(root: &Path) -> usize {
    let mut n = 0;
    for shard in std::fs::read_dir(root).unwrap() {
        let shard = shard.unwrap().path();
        if shard.is_dir() {
            n += std::fs::read_dir(&shard).unwrap().count();
        }
    }
    n
}

fn assert_no_collision(path_segments: bool) {
    let dir = tempfile::tempdir().unwrap();
    let corpus = dir.path().join("corpus");
    std::fs::create_dir(&corpus).unwrap();
    let a = copy_as(&corpus, "a_chart_revenue.png");
    let b = copy_as(&corpus, "b_goblin_warrior.png");
    assert_eq!(
        std::fs::read(&a).unwrap(),
        std::fs::read(&b).unwrap(),
        "fixtures must be byte-identical for this to be the real bug"
    );
    let cache_root = dir.path().join("cache");

    let docs = FilesSource::new(cfg(&corpus, &cache_root, path_segments))
        .unwrap()
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 2);

    let chart = docs
        .iter()
        .find(|d| d.title.as_deref() == Some("a_chart_revenue.png"))
        .unwrap();
    let goblin = docs
        .iter()
        .find(|d| d.title.as_deref() == Some("b_goblin_warrior.png"))
        .unwrap();

    assert!(chart.content.contains("chart revenue"), "{}", chart.content);
    assert!(
        goblin.content.contains("goblin warrior"),
        "{}",
        goblin.content
    );
    assert!(
        !goblin.content.contains("chart revenue"),
        "the second byte-identical image was served the FIRST's derived \
         text ({:?}) — this is the silent data-corruption bug",
        goblin.content
    );
    assert_eq!(
        count_entries(&cache_root),
        2,
        "byte-identical images with different names must not share one entry"
    );
}

#[test]
fn identical_images_with_different_names_get_their_own_text_without_segments() {
    assert_no_collision(false);
}

#[test]
fn identical_images_with_different_names_get_their_own_text_with_segments() {
    // `path_segments: true` adds parent directories to the derived text but
    // BOTH files share those parents, so the filename is still the only
    // thing separating them — the collision is identical in both configs.
    assert_no_collision(true);
}

#[test]
fn renaming_a_file_re_derives_under_the_new_name() {
    // The most ordinary trigger: rename a file and re-run the cell. Content
    // unchanged, deriver config unchanged — so before the fix both halves of
    // the key were identical and the old name's text was served forever.
    let dir = tempfile::tempdir().unwrap();
    let corpus = dir.path().join("corpus");
    std::fs::create_dir(&corpus).unwrap();
    copy_as(&corpus, "a.png");
    let cache_root = dir.path().join("cache");

    let run = || -> String {
        let docs = FilesSource::new(cfg(&corpus, &cache_root, false))
            .unwrap()
            .iter_documents()
            .unwrap();
        assert_eq!(docs.len(), 1);
        docs[0].content.clone()
    };

    assert_eq!(run(), "a");
    std::fs::rename(corpus.join("a.png"), corpus.join("b.png")).unwrap();
    assert_eq!(
        run(),
        "b",
        "derived text must follow the new name, not the cache"
    );
}

/// The property the fix PRESERVES — proof it is not too broad.
///
/// A deriver that is genuinely a pure function of (bytes, params) declares
/// no `cache_key_extra`, so two byte-identical files with different names
/// still derive ONCE and hold ONE entry (two copies of the same PDF should
/// not pay twice). Driven at the cache layer because `FilesSource`'s
/// `derive:` config can only route the two image rungs, both of which are
/// deliberately path-dependent — a pure deriver cannot be reached through
/// YAML, so it is exercised through the same `cached_derive` the pipeline
/// calls.
#[test]
fn pure_derivers_still_share_one_cache_entry() {
    use chunkshop::sources::derivers::cache::{cached_derive, DerivationCache};
    use chunkshop::sources::parsers::ParseResult;

    let dir = tempfile::tempdir().unwrap();
    let cache = DerivationCache::new(Some(dir.path().to_path_buf()));
    let mut calls = 0usize;

    // Same bytes, same deriver identity, DIFFERENT file names — a pure
    // deriver contributes None for both, so the second must hit.
    for _name in ["one.txt", "two.txt"] {
        let (r, _hit) = cached_derive(Some(&cache), "same-content", "pure-d1", None, || {
            calls += 1;
            Ok(ParseResult::new("content only"))
        })
        .unwrap();
        assert_eq!(r.text, "content only");
    }

    assert_eq!(calls, 1, "the second file must be a cache hit");
    assert_eq!(
        count_entries(dir.path()),
        1,
        "a pure deriver must keep exactly one shared, content-addressed entry"
    );
}

/// The trait default is what makes every existing content-only deriver pure
/// without touching it. If a future edit makes the default path-dependent,
/// every deriver silently loses content-addressed sharing.
#[test]
fn cache_key_extra_defaults_to_none() {
    use chunkshop::sources::parsers::{CacheableDeriver, Deriver, ParseResult};
    use serde_json::{json, Value};

    struct Pure;
    impl Deriver for Pure {
        fn parser_name(&self) -> &'static str {
            "PureTestDeriver"
        }

        fn derive(&self, _path: &Path) -> anyhow::Result<ParseResult> {
            Ok(ParseResult::new("body"))
        }
    }
    impl CacheableDeriver for Pure {
        fn cache_key_parts(&self) -> (String, String, String, Value) {
            ("pure".into(), "none".into(), "1".into(), json!({}))
        }
    }

    assert!(Pure.cache_key_extra(Path::new("/a/b/anything.txt")).is_none());
}
