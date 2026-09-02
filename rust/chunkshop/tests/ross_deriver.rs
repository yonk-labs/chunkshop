//! Rung 5 (`ross`) — the real binary, and SC-12 contract-level parity with
//! Python.
//!
//! NOT gated on any Cargo feature, deliberately: rung 5 shells out and
//! compiles into every build including `--no-default-features`, so its
//! tests must run in the default `cargo test` too — the one place a
//! `--features media` gate would have hidden them.
//!
//! Every test here needs the `ross` binary and skips cleanly without it,
//! mirroring how this repo's DB tests skip on an unreachable DSN. The
//! never-a-dead-end (NN-5) tests that must run REGARDLESS live in
//! `sources::derivers::ross`'s unit tests, driven by stub scripts.
//!
//! On parity: this file asserts KEY SETS and the `engine`/`rung`/`model`
//! values, never the `derivation_hash` or `params_hash`. For the other
//! rungs those differ by design because `engine` differs; here `engine` is
//! `"ross"` in BOTH ports, so the hashes would in fact match — but
//! asserting that would pin an accidental property (both hosts happening to
//! have the same ross version installed) and there is precedent in this
//! repo for that test being written wrong. Key sets are the contract.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use chunkshop::config::{
    DerivationCacheSettings, DeriverConfig, FilesSourceConfig, OnEmpty, RossDeriverConfig,
};
use chunkshop::sources::derivers::ross::{ross_available, RossDeriver};
use chunkshop::sources::files::FilesSource;
use chunkshop::sources::parsers::{CacheableDeriver, Deriver};

fn samples() -> PathBuf {
    PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/samples/images"))
}

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

/// `true` when this machine can run the real binary. Prints the skip reason
/// the way the DB-gated tests do, so a skipped run is visible rather than
/// silently green.
fn have_ross() -> bool {
    if ross_available("ross") {
        return true;
    }
    eprintln!("SKIP: ross binary not on $PATH (https://github.com/yonk-labs/ross)");
    false
}

fn default_cfg() -> RossDeriverConfig {
    RossDeriverConfig {
        binary: "ross".to_string(),
        labels_file: None,
        llm: false,
        timeout_s: 120,
    }
}

fn deriver() -> RossDeriver {
    RossDeriver::new("ross".to_string(), None, false, 120).unwrap()
}

// --- SC-12 contract-level parity -----------------------------------------

#[test]
fn rung5_metadata_keys_match_python() {
    if !have_ross() {
        return;
    }
    let f = fixture();
    let expected = key_set(&f["rung5"]["metadata_keys"]);
    let expected_derived = key_set(&f["rung5"]["derived_keys"]);
    assert!(
        !expected.is_empty(),
        "fixture has no rung5.metadata_keys — regenerate with \
         tests/parity/generate_media_derivation_reference.py on a machine with ross"
    );

    let r = deriver().derive(&samples().join("sprite_knight.png")).unwrap();
    let actual: HashSet<String> = r.metadata.keys().cloned().collect();
    let actual_derived: HashSet<String> = r.metadata["derived"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();

    assert_eq!(
        actual, expected,
        "rung5: top-level metadata key set must match Python"
    );
    assert_eq!(
        actual_derived, expected_derived,
        "rung5: derived.* key set must match Python"
    );

    // engine/rung/model ARE value-asserted here, unlike the other rungs:
    // both ports shell out to the same binary, so these are genuinely
    // identical rather than legitimately divergent. `model_version` is not
    // — it is whatever ross the host has.
    for field in ["engine", "rung", "model"] {
        assert_eq!(
            r.metadata["derived"][field], f["rung5"][field],
            "rung5: derived.{field} must match Python"
        );
    }
    assert_eq!(r.metadata["derived"]["engine"], "ross");
}

#[test]
fn rung5_document_metadata_matches_python() {
    if !have_ross() {
        return;
    }
    let f = fixture();
    let dir = tempfile::tempdir().unwrap();
    let img = dir.path().join("sprite_knight.png");
    std::fs::copy(samples().join("sprite_knight.png"), &img).unwrap();

    let docs = documents(dir.path(), "*.png", default_cfg(), false);
    assert_eq!(docs.len(), 1);
    let md = docs[0].metadata.as_object().unwrap();

    let actual: HashSet<String> = md.keys().cloned().collect();
    assert_eq!(
        actual,
        key_set(&f["rung5_document"]["metadata_keys"]),
        "rung5_document: document metadata key set must match Python"
    );
    assert_eq!(
        md["parser"], f["rung5_document"]["parser"],
        "`parser` must be the value Python stamps (`parser.__class__.__name__`)"
    );
}

// --- the real binary ------------------------------------------------------

#[test]
fn provenance_records_the_probed_version_not_a_constant() {
    if !have_ross() {
        return;
    }
    let d = deriver();
    let md = d.derive(&samples().join("sprite_knight.png")).unwrap().metadata;
    assert_eq!(md["derived"]["model_version"], d.version());
    // ...and that really is what the binary reports.
    let out = std::process::Command::new("ross")
        .arg("--version")
        .output()
        .unwrap();
    let reported = String::from_utf8_lossy(&out.stdout);
    assert_eq!(d.version(), reported.split_whitespace().last().unwrap());
}

#[test]
fn a_tagged_image_puts_its_tags_in_both_text_and_metadata() {
    if !have_ross() {
        return;
    }
    let r = deriver().derive(&samples().join("chart_revenue.png")).unwrap();
    let tags = r.metadata["ross_tags"].as_array().unwrap();
    assert!(!tags.is_empty(), "chart_revenue.png is expected to tag");
    for t in tags {
        assert!(r.text.contains(t.as_str().unwrap()), "{}", r.text);
    }
    assert!(r.metadata["ross_tag_confidence"].is_number());
}

#[test]
fn a_non_media_file_is_a_named_error_not_an_empty_document() {
    if !have_ross() {
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("notes.png"); // .png extension, text bytes
    std::fs::write(&p, "this is not an image").unwrap();
    let msg = format!("{:#}", deriver().derive(&p).unwrap_err());
    assert!(msg.contains("magic bytes"), "{msg}");
}

/// ross sniffs by magic bytes, so audio needs no extra machinery at all —
/// the same deriver, the same config block, the same key set. chunkshop has
/// no in-process audio tagger in any plan; this is it.
#[test]
fn audio_derives_through_the_same_rung_with_the_same_key_set() {
    if !have_ross() {
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let wav = dir.path().join("beep_tone.wav");
    write_wav(&wav, 1.0);

    let image = deriver().derive(&samples().join("sprite_knight.png")).unwrap();
    let audio = deriver().derive(&wav).unwrap();

    let keys = |r: &chunkshop::sources::parsers::ParseResult| -> HashSet<String> {
        r.metadata.keys().cloned().collect()
    };
    assert_eq!(keys(&image), keys(&audio), "one key set across modalities");
    assert_eq!(audio.metadata["ross_type"], "audio");
    assert!(!audio.text.trim().is_empty());
    let secs = audio.metadata["ross_metadata"]["duration_s"]
        .as_f64()
        .expect("ffprobe should report duration_s");
    assert!((secs - 1.0).abs() < 0.2, "duration_s was {secs}");
}

#[test]
fn audio_and_images_ingest_through_one_files_source() {
    if !have_ross() {
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    std::fs::copy(
        samples().join("sprite_knight.png"),
        dir.path().join("sprite_knight.png"),
    )
    .unwrap();
    write_wav(&dir.path().join("alarm_beep.wav"), 1.0);

    let docs = documents(dir.path(), "*", default_cfg(), false);
    assert_eq!(docs.len(), 2, "one image + one wav through one derive block");
    for d in &docs {
        assert!(!d.content.trim().is_empty(), "{}: empty content", d.id);
        assert_eq!(d.metadata["derived_rung"], "ross");
        assert_eq!(d.metadata["derived"]["engine"], "ross");
    }
}

/// The pairing test for `cache_key_extra`.
///
/// ross's gated-tagger fallback derives its description from the BASENAME,
/// so two copies of ONE file under two names must derive DIFFERENTLY and
/// must not be served each other's text out of the cache. This is the exact
/// silent-corruption bug `cache_key_extra` was introduced to fix, asserted
/// for the rung that reintroduces the hazard.
#[test]
fn byte_identical_files_under_two_names_do_not_share_a_cache_entry() {
    if !have_ross() {
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let cache_root = dir.path().join("cache");
    let corpus = dir.path().join("corpus");
    std::fs::create_dir_all(&corpus).unwrap();
    let src = samples().join("sprite_knight.png");
    std::fs::copy(&src, corpus.join("sprite_knight.png")).unwrap();
    std::fs::copy(&src, corpus.join("haunted_castle.png")).unwrap();

    let mut cfg = default_cfg();
    cfg.binary = "ross".to_string();
    let docs = documents_with_cache(&corpus, "*.png", cfg, Some(cache_root.clone()));
    assert_eq!(docs.len(), 2);

    let text_of = |id: &str| {
        docs.iter()
            .find(|d| d.id == id)
            .unwrap_or_else(|| panic!("no doc {id}"))
            .content
            .to_lowercase()
    };
    assert!(text_of("sprite_knight").contains("knight"), "{:?}", text_of("sprite_knight"));
    assert!(
        text_of("haunted_castle").contains("castle"),
        "the second name was served the first's cached text: {:?}",
        text_of("haunted_castle")
    );

    // The cache really was used (two distinct entries, not zero).
    let entries = walk_json(&cache_root);
    assert_eq!(
        entries, 2,
        "expected two cache entries for two names over identical bytes"
    );

    // ...and a second run over the same corpus is served from it.
    let again = documents_with_cache(&corpus, "*.png", default_cfg(), Some(cache_root.clone()));
    assert_eq!(walk_json(&cache_root), 2, "a re-run must not add entries");
    assert_eq!(again.len(), 2);
}

// --- helpers --------------------------------------------------------------

fn documents(
    dir: &Path,
    pattern: &str,
    ross: RossDeriverConfig,
    cache: bool,
) -> Vec<chunkshop::sources::base::Document> {
    documents_with_cache(dir, pattern, ross, if cache { Some(dir.join("c")) } else { None })
}

fn documents_with_cache(
    dir: &Path,
    pattern: &str,
    ross: RossDeriverConfig,
    cache_root: Option<PathBuf>,
) -> Vec<chunkshop::sources::base::Document> {
    let mut derive = std::collections::BTreeMap::new();
    derive.insert(
        "png|jpg|webp|wav|mp3|mp4".to_string(),
        DeriverConfig::Ross(ross),
    );
    let cfg = FilesSourceConfig {
        glob: dir.join(pattern).display().to_string(),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: Some(derive),
        cache: DerivationCacheSettings {
            enabled: cache_root.is_some(),
            // Never the user's real cache dir: a run must not depend on
            // whatever a previous one left behind.
            root: cache_root.map(|p| p.display().to_string()),
        },
    };
    FilesSource::new(cfg).unwrap().iter_documents().unwrap()
}

fn walk_json(root: &Path) -> usize {
    fn rec(p: &Path, n: &mut usize) {
        let Ok(rd) = std::fs::read_dir(p) else { return };
        for e in rd.flatten() {
            let path = e.path();
            if path.is_dir() {
                rec(&path, n);
            } else if path.extension().map(|x| x == "json").unwrap_or(false) {
                *n += 1;
            }
        }
    }
    let mut n = 0;
    rec(root, &mut n);
    n
}

/// A mono 16-bit PCM 440Hz tone, written by hand so the test needs neither
/// ffmpeg nor a WAV crate.
fn write_wav(path: &Path, seconds: f64) {
    const RATE: u32 = 8000;
    let frames = (RATE as f64 * seconds) as u32;
    let data_len = frames * 2;
    let mut out = Vec::with_capacity(44 + data_len as usize);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + data_len).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes()); // PCM chunk size
    out.extend_from_slice(&1u16.to_le_bytes()); // PCM
    out.extend_from_slice(&1u16.to_le_bytes()); // mono
    out.extend_from_slice(&RATE.to_le_bytes());
    out.extend_from_slice(&(RATE * 2).to_le_bytes()); // byte rate
    out.extend_from_slice(&2u16.to_le_bytes()); // block align
    out.extend_from_slice(&16u16.to_le_bytes()); // bits
    out.extend_from_slice(b"data");
    out.extend_from_slice(&data_len.to_le_bytes());
    for i in 0..frames {
        let s = (20000.0
            * (2.0 * std::f64::consts::PI * 440.0 * i as f64 / RATE as f64).sin())
            as i16;
        out.extend_from_slice(&s.to_le_bytes());
    }
    std::fs::write(path, out).unwrap();
}

/// `cache_key_extra` is a per-deriver declaration, and this asserts it is
/// the one the cache actually consults — the trait method, not a
/// reimplementation inside the test.
#[test]
fn the_deriver_declares_the_basename_as_its_extra_cache_key_material() {
    if !have_ross() {
        return;
    }
    let d = deriver();
    assert_eq!(
        d.cache_key_extra(Path::new("/a/b/knight.png")),
        Some("knight.png".to_string())
    );
}
