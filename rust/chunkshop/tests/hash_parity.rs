//! Cross-language derivation-hash parity, enforced by CI.
//!
//! `examples/hash_parity_probe.rs` and `python/scripts/hash_parity_probe.py`
//! print these same values, but a probe with no assertions that nothing
//! invokes is not a check — the `min_score` f32/f64 divergence sat in the
//! shipped code while both probes ran green, because the Rust probe
//! hand-wrote an f64 JSON literal the production path never produced.
//!
//! The goldens below were taken from the PYTHON implementation, which is the
//! reference port. `python/tests/chunkshop/test_hash_parity.py` asserts the
//! byte-identical constants against Python's shipped code, so a drift in
//! either port turns exactly one suite red instead of both staying green.
//!
//! `derivation_hash`'s `engine` component legitimately differs across ports
//! ("python" vs "rust"), so every case here pins a fixed synthetic
//! (rung, model, version, engine) tuple: any difference can then only come
//! from params canonicalization, which must NOT differ.

use chunkshop::sources::derivers::cache::relative_key_path;
use chunkshop::sources::derivers::provenance::derivation_hash;
use serde_json::json;

const RUNG: &str = "clip_tags";
const MODEL: &str = "Qdrant/clip-ViT-B-32";
const VERSION: &str = "1.2.3";
const ENGINE: &str = "fastembed-rs";

fn h(params: &serde_json::Value) -> String {
    derivation_hash(RUNG, MODEL, VERSION, ENGINE, params)
}

#[test]
fn params_canonicalization_matches_python() {
    let cases: &[(&str, serde_json::Value, &str)] = &[
        (
            "clip",
            json!({"top_k": 3, "min_score": 0.2, "vocabulary": ["a cat", "a dog"]}),
            "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333",
        ),
        (
            // key-order independence: same content, different literal order
            "reord",
            json!({"vocabulary": ["a cat", "a dog"], "min_score": 0.2, "top_k": 3}),
            "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333",
        ),
        (
            // Python's json.dumps defaults to ensure_ascii=True; the shipped
            // canonicalizer must pass ensure_ascii=False to agree with
            // serde_json's raw UTF-8.
            "nonascii",
            json!({"top_k": 3, "vocabulary": ["café", "猫"]}),
            "db2ad85f884587c546d0e93a5929ec60bd3aef1c0adde1c039ad916427dfb90f",
        ),
        (
            "quote",
            json!({"text": "she said \"hello\""}),
            "f529dc6cc68239f2662589b5e07813db5fdce79596d82d4576b689be8e89ae59",
        ),
        (
            "backslash",
            json!({"path": "C:\\Users\\test"}),
            "1beb4c9d5f4dc489677edaa9581b2942049cba490f2c4af16c8dca22008d3dd6",
        ),
        (
            "control",
            json!({"text": "line one\nline two\ttabbed"}),
            "fa7eb09d9b698f1b2f4f50ffaf1fd4d69be4f0ded56ab79560329ff50afe964f",
        ),
        (
            "emoji",
            json!({"text": "launch \u{1F680} party \u{1F389}"}),
            "96575b79196cba8800c208d044092ac1b55a25ea4daa67975da69cdf463cbb22",
        ),
    ];
    for (label, params, expected) in cases {
        assert_eq!(&h(params), expected, "params case {label} diverged");
    }
}

#[test]
fn empty_params_hash_matches_python() {
    assert_eq!(
        derivation_hash("image_meta", "deterministic", "1", "rust", &json!({})),
        "cb43b14c693ffdd721e0b974d86387717658461bcf31e970890d86cf13305d91"
    );
}

#[test]
fn cache_key_paths_match_python() {
    let cases: &[(&str, &str, &str, Option<&str>, &str)] = &[
        (
            "simple",
            "c1",
            "d1",
            None,
            "c0/c025b0ee86847c86d25f74d8016c44c3db6d52378e26b5679ffeda0c9f98090a.json",
        ),
        (
            "empty",
            "",
            "",
            None,
            "96/96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7.json",
        ),
        (
            "nonascii",
            "café",
            "猫",
            None,
            "47/4710d2edf99c3298915df9374ddb41b07de179742d37f9a836669d4041375796.json",
        ),
        (
            "shalike",
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
            "e159c4e35c8cf84439ecaf417477d67316c5607a367f773753ae76c54cdf0d1d",
            None,
            "af/af799e370d018d0108143371ee19f309d4f674599ddaf2236baddf5674ddf08d.json",
        ),
        (
            // A deriver that IS path-dependent contributes a third component.
            "extra",
            "c1",
            "d1",
            Some("a chart revenue"),
            "03/033455ba3ef8e3338f24e29cb3a1a2bd3f2d52aa4543ff7c6d1067d64cc219e1.json",
        ),
    ];
    for (label, content, deriv, extra, expected) in cases {
        assert_eq!(
            &relative_key_path(content, deriv, *extra),
            expected,
            "cache-key case {label} diverged"
        );
    }
}

/// The case the probe was supposed to cover and did not: the params object
/// the SHIPPED `ClipTagsDeriver` actually produces, with every default taken
/// from the shipped config defaults rather than retyped as a literal.
///
/// This is the regression test for `min_score` being held as `f32`: an f32
/// `0.20` canonicalizes as `0.20000000298023224` once serde widens it to
/// f64, against Python's `0.2`.
#[test]
#[cfg(all(feature = "media", feature = "embedder-hub"))]
fn shipped_clip_tags_params_canonicalize_like_python() {
    use chunkshop::config::DeriverConfig;
    use chunkshop::sources::derivers::load_deriver;

    // Deserialized from the YAML shape a user writes, so `top_k`,
    // `min_score` and both model ids come from the shipped serde defaults.
    let dcfg: DeriverConfig =
        serde_yaml_ng::from_str("rung: clip_tags\nvocabulary: [\"a cat\", \"a dog\"]\n").unwrap();
    let deriver = load_deriver(&dcfg).unwrap();
    let (rung, _model, _version, params) = deriver.cache_key_parts();

    assert_eq!(rung, "clip_tags");
    assert_eq!(
        serde_json::to_string(&params).unwrap(),
        r#"{"min_score":0.2,"top_k":3,"vocabulary":["a cat","a dog"]}"#
    );
    assert_eq!(
        derivation_hash(&rung, MODEL, VERSION, ENGINE, &params),
        "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333"
    );
}
