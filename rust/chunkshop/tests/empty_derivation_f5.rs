//! Spec defect F5 — "empty-derivation is loud, not silent" (Rust port).
//!
//! A deriver that extracts no text from a file which DEMONSTRABLY HAS
//! content must fail loudly rather than index a silent empty document. The
//! canonical case is a scanned PDF: pages, no text layer, zero characters.
//!
//! The other half of this file is the regression guard for the opposite bug.
//! `on_empty` firing on the plain-text path aborted a whole cell on a
//! zero-byte `.md` — after `mode: overwrite` had already dropped the table.
//! The two cases must stay separated: a blank text file is LEGITIMATELY
//! empty (bytes in, no text out, nothing lost) and is indexed, while a PDF
//! with pages and no characters is a failed extraction and errors. The line
//! between them is an affirmative `ParseResult::had_content` claim from the
//! deriver, never "the text came out blank".
//!
//! Mirrors `python/tests/chunkshop/test_parser_empty_derivation_f5.py`
//! test-for-test, on the byte-identical `scanned.pdf` / `table_only.docx` /
//! `blank.xlsx` fixtures.

use std::io::Write;
use std::path::{Path, PathBuf};

use chunkshop::config::{FilesSourceConfig, OnEmpty};
use chunkshop::sources::files::FilesSource;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(name)
}

fn copy_fixture(dir: &Path, name: &str) {
    std::fs::copy(fixture(name), dir.join(name))
        .unwrap_or_else(|e| panic!("copying fixture {name}: {e}"));
}

fn write_temp(dir: &Path, name: &str, contents: &[u8]) {
    let mut f = std::fs::File::create(dir.join(name)).unwrap();
    f.write_all(contents).unwrap();
}

fn source(dir: &Path, pattern: &str, on_empty: OnEmpty) -> FilesSource {
    FilesSource::new(FilesSourceConfig {
        glob: format!("{}/{pattern}", dir.display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
        on_empty,
        derive: None,
        cache: Default::default(),
    })
    .unwrap()
}

// --- F5: loud by default ---------------------------------------------------

#[test]
fn scanned_pdf_fails_loudly_by_default() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "scanned.pdf");
    let err = source(dir.path(), "*.pdf", OnEmpty::Error)
        .iter_documents()
        .unwrap_err();
    let msg = format!("{err:#}");
    // Actionable: names the file, says what it HAD, and points at the real
    // remedy (preprocessing) before the escape hatch. Same needles as the
    // Python test asserts.
    for needle in [
        "scanned.pdf",
        "1 page(s)",
        "extracted no text",
        "no text layer",
        "does not OCR",
        "yonk-doctools",
        "source.on_empty='skip'",
    ] {
        assert!(msg.contains(needle), "missing {needle:?} in: {msg}");
    }
}

#[test]
fn docx_with_only_a_table_fails_loudly() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "table_only.docx");
    let err = source(dir.path(), "*.docx", OnEmpty::Error)
        .iter_documents()
        .unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("1 table(s)"), "{msg}");
    assert!(msg.contains("table_only.docx"), "{msg}");
}

#[test]
fn image_only_html_fails_loudly() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(
        dir.path(),
        "a.html",
        b"<html><body><img src='scan-01.png'><img src='scan-02.png'>\
          <svg viewBox='0 0 1 1'></svg></body></html>",
    );
    let err = source(dir.path(), "*.html", OnEmpty::Error)
        .iter_documents()
        .unwrap_err();
    let msg = format!("{err:#}");
    assert!(msg.contains("3 embedded element(s) (img, svg)"), "{msg}");
}

// --- the escape hatches ----------------------------------------------------

#[test]
fn on_empty_skip_omits_the_file_and_continues() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "scanned.pdf");
    copy_fixture(dir.path(), "hello.pdf");
    let docs = source(dir.path(), "*.pdf", OnEmpty::Skip)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1, "{docs:?}");
    assert_eq!(docs[0].title.as_deref(), Some("hello.pdf"));
}

#[test]
fn on_empty_index_restores_the_silent_empty_document() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "scanned.pdf");
    let docs = source(dir.path(), "*.pdf", OnEmpty::Index)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert!(docs[0].content.trim().is_empty());
}

// --- unaffected: text-bearing files ----------------------------------------

#[test]
fn text_bearing_pdf_is_unaffected() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "hello.pdf");
    let docs = source(dir.path(), "*.pdf", OnEmpty::Error)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert!(!docs[0].content.trim().is_empty(), "{:?}", docs[0].content);
    assert_eq!(docs[0].metadata["parser"], "PDFParser");
}

#[test]
fn text_bearing_docx_is_unaffected() {
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "one_paragraph.docx");
    let docs = source(dir.path(), "*.docx", OnEmpty::Error)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert!(!docs[0].content.trim().is_empty());
}

// --- REGRESSION GUARD: blank text files must stay indexable ----------------
//
// These are the tests that fail if someone re-widens the check from "the
// deriver claimed unextracted content" back to "the extracted text is blank".

#[test]
fn blank_text_files_index_under_every_on_empty_mode() {
    for mode in [OnEmpty::Error, OnEmpty::Skip, OnEmpty::Index] {
        let dir = tempfile::tempdir().unwrap();
        write_temp(dir.path(), "empty.md", b"");
        write_temp(dir.path(), "whitespace.md", b"   \n\t\n  ");
        write_temp(dir.path(), "real.md", b"# Real\n\nBody.\n");
        let docs = source(dir.path(), "*.md", mode)
            .iter_documents()
            .unwrap_or_else(|e| panic!("on_empty={mode:?} errored: {e:#}"));
        // `Skip` is the one mode that legitimately drops a document — but
        // only a derive-routed or deriver-flagged one, never a plain empty
        // text file.
        assert_eq!(docs.len(), 3, "on_empty={mode:?} dropped a plain text file");
        let mut titles: Vec<&str> = docs.iter().filter_map(|d| d.title.as_deref()).collect();
        titles.sort_unstable();
        assert_eq!(titles, ["empty.md", "real.md", "whitespace.md"]);
    }
}

#[test]
fn zero_byte_file_of_every_text_extension_indexes() {
    let dir = tempfile::tempdir().unwrap();
    let exts = ["txt", "md", "markdown", "rst", "log", "csv", "tsv"];
    for e in exts {
        write_temp(dir.path(), &format!("blank.{e}"), b"");
    }
    let docs = source(dir.path(), "blank.*", OnEmpty::Error)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), exts.len());
}

#[test]
fn blank_workbook_is_not_a_failure() {
    // XLSX deliberately makes no F5 claim — see `XlsxDeriver::derive`. A
    // workbook of empty cells is a blank template, not a failed extraction,
    // and neither port's library exposes non-cell content publicly. Raising
    // here would be a false positive, so it indexes.
    let dir = tempfile::tempdir().unwrap();
    copy_fixture(dir.path(), "blank.xlsx");
    let docs = source(dir.path(), "*.xlsx", OnEmpty::Error)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert!(docs[0].content.trim().is_empty());
}

#[test]
fn empty_html_with_no_embedded_content_is_not_a_failure() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.html", b"<html><body></body></html>");
    let docs = source(dir.path(), "*.html", OnEmpty::Error)
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert!(docs[0].content.trim().is_empty());
}
