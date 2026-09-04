//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use anyhow::{anyhow, Context, Result};
use sha1::{Digest as _, Sha1};
use sha2::Sha256;

use crate::config::{FilesSourceConfig, OnEmpty};
use crate::sources::base::Document;
use crate::sources::derivers::cache::{cached_derive, DerivationCache};
use crate::sources::derivers::provenance::derivation_hash;
use crate::sources::parsers::{AmbiguousDeriverError, CacheableDeriver, EmptyDerivationError};

pub struct FilesSource {
    cfg: FilesSourceConfig,
    /// Per-extension deriver override table, built from `cfg.derive`.
    /// ONLY extensions listed here are cache-eligible — see `_derived_exts`
    /// in `files.py`'s docstring for why an extension outside this table
    /// (the default/registry parser path) must never be routed through the
    /// cache.
    derived: HashMap<String, Rc<dyn CacheableDeriver>>,
    cache: Option<DerivationCache>,
    /// Set at construction if `cfg.derive` routes the same extension from
    /// two different pattern keys (Task 9 Correction 2). Checked by
    /// `iter_documents` before any file is touched, and returned as an
    /// error rather than silently picking a winner. Deliberately still
    /// deferred (rather than folded into `new()`'s `Result`, which Task 12
    /// added for deriver-construction failures): it is a typed, `Clone`
    /// error whose existing call sites assert on it at iteration time, and
    /// the two failure classes are worth keeping distinguishable.
    derive_error: Option<AmbiguousDeriverError>,
}

impl FilesSource {
    /// Fallible since Task 12: a `derive:` block can now name a deriver
    /// that cannot be constructed — an empty CLIP vocabulary, an
    /// unsupported `vision_model`/`text_model` override, or a rung whose
    /// Cargo feature wasn't built in. Every one of those must surface here,
    /// at config-load, rather than degrade to a different rung at runtime.
    pub fn new(cfg: FilesSourceConfig) -> Result<Self> {
        let mut derived: HashMap<String, Rc<dyn CacheableDeriver>> = HashMap::new();
        let mut ext_owner: HashMap<String, String> = HashMap::new();
        let mut derive_error = None;

        if let Some(derive_cfg) = &cfg.derive {
            for (pattern, dcfg) in derive_cfg {
                let deriver: Rc<dyn CacheableDeriver> =
                    crate::sources::derivers::load_deriver(dcfg)?;
                for raw_ext in pattern.split('|') {
                    let ext = raw_ext.trim().to_lowercase();
                    let ext = ext.trim_start_matches('.').to_string();
                    if let Some(existing_key) = ext_owner.get(&ext) {
                        if existing_key != pattern && derive_error.is_none() {
                            derive_error = Some(AmbiguousDeriverError {
                                ext: ext.clone(),
                                first_key: existing_key.clone(),
                                second_key: pattern.clone(),
                            });
                        }
                        continue;
                    }
                    ext_owner.insert(ext.clone(), pattern.clone());
                    derived.insert(ext, deriver.clone());
                }
            }
        }

        // The cache exists to memoise EXPENSIVE derivation. Without a
        // derive block every parser is cheap local text/binary extraction
        // via the registry, so a text-only (or plain media-registry) cell
        // builds no cache and touches no disk. Even with a derive block,
        // only derive-configured extensions are ever routed through it —
        // see `iter_documents` below.
        let cache = if cfg.derive.is_some() && cfg.cache.enabled {
            Some(DerivationCache::new(
                cfg.cache.root.clone().map(PathBuf::from),
            ))
        } else {
            None
        };

        Ok(Self {
            cfg,
            derived,
            cache,
            derive_error,
        })
    }

    /// Enumerate files matching the glob, in sorted order, deriving each
    /// through the per-extension `derive:` override table first (cache-
    /// eligible), falling back to the extension-dispatched parser registry
    /// (plain text passes through unchanged; recognized binary formats get
    /// real extraction or a named refusal — never a silent UTF-8
    /// mis-decode).
    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        if let Some(err) = &self.derive_error {
            return Err(anyhow::Error::new(err.clone()));
        }

        let mut paths: Vec<PathBuf> = glob::glob(&self.cfg.glob)
            .with_context(|| format!("invalid glob {:?}", self.cfg.glob))?
            .filter_map(std::result::Result::ok)
            .collect();
        if paths.is_empty() {
            return Err(anyhow!("no files matched glob: {}", self.cfg.glob));
        }
        paths.sort();

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let ext = p
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_lowercase();

            // `derived` is THE routing signal, set on the branch actually
            // taken and reused below for `on_empty`. "this extension is in
            // the `derive:` block" and "this document went through a
            // Deriver" are the same fact here, not two: `self.derived`
            // short-circuits the registry entirely, and a deriver that
            // cannot be built fails in `load_deriver` (and again in
            // `CellConfig::preflight_derive`, before the sink's DDL) rather
            // than degrading to a text parser. Deriving it a second way —
            // from the parser object, say — is the duplicated-routing
            // defect class that produced the bug this gate fixes.
            let (result, content_hash, parser_name, derived) = if let Some(deriver) =
                self.derived.get(&ext)
            {
                let bytes = std::fs::read(&p).with_context(|| format!("reading {}", p.display()))?;
                let content_hash = hash_bytes(&bytes);
                let (rung, model_id, model_version, params) = deriver.cache_key_parts();
                // `deriver.engine()`, not a hardcoded "rust": rung 5
                // (`RossDeriver`) shells out to the same binary from both
                // ports and stamps `engine: "ross"`, so a hardcoded engine
                // here would compute a cache key that disagrees with the
                // provenance the deriver actually stamps.
                let d_hash =
                    derivation_hash(&rung, &model_id, &model_version, deriver.engine(), &params);
                // Third component of the key: whatever per-document
                // material this deriver consumes beyond its bytes (`None`
                // for a pure deriver — see `CacheableDeriver`'s
                // cache-soundness invariant). Without it,
                // `ImageMetaDeriver` — whose text comes from the FILENAME —
                // served byte-identical images each other's derived text.
                let extra = deriver.cache_key_extra(&p);
                let (r, _hit) = cached_derive(self.cache.as_ref(), &content_hash, &d_hash, extra.as_deref(), || {
                    deriver.derive(&p)
                })?;
                (r, Some(content_hash), deriver.parser_name(), true)
            } else {
                let deriver = crate::sources::parsers::registry::get_deriver(&ext);
                let name = deriver.parser_name();
                (
                    deriver
                        .derive(&p)
                        .with_context(|| format!("deriving {}", p.display()))?,
                    None,
                    name,
                    false,
                )
            };

            if result.text.trim().is_empty() {
                // `on_empty` is a DERIVATION policy, scoped to derive-routed
                // documents — see `FilesSourceConfig::on_empty`. Applying it
                // to the plain parser path was a data-loss regression for
                // text-only corpora: the default `Error` aborted the whole
                // run on a zero-byte or whitespace-only `.md`, and
                // `iter_documents` builds the entire `Vec<Document>` eagerly,
                // so NOTHING was written — after `mode: overwrite`'s
                // DROP+CREATE had already emptied the target table. A
                // non-derived document keeps the pre-`on_empty` behaviour
                // unconditionally: it is indexed as an empty document.
                let policy = if derived {
                    self.cfg.on_empty
                } else {
                    OnEmpty::Index
                };
                match policy {
                    OnEmpty::Error => {
                        return Err(EmptyDerivationError {
                            path: p.clone(),
                            // The deriver's Python class name, matching
                            // Python's `f"{path} produced no text via
                            // {parser.__class__.__name__}"`. Was the bare
                            // extension until the `parser_name` contract
                            // existed to supply the real thing.
                            parser: parser_name.to_string(),
                        }
                        .into());
                    }
                    OnEmpty::Skip => continue,
                    OnEmpty::Index => {} // fall through, index the empty document
                }
            }

            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            let mut metadata = result.metadata;
            metadata.insert(
                "source_path".to_string(),
                serde_json::Value::String(p.display().to_string()),
            );
            // Stamped HERE, not inside the deriver, for two reasons.
            // (1) `ClipTagsDeriver` delegates to `ImageMetaDeriver` for its
            // rung-0 base, so a deriver-side stamp would label every rung-1
            // document `"ImageMetaDeriver"` — a wrong value in place of a
            // missing key, which is strictly worse. (2) It sits outside the
            // derivation cache, so a cache HIT still gets the parser that
            // this cell is configured with rather than whichever one first
            // populated the entry. Mirrors Python's own placement in
            // `files.py`, where `parser` is merged in after `cached_derive`
            // returns and takes precedence over deriver-emitted keys.
            metadata.insert(
                "parser".to_string(),
                serde_json::Value::String(parser_name.to_string()),
            );
            out.push(Document {
                id: doc_id,
                content: result.text,
                title,
                metadata: serde_json::Value::Object(metadata),
                fingerprint: content_hash,
            });
        }
        Ok(out)
    }

    fn id_for(&self, path: &Path) -> Result<String> {
        match self.cfg.id_from.as_str() {
            "path" => Ok(path.display().to_string()),
            "stem" => path
                .file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
                .ok_or_else(|| anyhow!("file has no stem: {}", path.display())),
            "sha1" => {
                let mut hasher = Sha1::new();
                hasher.update(path.display().to_string().as_bytes());
                Ok(format!("{:x}", hasher.finalize()))
            }
            other => Err(anyhow!("unknown id_from: {other}")),
        }
    }
}

/// Mirrors Python's `_hash_bytes` (`files.py:21-22`).
fn hash_bytes(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;
    use crate::config::{DeriverConfig, ImageMetaDeriverConfig};

    fn base_cfg(
        glob: String,
        derive: Option<BTreeMap<String, DeriverConfig>>,
    ) -> FilesSourceConfig {
        FilesSourceConfig {
            glob,
            id_from: "path".to_string(),
            encoding: "utf-8".to_string(),
            on_empty: OnEmpty::Error,
            derive,
            cache: Default::default(),
        }
    }

    fn image_meta_cfg() -> DeriverConfig {
        DeriverConfig::ImageMeta(ImageMetaDeriverConfig {
            path_segments: true,
            segment_depth: 3,
        })
    }

    // --- `on_empty` scope: derivation policy, not a text policy ---------

    /// Stands in for a real rung that derives nothing (a blank scan).
    ///
    /// Reached by hand-building `FilesSource`'s `derived` table below
    /// rather than through `load_deriver`, because every real rung this
    /// build can construct deliberately yields non-empty text
    /// (`ImageMetaDeriver` falls back to `"<format> image"`), so there is
    /// no config that routes a stub deriver in. The tests are in the same
    /// module as the struct, so its private fields are reachable.
    struct EmptyDeriver;

    impl crate::sources::parsers::Deriver for EmptyDeriver {
        fn derive(&self, _path: &Path) -> Result<crate::sources::parsers::ParseResult> {
            Ok(crate::sources::parsers::ParseResult::new("   \n  "))
        }
        fn parser_name(&self) -> &'static str {
            "EmptyDeriver"
        }
    }

    impl CacheableDeriver for EmptyDeriver {
        fn cache_key_parts(&self) -> (String, String, String, serde_json::Value) {
            (
                "test_empty".to_string(),
                "deterministic".to_string(),
                "1".to_string(),
                serde_json::json!({}),
            )
        }
    }

    /// A source whose `.blank` files are genuinely derive-routed.
    fn derived_src(dir: &std::path::Path, on_empty: OnEmpty) -> FilesSource {
        std::fs::write(dir.join("a.blank"), b"x").unwrap();
        let mut derive = BTreeMap::new();
        derive.insert("blank".to_string(), image_meta_cfg());
        let mut cfg = base_cfg(format!("{}/*.blank", dir.display()), Some(derive));
        cfg.on_empty = on_empty;
        let mut derived: HashMap<String, Rc<dyn CacheableDeriver>> = HashMap::new();
        derived.insert("blank".to_string(), Rc::new(EmptyDeriver));
        FilesSource {
            cfg,
            derived,
            // No cache: these tests must not touch the user's cache root.
            cache: None,
            derive_error: None,
        }
    }

    #[test]
    fn derive_routed_empty_errors_by_default() {
        let dir = tempfile::tempdir().unwrap();
        let err = derived_src(dir.path(), OnEmpty::Error)
            .iter_documents()
            .unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("a.blank"), "{msg}");
        assert!(msg.contains("EmptyDeriver"), "{msg}");
    }

    #[test]
    fn derive_routed_empty_is_omitted_under_skip() {
        let dir = tempfile::tempdir().unwrap();
        let docs = derived_src(dir.path(), OnEmpty::Skip)
            .iter_documents()
            .unwrap();
        assert!(docs.is_empty(), "{docs:?}");
    }

    #[test]
    fn derive_routed_empty_is_indexed_under_index() {
        let dir = tempfile::tempdir().unwrap();
        let docs = derived_src(dir.path(), OnEmpty::Index)
            .iter_documents()
            .unwrap();
        assert_eq!(docs.len(), 1);
        assert!(docs[0].content.trim().is_empty(), "{:?}", docs[0].content);
    }

    /// One normal, one zero-byte, one whitespace-only. No `derive:` block.
    fn text_corpus(dir: &std::path::Path) -> String {
        std::fs::write(dir.join("doc-normal.md"), b"# Heading\n\nreal prose\n").unwrap();
        std::fs::write(dir.join("doc-empty.md"), b"").unwrap();
        std::fs::write(dir.join("doc-ws.md"), b"   \n\n\t\n").unwrap();
        format!("{}/*.md", dir.display())
    }

    #[test]
    fn text_only_corpus_with_empty_files_ingests_under_default_config() {
        // THE regression test. Before the fix this returned
        // EmptyDerivationError on `doc-empty.md` and — because
        // `iter_documents` builds the whole Vec eagerly — nothing at all
        // was written, after `mode: overwrite` had already dropped the
        // populated target table.
        let dir = tempfile::tempdir().unwrap();
        let glob = text_corpus(dir.path());
        let src = FilesSource::new(base_cfg(glob, None)).unwrap();
        let docs = src.iter_documents().unwrap();
        assert_eq!(
            docs.len(),
            3,
            "{:?}",
            docs.iter().map(|d| &d.id).collect::<Vec<_>>()
        );
        let empty: Vec<_> = docs
            .iter()
            .filter(|d| d.content.trim().is_empty())
            .collect();
        assert_eq!(
            empty.len(),
            2,
            "the empty and whitespace-only docs must both survive"
        );
    }

    #[test]
    fn text_only_corpus_is_unaffected_by_every_on_empty_mode() {
        // `Skip` included: a user who set it for their images must not
        // silently lose empty text documents from the same cell.
        for mode in [OnEmpty::Error, OnEmpty::Skip, OnEmpty::Index] {
            let dir = tempfile::tempdir().unwrap();
            let glob = text_corpus(dir.path());
            let mut cfg = base_cfg(glob, None);
            cfg.on_empty = mode;
            let docs = FilesSource::new(cfg).unwrap().iter_documents().unwrap();
            assert_eq!(docs.len(), 3, "on_empty: {mode:?}");
        }
    }

    #[test]
    fn ambiguous_derive_block_is_rejected() {
        // Task 9 Correction 2: "png|jpg" and "jpg|webp" both claim "jpg" —
        // Python's dict-insertion-order last-writer-wins and Rust's
        // BTreeMap lexicographic-order last-writer-wins pick OPPOSITE
        // derivers for this identical config, so both must reject it
        // outright instead of picking either.
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("a.png"), b"x").unwrap();
        let mut derive = BTreeMap::new();
        derive.insert("png|jpg".to_string(), image_meta_cfg());
        derive.insert("jpg|webp".to_string(), image_meta_cfg());
        let cfg = base_cfg(format!("{}/*", dir.path().display()), Some(derive));

        let src = FilesSource::new(cfg).unwrap();
        let err = src.iter_documents().unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("jpg"), "{msg}");
        assert!(msg.contains("derive"), "{msg}");
    }

    #[test]
    fn non_overlapping_multi_key_derive_block_still_works() {
        let dir = tempfile::tempdir().unwrap();
        // The one file present has an extension that ISN'T derive-
        // configured, so it goes through the ordinary registry path —
        // this proves construction with a multi-key, non-overlapping
        // derive block doesn't spuriously error.
        std::fs::write(dir.path().join("a.txt"), b"hello").unwrap();
        let mut derive = BTreeMap::new();
        derive.insert("png|jpg".to_string(), image_meta_cfg());
        derive.insert("webp|gif".to_string(), image_meta_cfg());
        let cfg = base_cfg(format!("{}/*", dir.path().display()), Some(derive));

        let src = FilesSource::new(cfg).unwrap();
        let docs = src.iter_documents().unwrap();
        assert_eq!(docs.len(), 1);
        assert_eq!(docs[0].content, "hello");
    }
}
