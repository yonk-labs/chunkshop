//! Tag-agreement measurement harness for `ClipTagsDeriver`.
//!
//! This is a MEASUREMENT tool, not part of the contract-parity test suite:
//! it runs the same vocabulary through the Rust deriver that a companion
//! ad-hoc Python script runs through the Python deriver, at `top_k=5`,
//! `min_score=0.0`, on the same three fixture images used in the CLIP
//! vision-tower divergence measurement. The question it answers is whether
//! that vision-vector divergence changes which TAGS get selected — the
//! only thing that reaches the database, since the vision vector itself is
//! never stored (see `clip_tags.rs`'s module doc).
//!
//! Run: `cargo run --example clip_tags_parity --features media,embedder-hub`
//!
//! Prints one JSON object with the full ranked (tag, score) list per image,
//! to be diffed by hand against the Python run's output.

use chunkshop::sources::derivers::clip_tags::{ClipTagsDeriver, TEXT_DEFAULT, VISION_DEFAULT};
use chunkshop::sources::parsers::Deriver;
use serde_json::json;
use std::path::PathBuf;

fn main() {
    let vocab: Vec<String> = vec![
        "pixel art game sprite",
        "a knight character",
        "a bar chart",
        "a line graph",
        "a revenue dashboard",
        "a software settings screen",
        "a user interface screenshot",
        "a photograph of a landscape",
        "a photograph of a cat",
        "an icon",
    ]
    .into_iter()
    .map(String::from)
    .collect();

    let deriver = ClipTagsDeriver::new(
        vocab,
        5,
        0.0,
        VISION_DEFAULT.to_string(),
        TEXT_DEFAULT.to_string(),
    )
    .expect("valid deriver config");

    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("repo root");
    let fixtures = [
        "docs/samples/images/sprite_knight.png",
        "docs/samples/images/chart_revenue.png",
        "docs/samples/images/screenshot_settings.png",
    ];

    let mut out = serde_json::Map::new();
    for rel in fixtures {
        let path = repo_root.join(rel);
        let result = deriver.derive(&path).expect("derive");
        let tags = result.metadata["clip_tags"].clone();
        let scores = result.metadata["clip_scores"].clone();
        let name = path.file_name().unwrap().to_string_lossy().to_string();

        println!("=== {name} ===");
        let tags_arr = tags.as_array().unwrap();
        let scores_arr = scores.as_array().unwrap();
        for (t, s) in tags_arr.iter().zip(scores_arr.iter()) {
            println!("  {s:.4}  {t}", s = s.as_f64().unwrap(), t = t.as_str().unwrap());
        }

        out.insert(name, json!({ "tags": tags, "scores": scores }));
    }

    println!();
    println!("JSON:");
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
