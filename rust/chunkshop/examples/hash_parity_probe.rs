//! Cross-language check: does Rust's derivation_hash agree with Python's for
//! identical inputs? The cache key IS the provenance record, so a divergence
//! here means the two implementations silently maintain separate caches.
use chunkshop::sources::derivers::cache::relative_key_path;
use chunkshop::sources::derivers::provenance::derivation_hash;
use serde_json::json;

/// The clip_tags params object as the SHIPPED deriver produces it.
///
/// This used to be a hand-written `json!({"top_k": 3, "min_score": 0.2, ...})`
/// — an f64 literal — which is a value the production path never emitted:
/// `ClipTagsDeriverConfig::min_score` was an `f32`, so the real
/// `cache_key_parts()` canonicalized the shipped default as
/// `0.20000000298023224` against Python's `0.2`. The probe hashed the
/// literal and reported agreement while the shipped code disagreed. Calling
/// the real code path is the whole point of a parity probe.
///
/// `media`-gated because `ClipTagsDeriver` is; without the feature the
/// probe still builds and still prints every other case. The assertion-
/// carrying version of this invariant lives in `tests/hash_parity.rs` (and
/// `python/tests/chunkshop/test_hash_parity.py`) so CI enforces it — a
/// probe nothing invokes cannot.
#[cfg(all(feature = "media", feature = "embedder-hub"))]
fn shipped_clip_params() -> Option<serde_json::Value> {
    use chunkshop::config::DeriverConfig;
    use chunkshop::sources::derivers::load_deriver;

    // Deserialized from the YAML shape a user writes, so top_k, min_score
    // and both model ids come from the shipped serde defaults.
    let dcfg: DeriverConfig =
        serde_yaml_ng::from_str("rung: clip_tags\nvocabulary: [\"a cat\", \"a dog\"]\n").ok()?;
    let deriver = load_deriver(&dcfg).ok()?;
    Some(deriver.cache_key_parts().3)
}

#[cfg(not(all(feature = "media", feature = "embedder-hub")))]
fn shipped_clip_params() -> Option<serde_json::Value> {
    None
}

fn main() {
    match shipped_clip_params() {
        Some(params) => {
            println!(
                "rust-clip-canonical {}",
                serde_json::to_string(&params).unwrap()
            );
            println!(
                "rust-clip  {}",
                derivation_hash(
                    "clip_tags",
                    "Qdrant/clip-ViT-B-32",
                    "1.2.3",
                    "fastembed-rs",
                    &params
                )
            );
        }
        None => eprintln!(
            "(rust-clip skipped: rebuild with --features media,embedder-hub)"
        ),
    }
    println!(
        "rust-empty {}",
        derivation_hash("image_meta", "deterministic", "1", "rust", &json!({}))
    );
    // key-order independence: same content, different literal order
    let reordered = json!({"vocabulary": ["a cat", "a dog"], "min_score": 0.2, "top_k": 3});
    println!(
        "rust-reord {}",
        derivation_hash(
            "clip_tags",
            "Qdrant/clip-ViT-B-32",
            "1.2.3",
            "fastembed-rs",
            &reordered
        )
    );

    // FIX 1 review cases: does canonicalization agree with Python beyond
    // plain ASCII? Python's json.dumps defaults to ensure_ascii=True
    // (emitting \uXXXX escapes); serde_json always emits raw UTF-8. All
    // cases below share the same (rung, model, version, engine) tuple as
    // "rust-clip" so any divergence can only come from params
    // canonicalization, not the other hash inputs.
    let non_ascii = json!({"top_k": 3, "vocabulary": ["café", "猫"]});
    println!(
        "rust-nonascii {}",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", &non_ascii)
    );

    let quote = json!({"text": "she said \"hello\""});
    println!(
        "rust-quote    {}",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", &quote)
    );

    let backslash = json!({"path": "C:\\Users\\test"});
    println!(
        "rust-backslash {}",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", &backslash)
    );

    let control = json!({"text": "line one\nline two\ttabbed"});
    println!(
        "rust-control  {}",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", &control)
    );

    let emoji = json!({"text": "launch \u{1F680} party \u{1F389}"});
    println!(
        "rust-emoji    {}",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", &emoji)
    );

    // Correction 2: the cache PATH KEY itself
    // (`sha256(content_hash + "\0" + derivation_hash)`, `key[..2]/key.json`
    // layout) — the most parity-critical line in Task 7. If this differs
    // from Python's for identical inputs, the two languages silently
    // maintain SEPARATE cache directories even when derivation_hash agrees,
    // which is exactly the scenario `engine` was folded into
    // derivation_hash (Task 6) to prevent. Companion cases, same order,
    // same labels (minus the "rust-"/"python-" prefix), live in
    // `python/scripts/hash_parity_probe.py` so the two outputs diff
    // line-for-line.
    println!(
        "rust-cachekey-simple    {}",
        relative_key_path("c1", "d1", None)
    );
    println!(
        "rust-cachekey-empty     {}",
        relative_key_path("", "", None)
    );
    println!(
        "rust-cachekey-nonascii  {}",
        relative_key_path("café", "猫", None)
    );
    println!(
        "rust-cachekey-shalike   {}",
        relative_key_path(
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
            "e159c4e35c8cf84439ecaf417477d67316c5607a367f773753ae76c54cdf0d1d",
            None
        )
    );

    cache_key_extra_cases();
}

/// The `cache_key_extra` half of the probe. `media`-gated because
/// `ImageMetaDeriver` is: without the feature the deriver does not exist,
/// so the probe still builds and still prints every `derivation_hash` and
/// plain cache-key case. Run with `--features media` to get these lines.
#[cfg(not(feature = "media"))]
fn cache_key_extra_cases() {
    eprintln!("(cache_key_extra cases skipped: rebuild with --features media)");
}

#[cfg(feature = "media")]
fn cache_key_extra_cases() {
    use std::path::Path;

    use chunkshop::sources::derivers::image_meta::ImageMetaDeriver;
    use chunkshop::sources::parsers::CacheableDeriver;

    // The third key component (`cache_key_extra`), added to fix the silent
    // path-collision data-corruption bug. Two byte-identical images with
    // different names share `content_hash` AND `derivation_hash` — only
    // this component separates them, so if it disagrees across languages
    // the two ports assign the same document DIFFERENT identities.
    //
    // The `extra` values below are produced by the SHIPPED
    // `ImageMetaDeriver::cache_key_extra`, not by a probe-local
    // reimplementation. `path_text` is pure path arithmetic and never
    // touches the filesystem, so the probe can use synthetic paths that
    // need not exist.
    let with_segments = ImageMetaDeriver { path_segments: true, segment_depth: 3 };
    let no_segments = ImageMetaDeriver { path_segments: false, segment_depth: 3 };
    let a = Path::new("/srv/corpus/assets/sprites/a_chart_revenue.png");
    let b = Path::new("/srv/corpus/assets/sprites/b_goblin_warrior.png");
    // A non-ASCII filename, per the fix's parity requirement: humanise()
    // splits on `_`/`-`/`.` only, so accented and CJK graphemes must pass
    // through both languages' string handling byte-identically.
    let nonascii = Path::new("/srv/corpus/données/café_猫-01.png");

    println!("rust-extra-a-noseg      {:?}", no_segments.cache_key_extra(a));
    println!("rust-extra-b-noseg      {:?}", no_segments.cache_key_extra(b));
    println!("rust-extra-a-seg        {:?}", with_segments.cache_key_extra(a));
    println!("rust-extra-nonascii-seg {:?}", with_segments.cache_key_extra(nonascii));

    println!(
        "rust-cachekey-a-noseg   {}",
        relative_key_path("c1", "d1", no_segments.cache_key_extra(a).as_deref())
    );
    println!(
        "rust-cachekey-b-noseg   {}",
        relative_key_path("c1", "d1", no_segments.cache_key_extra(b).as_deref())
    );
    println!(
        "rust-cachekey-a-seg     {}",
        relative_key_path("c1", "d1", with_segments.cache_key_extra(a).as_deref())
    );
    println!(
        "rust-cachekey-nonascii-seg {}",
        relative_key_path("c1", "d1", with_segments.cache_key_extra(nonascii).as_deref())
    );
    // The preserved property: a PURE deriver contributes None, so
    // byte-identical files keep ONE shared entry.
    println!(
        "rust-cachekey-pure      {}",
        relative_key_path("c1", "d1", None)
    );
}
