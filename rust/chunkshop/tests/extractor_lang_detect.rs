//! Integration test for lang_detect extractor. Algorithm-only parity to
//! Python's langdetect (different underlying detector); test asserts the
//! shape and the right ISO 639-1 code on canonical English/Spanish samples.

use chunkshop::config::{ExtractorConfig, LangDetectExtractorConfig};
use chunkshop::extractor::build_extractor;

const EN_TEXT: &str = "The quick brown fox jumps over the lazy dog. \
                       This is a sample English sentence to identify.";
const ES_TEXT: &str = "El zorro marrón rápido salta sobre el perro perezoso. \
                       Esta es una oración de muestra en español para identificar.";

fn build() -> Box<dyn chunkshop::extractor::ExtractorImpl> {
    build_extractor(ExtractorConfig::LangDetect(LangDetectExtractorConfig {
        backend: "langdetect".into(),
    }))
    .expect("build lang_detect")
}

#[test]
fn english_text_returns_en() {
    let e = build();
    let r = e.extract(EN_TEXT).expect("extract");
    assert!(r.tags.is_empty(), "lang_detect emits no tags");
    let lang = r.metadata.get("language").and_then(|v| v.as_str());
    assert_eq!(lang, Some("en"), "expected English (en), got {lang:?}");
    let conf = r
        .metadata
        .get("language_confidence")
        .and_then(|v| v.as_f64())
        .expect("language_confidence is f64");
    assert!(conf >= 0.0 && conf <= 1.0, "confidence out of range: {conf}");
}

#[test]
fn spanish_text_returns_es() {
    let e = build();
    let r = e.extract(ES_TEXT).expect("extract");
    let lang = r.metadata.get("language").and_then(|v| v.as_str());
    assert_eq!(lang, Some("es"), "expected Spanish (es), got {lang:?}");
}

#[test]
fn empty_text_returns_null_language() {
    let e = build();
    let r = e.extract("").expect("extract");
    assert!(r.metadata.get("language").unwrap().is_null());
    assert_eq!(
        r.metadata.get("language_confidence").unwrap().as_f64(),
        Some(0.0)
    );
}

#[test]
fn whitespace_only_returns_null_language() {
    let e = build();
    let r = e.extract("   \n\t  ").expect("extract");
    assert!(r.metadata.get("language").unwrap().is_null());
}
