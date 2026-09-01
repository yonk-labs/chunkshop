//! SC-12 contract-level parity: same input, same SCHEMA (metadata keys +
//! provenance shape) on both implementations. NOT value-level for the
//! `derived.*` provenance block specifically — "ts" (timestamp) and the
//! `derivation_hash`/`params_hash` legitimately differ (Task 9 Correction
//! 4: `engine` deliberately participates in the hash, so Python's
//! `engine="python"` and Rust's `engine="rust"` produce different
//! `params_hash`/cache keys for byte-identical config — that's BY DESIGN,
//! not a bug, and this file must never assert the two hashes are equal).
//!
//! `width`/`height`/`image_format`/`image_mode`/`has_alpha` on the
//! `sprite_knight.png` fixture used here (plain RGB PNG, no palette, no
//! alpha) ARE value-level asserted — those five fields are exact-parity
//! candidates per Task 9 Correction 1's measurement, and the plain-RGB
//! fixture is exactly the case where Rust and Python already agree
//! (`docs/samples/images/swatch_palette*.png` / `swatch_rgb.gif` are the
//! adversarial cases covered by `image_meta.rs`'s own unit tests instead,
//! since the golden fixture format here only records the key SET, not a
//! second language's per-fixture values).
//!
//! Gated on `media` for the whole file (not per-test): every test here
//! exercises `ImageMetaDeriver`, which needs the `image`/`png`/`gif`
//! crates the `media` feature gates — unlike `media_parser_formats.rs`,
//! there's no non-`media` refusal-path test in this file to keep
//! unconditional.
#![cfg(feature = "media")]

use std::collections::HashSet;

use chunkshop::sources::derivers::image_meta::ImageMetaDeriver;
use chunkshop::sources::parsers::Deriver;

#[test]
fn rung0_metadata_keys_match_python() {
    let fixture: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/parity-fixtures/media_derivation_reference.json").unwrap(),
    )
    .unwrap();
    let expected_keys: HashSet<String> = fixture["metadata_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let expected_derived_keys: HashSet<String> = fixture["derived_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();

    let img_path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../docs/samples/images/sprite_knight.png"
    );
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(img_path))
        .unwrap();
    let actual_keys: HashSet<String> = result.metadata.keys().cloned().collect();
    let actual_derived_keys: HashSet<String> = result.metadata["derived"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();

    assert_eq!(
        actual_keys, expected_keys,
        "top-level metadata key set must match Python"
    );
    assert_eq!(
        actual_derived_keys, expected_derived_keys,
        "derived.* key set must match Python"
    );
}

#[test]
fn rung0_values_match_python_for_a_plain_rgb_png() {
    // sprite_knight.png is plain RGB, no palette, no alpha — Task 9
    // Correction 1's measurement found Python and Rust already agree
    // value-for-value on this shape. Pinned here as regression coverage;
    // Python's own values were captured alongside the golden fixture via
    // `docs/superpowers/plans/2026-09-01-rust-media-parity-plan-d.md`
    // Task 9 Step 5's generation script (128x128 PNG, mode RGB, no alpha).
    let img_path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../docs/samples/images/sprite_knight.png"
    );
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(img_path))
        .unwrap();
    assert_eq!(result.metadata["width"], serde_json::json!(128));
    assert_eq!(result.metadata["height"], serde_json::json!(128));
    assert_eq!(result.metadata["image_format"], serde_json::json!("PNG"));
    assert_eq!(result.metadata["image_mode"], serde_json::json!("RGB"));
    assert_eq!(result.metadata["has_alpha"], serde_json::json!(false));
}

#[test]
fn engine_field_names_the_implementation() {
    let img_path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../docs/samples/images/sprite_knight.png"
    );
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(img_path))
        .unwrap();
    assert_eq!(result.metadata["derived"]["engine"], serde_json::json!("rust"));
}
