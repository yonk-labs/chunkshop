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
}
