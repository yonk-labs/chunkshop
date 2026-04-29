//! Integration tests for none + composite extractors via the public
//! `build_extractor` factory. Stub-extractor errors at build are also covered.

use chunkshop::config::{
    CompositeExtractorConfig, ExtractorConfig, KeybertPhrasesExtractorConfig,
    NoneExtractorConfig,
};
use chunkshop::extractor::build_extractor;

#[test]
fn none_via_factory_returns_empty() {
    let e = build_extractor(ExtractorConfig::None(NoneExtractorConfig::default()))
        .expect("build none");
    let r = e.extract("hello world").expect("extract");
    assert!(r.tags.is_empty());
    assert!(r.metadata.is_empty());
}

#[test]
fn composite_of_two_nones_via_factory() {
    let cfg = ExtractorConfig::Composite(CompositeExtractorConfig {
        extractors: vec![
            ExtractorConfig::None(NoneExtractorConfig::default()),
            ExtractorConfig::None(NoneExtractorConfig::default()),
        ],
    });
    let e = build_extractor(cfg).expect("build composite");
    let r = e.extract("text").expect("extract");
    assert!(r.tags.is_empty());
    assert!(r.metadata.is_empty());
}

#[test]
fn composite_containing_keybert_errors_at_build() {
    let cfg = ExtractorConfig::Composite(CompositeExtractorConfig {
        extractors: vec![ExtractorConfig::KeybertPhrases(
            KeybertPhrasesExtractorConfig {
                top_k: 5,
                model_name: "x".into(),
                keyphrase_ngram_range: (1, 2),
            },
        )],
    });
    let err = match build_extractor(cfg) {
        Ok(_) => panic!("expected error"),
        Err(e) => e.to_string(),
    };
    assert!(err.contains("keybert_phrases"), "got: {err}");
    assert!(err.contains("Python-only"), "got: {err}");
}
