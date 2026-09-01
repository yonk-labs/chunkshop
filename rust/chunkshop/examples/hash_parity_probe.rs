//! Cross-language check: does Rust's derivation_hash agree with Python's for
//! identical inputs? The cache key IS the provenance record, so a divergence
//! here means the two implementations silently maintain separate caches.
use chunkshop::sources::derivers::provenance::derivation_hash;
use serde_json::json;

fn main() {
    let params = json!({"top_k": 3, "min_score": 0.2, "vocabulary": ["a cat", "a dog"]});
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
}
