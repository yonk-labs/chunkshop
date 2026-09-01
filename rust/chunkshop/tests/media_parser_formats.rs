//! Format-by-format round-trip tests for the SP-3 parser layer, plus the
//! guard that adding this layer does not change output for existing
//! text-only YAMLs (SC-13's Rust equivalent).
//!
//! `.pptx` (the only format below still refused unconditionally, in every
//! build) is fed non-UTF-8 bytes below: if a future change reinstated a
//! text-decode fallback for it, an accidentally-decodable fixture (e.g.
//! plain ASCII) would let that regression pass silently. Invalid UTF-8
//! forces the failure to surface loudly instead. `.pdf`, `.xlsx`, and
//! `.docx` each moved off non-UTF-8 fixtures once their derivers landed
//! (Tasks 3, 4, and 5) — their non-`media`-build refusal tests still use
//! non-UTF-8 bytes for the same reason, but their `media`-build round-trip
//! tests exercise real extracted content instead.

use std::io::Write;

use chunkshop::config::{FilesSourceConfig, OnEmpty};
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
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
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
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let src = FilesSource::new(cfg);
    let docs = src.iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "# Title\n\nBody text.\n");
    assert_eq!(docs[0].id, "a");
}

// The case below (pptx) is UNCONDITIONAL — no `#[cfg(feature = "media")]`
// — because the refusal itself is unconditional (see `registry.rs`'s
// `UNIMPLEMENTED_FORMATS`): a default (non-`media`) build has no deriver
// for it either, so it must refuse too, not silently text-decode it.
// Coverage must match that guarantee in both build modes.
//
// `pdf` moved to `MEDIA_GATED_FORMATS` in Task 3, `xlsx` in Task 4, and
// `docx` in Task 5 (each has a real deriver, gated behind `media`), so
// their refusal behaviour now differs by build: a non-`media` build still
// refuses (via `MissingFeatureError`, naming the feature), but a `media`
// build actually derives text — see
// `pdf_without_media_feature_refuses_naming_the_feature` /
// `pdf_with_extractable_text_round_trips` and their xlsx/docx
// counterparts below.

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
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
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

#[cfg(not(feature = "media"))]
#[test]
fn docx_without_media_feature_refuses_naming_the_feature() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "letter.docx", NOT_UTF8);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.docx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("--features media"),
        "expected the error to name the feature to enable, got: {msg}"
    );
    assert!(msg.contains("docx"), "expected the error to name docx, got: {msg}");
}

#[cfg(not(feature = "media"))]
#[test]
fn xlsx_without_media_feature_refuses_naming_the_feature() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "sheet.xlsx", NOT_UTF8);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.xlsx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("--features media"),
        "expected the error to name the feature to enable, got: {msg}"
    );
    assert!(msg.contains("xlsx"), "expected the error to name xlsx, got: {msg}");
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
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("Hello PDF"),
        "got: {:?}",
        docs[0].content
    );
}

#[cfg(feature = "media")]
#[test]
fn xlsx_with_a_cell_round_trips() {
    // Fixture generated with openpyxl (`Workbook()`, `wb.active['A1'] =
    // 'hello xlsx'`, save) — `calamine` is read-only, so there is no
    // writer half to generate this fixture in Rust. Round-trip verified
    // against both openpyxl's own `iter_rows` and calamine's `XlsxDeriver`
    // before committing.
    const FIXTURE: &[u8] = include_bytes!("fixtures/one_cell.xlsx");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("one_cell.xlsx");
    std::fs::write(&p, FIXTURE).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.xlsx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("hello xlsx"),
        "got: {:?}",
        docs[0].content
    );
}

#[cfg(feature = "media")]
#[test]
fn docx_with_a_paragraph_round_trips() {
    // Fixture generated with python-docx (`Document()`, `add_paragraph`,
    // save) — `docx-rust` is read-only, so there is no writer half to
    // generate this fixture in Rust. Independently verified with
    // `unzip -p one_paragraph.docx word/document.xml`, which shows a
    // single `<w:t>hello docx</w:t>` run, and with python-docx's own
    // `d.paragraphs` read-back, before committing.
    const FIXTURE: &[u8] = include_bytes!("fixtures/one_paragraph.docx");
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("one_paragraph.docx");
    std::fs::write(&p, FIXTURE).unwrap();

    let cfg = FilesSourceConfig {
        glob: format!("{}/*.docx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(
        docs[0].content.contains("hello docx"),
        "got: {:?}",
        docs[0].content
    );
}

#[test]
fn on_empty_error_is_the_default() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.txt", b"   \n  ");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.txt", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Error,
        derive: None,
        cache: Default::default(),
    };
    let err = FilesSource::new(cfg).iter_documents().unwrap_err();
    assert!(format!("{err:#}").contains("a.txt"));
}

#[test]
fn on_empty_skip_omits_the_document() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.txt", b"   \n  ");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.txt", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Skip,
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 0);
}

#[test]
fn on_empty_index_keeps_the_empty_document() {
    // Not in the plan's Step 4 snippet, but the third enum variant deserves
    // direct coverage at the FilesSource level too (config-layer coverage
    // for `index` already exists in config.rs's on_empty_all_three_valid_values_parse).
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.txt", b"   \n  ");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.txt", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty: OnEmpty::Index,
        derive: None,
        cache: Default::default(),
    };
    let docs = FilesSource::new(cfg).iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert!(docs[0].content.trim().is_empty());
}
