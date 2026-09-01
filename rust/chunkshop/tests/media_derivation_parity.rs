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

const IMG_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../docs/samples/images/sprite_knight.png"
);

fn fixture() -> serde_json::Value {
    let p = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/parity-fixtures/media_derivation_reference.json"
    );
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn key_set(v: &serde_json::Value) -> HashSet<String> {
    v.as_array()
        .expect("fixture field must be an array of key names")
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

/// Compare one rung's key sets against the Python golden fixture.
///
/// KEYS and FIELD NAMES only — never hash values. See this file's header.
fn assert_rung_keys_match(rung: &str, result: &chunkshop::sources::parsers::ParseResult) {
    let f = fixture();
    let expected_keys = key_set(&f[rung]["metadata_keys"]);
    let expected_derived_keys = key_set(&f[rung]["derived_keys"]);
    assert!(
        !expected_keys.is_empty(),
        "fixture has no {rung}.metadata_keys — a fixture that expects nothing \
         would make this assertion vacuous"
    );

    let actual_keys: HashSet<String> = result.metadata.keys().cloned().collect();
    let actual_derived_keys: HashSet<String> = result.metadata["derived"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();

    assert_eq!(
        actual_keys, expected_keys,
        "{rung}: top-level metadata key set must match Python"
    );
    assert_eq!(
        actual_derived_keys, expected_derived_keys,
        "{rung}: derived.* key set must match Python"
    );
}

#[test]
fn rung0_metadata_keys_match_python() {
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(IMG_PATH))
        .unwrap();
    assert_rung_keys_match("rung0", &result);
}

/// Rung 1 (CLIP zero-shot tags). Same contract as rung 0 — keys and
/// provenance FIELD NAMES, never hash VALUES.
///
/// Python stamps `engine="python"` and Rust stamps `engine="rust"`, and Task 6
/// deliberately made `engine` participate in the derivation hash. The two
/// languages therefore produce DIFFERENT `derivation_hash`/`params_hash`
/// values for byte-identical config, by design: that is exactly what lets both
/// share one cache directory without either serving the other's derivations as
/// its own. `derivation_hash_differs_by_engine_by_design` below pins that
/// property positively. Nothing in this file may ever assert the two hashes
/// are equal.
///
/// `#[ignore]`d because it loads ~0.6GB of CLIP weights, matching
/// `clip_tags.rs`'s own live test. Run with:
/// `cargo test --features media,embedder-hub --test media_derivation_parity -- --ignored`
#[cfg(feature = "embedder-hub")]
#[test]
#[ignore = "loads ~0.6GB of CLIP weights; run manually with --ignored"]
fn rung1_metadata_keys_match_python() {
    use chunkshop::sources::derivers::clip_tags::{ClipTagsDeriver, TEXT_DEFAULT, VISION_DEFAULT};

    let f = fixture();
    // Read the deriver params from the fixture rather than restating them, so
    // a fixture regenerated with a different vocabulary can never silently
    // compare against a Rust deriver configured the old way.
    let vocabulary: Vec<String> = f["rung1"]["vocabulary"]
        .as_array()
        .expect("fixture must record the vocabulary it was generated with")
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let top_k = f["rung1"]["top_k"].as_u64().unwrap() as usize;
    let min_score = f["rung1"]["min_score"].as_f64().unwrap() as f32;

    let d = ClipTagsDeriver::new(
        vocabulary,
        top_k,
        min_score,
        VISION_DEFAULT.to_string(),
        TEXT_DEFAULT.to_string(),
    )
    .unwrap();
    let result = d.derive(std::path::Path::new(IMG_PATH)).unwrap();

    assert_rung_keys_match("rung1", &result);
    assert_eq!(
        result.metadata["derived"]["rung"],
        serde_json::json!("clip_tags")
    );
    assert_eq!(
        result.metadata["derived"]["engine"],
        serde_json::json!("rust")
    );
    // Rung 1 is rung 0 plus tags — the rung-0 keys must survive, not be
    // replaced. (The key-set equality above would also catch a drop, but only
    // as an opaque set diff.)
    for k in [
        "width",
        "height",
        "image_format",
        "clip_tags",
        "clip_scores",
    ] {
        assert!(result.metadata.contains_key(k), "rung 1 lost/omitted {k:?}");
    }
}

/// The positive statement of the "never compare hashes" rule: identical rung,
/// model, version and params, differing only in `engine`, MUST hash
/// differently. If this ever passes-by-equality, Python and Rust would collide
/// in a shared cache directory and each could serve the other's derivations
/// under its own provenance stamp.
#[test]
fn derivation_hash_differs_by_engine_by_design() {
    use chunkshop::sources::derivers::provenance::derivation_hash;
    let params =
        serde_json::json!({"vocabulary": ["a knight sprite"], "top_k": 3, "min_score": 0.2});
    let py = derivation_hash(
        "clip_tags",
        "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text",
        "0.8.0",
        "python",
        &params,
    );
    let rs = derivation_hash(
        "clip_tags",
        "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text",
        "0.8.0",
        "rust",
        &params,
    );
    assert_ne!(
        py, rs,
        "engine must participate in the derivation hash (Task 6) — equal hashes \
         mean Python and Rust share cache keys and can overwrite each other"
    );
    // ...and the hash must still be stable for a fixed engine, or the cache
    // would never hit and the inequality above would be meaningless.
    assert_eq!(
        rs,
        derivation_hash(
            "clip_tags",
            "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text",
            "0.8.0",
            "rust",
            &params,
        )
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
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(IMG_PATH))
        .unwrap();
    assert_eq!(result.metadata["width"], serde_json::json!(128));
    assert_eq!(result.metadata["height"], serde_json::json!(128));
    assert_eq!(result.metadata["image_format"], serde_json::json!("PNG"));
    assert_eq!(result.metadata["image_mode"], serde_json::json!("RGB"));
    assert_eq!(result.metadata["has_alpha"], serde_json::json!(false));
}

#[test]
fn engine_field_names_the_implementation() {
    let result = ImageMetaDeriver::default()
        .derive(std::path::Path::new(IMG_PATH))
        .unwrap();
    assert_eq!(
        result.metadata["derived"]["engine"],
        serde_json::json!("rust")
    );
}

/// SC-10: content and metadata are separately addressable — `content_origin`
/// and `derived_rung` reach REAL SQL columns, queryable without a JSON
/// extraction, through the already-existing generic `promote_metadata`
/// mechanism. No new sink code exists or is needed for media.
///
/// SQLite on a tempdir, deliberately: per this repo's conventions, a
/// DB-touching test that needs infrastructure gets skipped in most runs and
/// therefore rots. This one needs nothing — it runs everywhere, every time.
/// (The mechanism under test is backend-generic: `PromoteColumn::column_name`
/// is the single source of truth for jsonb-path -> identifier in the Postgres,
/// MariaDB, ClickHouse and SQLite sinks alike.)
///
/// # What makes this go red
///
/// Dropping `content_origin`/`derived_rung` from `stamp_provenance`; renaming
/// them; `PromoteColumn::column_name` changing its identifier derivation;
/// `ensure_promote_columns` no longer running as part of `create_table`;
/// `write_document` no longer populating promoted columns; or the promoted
/// value ceasing to be readable as plain SQL. The final assertion also fails
/// if promoted columns start appearing for metadata keys that were never
/// requested — i.e. if the columns showed up for some reason other than the
/// `promote_metadata` config this test set.
#[cfg(feature = "sink")]
#[tokio::test]
async fn content_origin_and_derived_rung_reach_real_sql_columns() {
    use chunkshop::backends::SQLiteBackend;
    use chunkshop::chunker::Chunk;
    use chunkshop::config::SqliteTargetConfig;
    use chunkshop::sinks::{Sink, SqliteSink};
    use tempfile::tempdir;

    let dir = tempdir().unwrap();
    let env = format!("MEDIA_PROMOTE_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("promote.db").to_str().unwrap());

    // Built by DESERIALIZING YAML, not by struct literal: `PromoteColumn`'s
    // path/type allowlist validation lives only in its `Deserialize` impl, so
    // a struct literal would silently skip the very check that makes these
    // identifiers safe to interpolate into DDL.
    let yaml = format!(
        r#"
type: sqlite
dsn_env: {env}
database: ignored
table: chunks
mode: overwrite
source_tag: media_promote
hnsw: false
promote_metadata:
  - path: content_origin
    type: text
  - path: derived_rung
    type: text
"#
    );
    let raw: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
    // The `type:` tag belongs to the TargetConfig union; strip it to land on
    // the concrete SQLite config this test drives the sink with directly.
    let mut map = raw.as_mapping().unwrap().clone();
    map.remove(serde_yaml_ng::Value::String("type".into()));
    let cfg: SqliteTargetConfig =
        serde_yaml_ng::from_value(serde_yaml_ng::Value::Mapping(map)).unwrap();
    assert_eq!(cfg.promote_metadata.len(), 2);
    assert_eq!(cfg.promote_metadata[0].column_name(), "content_origin");
    assert_eq!(cfg.promote_metadata[1].column_name(), "derived_rung");

    // Real deriver output, not a hand-written metadata blob — so this breaks
    // if the deriver stops emitting the provenance fields at all.
    let derived = ImageMetaDeriver::default()
        .derive(std::path::Path::new(IMG_PATH))
        .unwrap();
    assert_eq!(
        derived.metadata["content_origin"],
        serde_json::json!("derived")
    );
    assert_eq!(
        derived.metadata["derived_rung"],
        serde_json::json!("image_meta")
    );

    let chunk = Chunk {
        doc_id: "sprite_knight".into(),
        seq_num: 0,
        original_content: derived.text.clone(),
        embedded_content: derived.text.clone(),
        metadata: serde_json::Value::Object(derived.metadata.clone()),
    };

    let backend = SQLiteBackend::new(env.clone());
    let sink = SqliteSink::new(cfg, backend, 4);
    sink.create_table().await.unwrap();
    sink.write_document(
        "sprite_knight",
        std::slice::from_ref(&chunk),
        &[vec![0.5_f32; 4]],
        &[vec![]],
    )
    .await
    .unwrap();

    let verify = SQLiteBackend::new(env);
    let conn = verify.connect().await.unwrap();
    let g = conn.lock().await;

    // 1. The columns exist as real columns in the table's schema.
    let mut stmt = g.prepare("PRAGMA table_info(chunks)").unwrap();
    let cols: Vec<String> = stmt
        .query_map([], |r: &rusqlite::Row| r.get::<_, String>(1))
        .unwrap()
        .map(|r| r.unwrap())
        .collect();
    assert!(
        cols.contains(&"content_origin".to_string()),
        "content_origin is not a column; got {cols:?}"
    );
    assert!(
        cols.contains(&"derived_rung".to_string()),
        "derived_rung is not a column; got {cols:?}"
    );

    // 2. They carry the deriver's values — read as plain SQL, no JSON path.
    let (origin, rung): (String, String) = g
        .query_row(
            "SELECT content_origin, derived_rung FROM chunks WHERE doc_id = ?",
            ["sprite_knight"],
            |r: &rusqlite::Row| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!(origin, "derived");
    assert_eq!(rung, "image_meta");

    // 3. They are usable as a WHERE predicate — the point of promoting them
    //    (SC-10: distinguish authored from derived without a join or a JSON
    //    extraction in the query the downstream tool already runs).
    let n_derived: i64 = g
        .query_row(
            "SELECT COUNT(*) FROM chunks WHERE content_origin = 'derived'",
            [],
            |r: &rusqlite::Row| r.get(0),
        )
        .unwrap();
    assert_eq!(n_derived, 1);
    let n_authored: i64 = g
        .query_row(
            "SELECT COUNT(*) FROM chunks WHERE content_origin = 'authored'",
            [],
            |r: &rusqlite::Row| r.get(0),
        )
        .unwrap();
    assert_eq!(n_authored, 0, "nothing authored was written");

    // 4. Control: metadata keys that were NOT promoted must NOT become
    //    columns. Without this, the assertions above would still pass if
    //    something started promoting every metadata key wholesale, and the
    //    test would no longer be testing `promote_metadata` at all.
    for not_promoted in ["width", "height", "image_format", "derived"] {
        assert!(
            !cols.contains(&not_promoted.to_string()),
            "{not_promoted:?} was promoted to a column without being configured; \
             columns are {cols:?}"
        );
    }
}
