//! Format-by-format round-trip tests for the SP-3 parser layer, plus the
//! guard that adding this layer does not change output for existing
//! text-only YAMLs (SC-13's Rust equivalent).
//!
//! Every `.pdf`/`.xlsx`/`.docx`/`.pptx` case below is deliberately fed
//! non-UTF-8 bytes: if a future change reinstated a text-decode fallback for
//! any of these formats, an accidentally-decodable fixture (e.g. plain
//! ASCII) would let that regression pass silently. Invalid UTF-8 forces the
//! failure to surface loudly instead.

use std::io::Write;

use chunkshop::config::FilesSourceConfig;
use chunkshop::sources::files::FilesSource;

/// Not valid UTF-8 in any encoding — guarantees `TextDeriver` would error
/// (or, worse, silently produce garbage) if the refusal path were ever
/// bypassed for a recognized-but-unimplemented format.
const NOT_UTF8: &[u8] = &[0xFF, 0xFE, 0x00, 0x01, 0x02];

fn write_temp(dir: &std::path::Path, name: &str, contents: &[u8]) -> std::path::PathBuf {
    let p = dir.join(name);
    let mut f = std::fs::File::create(&p).unwrap();
    f.write_all(contents).unwrap();
    p
}

fn assert_named_refusal(ext: &str, filename: &str) {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), filename, NOT_UTF8);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.{ext}", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("not yet implemented"),
        "expected a named refusal naming {ext}, got: {msg}"
    );
    assert!(
        msg.contains(ext),
        "expected the error to name the format {ext:?}, got: {msg}"
    );
}

#[test]
fn text_file_output_is_unchanged_by_the_parser_layer() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.md", b"# Title\n\nBody text.\n");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.md", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let docs = src.iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "# Title\n\nBody text.\n");
    assert_eq!(docs[0].id, "a");
}

// The three cases below (pptx/xlsx/docx) are UNCONDITIONAL — no
// `#[cfg(feature = "media")]` — because the refusal itself is unconditional
// (see `registry.rs`'s `UNIMPLEMENTED_FORMATS`): a default (non-`media`)
// build has no deriver for any of these formats either, so it must refuse
// them too, not silently text-decode them. Coverage must match that
// guarantee in both build modes.
//
// `pdf` moved to `MEDIA_GATED_FORMATS` in Task 3 (a real deriver exists,
// gated behind `media`), so its refusal behaviour now differs by build:
// a non-`media` build still refuses (via `MissingFeatureError`, naming the
// feature), but a `media` build actually derives text — see
// `pdf_without_media_feature_refuses_naming_the_feature` and
// `pdf_with_extractable_text_round_trips` below.

#[test]
fn pptx_is_refused_not_silently_decoded_as_text() {
    // Not a real PPTX (no crate to generate one with in this test) — see
    // `NOT_UTF8`'s doc comment for why this must NOT come back as a
    // "successful" garbled-text Document.
    assert_named_refusal("pptx", "deck.pptx");
}

#[cfg(not(feature = "media"))]
#[test]
fn pdf_without_media_feature_refuses_naming_the_feature() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "doc.pdf", NOT_UTF8);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.pdf", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("--features media"),
        "expected the error to name the feature to enable, got: {msg}"
    );
    assert!(msg.contains("pdf"), "expected the error to name pdf, got: {msg}");
}

#[test]
fn xlsx_is_refused_not_silently_decoded_as_text() {
    assert_named_refusal("xlsx", "sheet.xlsx");
}

#[test]
fn docx_is_refused_not_silently_decoded_as_text() {
    assert_named_refusal("docx", "letter.docx");
}

#[cfg(feature = "media")]
#[test]
fn pdf_with_extractable_text_round_trips() {
    // Fixture generated with pypdf (PdfWriter + a hand-built content
    // stream drawing "Hello PDF" in Helvetica) rather than hand-authored
    // bytes — see the fixture-generation note in the task report. Round-
    // trip verified against pypdf's own extract_text() before committing.
    const MINI_PDF: &[u8] = include_bytes!("fixtures/hello.pdf");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("hello.pdf");
    std::fs::write(&p, MINI_PDF).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.pdf", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("Hello PDF"),
        "got: {:?}",
        docs[0].content
    );
}
