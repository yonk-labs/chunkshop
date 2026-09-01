//! Extension -> Deriver dispatch. Mirrors `get_parser`/`DEFAULT_PARSERS` in
//! `python/src/chunkshop/sources/parsers/__init__.py`.
//!
//! Recognized-but-unavailable formats are a DELIBERATE gap, not an
//! oversight: PPTX has no Rust crate with the track record
//! `pdf-extract`/`calamine`/`docx-rust`/`html2text` have (scoping
//! `.superpowers/plan-d-scoping.md` §2 — `pptx-to-md`, `undoc`, and
//! `office_oxide`'s PPTX path are all young or low-traction, and a bad pick
//! risks 1-2 days of mid-implementation rework per scoping §5, risk 1).
//! PDF's deriver (`pdf-extract`, `media`-gated) landed in Task 3; XLSX's
//! (`calamine`, `media`-gated) landed in Task 4; DOCX's (`docx-rust`,
//! `media`-gated) landed in Task 5. Every recognized binary format gets a
//! named refusal rather than falling through to `TextDeriver` — the whole
//! point of this parser layer is that a binary file must never be
//! silently read as UTF-8 text.
//!
//! Two distinct "not available" states, tracked as two separate lists so a
//! format's state is explicit rather than inferred:
//! - `UNIMPLEMENTED_FORMATS` — no Rust deriver exists at all, in any build.
//!   Refuses with `UnsupportedFormatError`.
//! - `MEDIA_GATED_FORMATS` — a real deriver exists, but it's compiled in
//!   only behind the `media` feature. In a non-`media` build these must
//!   ALSO refuse (with `MissingFeatureError`, naming the feature to
//!   enable) — they must never fall through to `TextDeriver` just because
//!   *this particular build* lacks the feature.
//!
//! Landing a real deriver for a format MOVES that extension from
//! `UNIMPLEMENTED_FORMATS` to `MEDIA_GATED_FORMATS` (never delete it
//! outright) at the same time as adding the format's `media_deriver`
//! match arm. Deleting outright reopens the binary-decoded-as-text hole
//! in a non-`media` build: `media_deriver` returns `None` when the
//! feature is off, and if the format isn't in either list any more,
//! `get_deriver` falls all the way through to `TextDeriver`. Tasks 3, 4,
//! and 5 did this move for `pdf`, `xlsx`, and `docx` respectively.
//!
//! HTML is the one deliberate exception: it stays OUT of both lists,
//! falling back to `TextDeriver` in a non-`media` build. HTML source is
//! valid UTF-8 text, so reading raw markup as plain text is a *degraded but
//! non-corrupting* result — unlike a PDF's binary bytes, it never produces
//! garbage or a decode error. The invariant these two lists protect is
//! specifically about binary formats; HTML was never the risk this layer
//! exists to close.
//!
//! Follow-up for PPTX specifically: a short bakeoff task against real PPTX
//! fixtures, tracked in `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`.

use std::path::Path;

use super::text::TextDeriver;
use super::{Deriver, MissingFeatureError, ParseResult, UnsupportedFormatError};

/// Formats with NO Rust deriver at all, in any build. See module docs.
const UNIMPLEMENTED_FORMATS: &[&str] = &["pptx"];

/// Formats WITH a real deriver, but gated behind the `media` Cargo feature.
/// `pdf` moved in here in Task 3, `xlsx` in Task 4, `docx` in Task 5. See
/// module docs for the move-not-delete rule.
const MEDIA_GATED_FORMATS: &[&str] = &["pdf", "xlsx", "docx"];

#[cfg(feature = "media")]
fn media_deriver(ext: &str) -> Option<Box<dyn Deriver>> {
    match ext {
        "html" | "htm" => Some(Box::new(super::html::HtmlDeriver)),
        "pdf" => Some(Box::new(super::pdf::PdfDeriver)),
        "xlsx" => Some(Box::new(super::xlsx::XlsxDeriver)),
        "docx" => Some(Box::new(super::docx::DocxDeriver)),
        _ => None,
    }
}

#[cfg(not(feature = "media"))]
fn media_deriver(_ext: &str) -> Option<Box<dyn Deriver>> {
    None
}

/// Core dispatch, parameterized over the two refusal lists so either branch
/// can be exercised by a unit test with injected lists, independently of
/// whichever formats happen to occupy them today (see the `tests` module
/// below) — a branch no test reaches is exactly how the pdf/xlsx/docx hole
/// slipped through an earlier round of this fix.
fn dispatch(
    ext: &str,
    unimplemented: &'static [&'static str],
    media_gated: &'static [&'static str],
) -> Box<dyn Deriver> {
    if let Some(d) = media_deriver(ext) {
        return d;
    }
    if let Some(&format) = unimplemented.iter().find(|&&f| f == ext) {
        return Box::new(UnimplementedFormatDeriver { format });
    }
    if let Some(&format) = media_gated.iter().find(|&&f| f == ext) {
        return Box::new(MissingFeatureDeriver {
            format,
            feature: "media",
        });
    }
    Box::new(TextDeriver)
}

pub fn get_deriver(ext: &str) -> Box<dyn Deriver> {
    let ext = ext.to_lowercase();
    dispatch(&ext, UNIMPLEMENTED_FORMATS, MEDIA_GATED_FORMATS)
}

/// Python's parser CLASS NAME for an extension — the value Python stamps
/// into `metadata["parser"]` (`files.py`) and the one `Deriver::parser_name`
/// must reproduce.
///
/// Exists for the two STAND-IN derivers below, which cover several formats
/// each and so cannot answer with a single literal the way `PdfDeriver` and
/// friends do. The real derivers keep their own literal — `parser_name` is a
/// property of the type, not of a lookup table — and
/// `stand_in_names_agree_with_the_real_derivers` below is the tripwire that
/// keeps the two in step.
///
/// The `_ => "TextParser"` arm is not a guess: Python's `get_parser` returns
/// the shared `_TEXT` (`TextParser`) for every extension absent from
/// `DEFAULT_PARSERS`, so an unknown extension genuinely IS `TextParser` on
/// both sides.
pub(crate) fn python_parser_name(ext: &str) -> &'static str {
    match ext {
        "pdf" => "PDFParser",
        "docx" => "DOCXParser",
        "pptx" => "PPTXParser",
        "xlsx" => "XLSXParser",
        "html" | "htm" => "HTMLParser",
        _ => "TextParser",
    }
}

/// A recognized binary format with no Rust deriver at all yet. Refuses
/// loudly instead of falling through to `TextDeriver` — see module docs.
pub struct UnimplementedFormatDeriver {
    format: &'static str,
}

impl Deriver for UnimplementedFormatDeriver {
    fn parser_name(&self) -> &'static str {
        python_parser_name(self.format)
    }

    fn derive(&self, _path: &Path) -> anyhow::Result<ParseResult> {
        Err(UnsupportedFormatError {
            format: self.format,
        }
        .into())
    }
}

/// A recognized binary format WITH a real deriver, but this build was
/// compiled without the `media` feature that gates it. Refuses naming the
/// feature to enable, instead of falling through to `TextDeriver` — see
/// module docs.
pub struct MissingFeatureDeriver {
    format: &'static str,
    feature: &'static str,
}

impl Deriver for MissingFeatureDeriver {
    fn parser_name(&self) -> &'static str {
        python_parser_name(self.format)
    }

    fn derive(&self, _path: &Path) -> anyhow::Result<ParseResult> {
        Err(MissingFeatureError {
            format: self.format,
            feature: self.feature,
        }
        .into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn media_gated_format_without_media_feature_refuses_with_missing_feature_error() {
        // Uses a synthetic extension (not "pdf" — Task 3 gave `pdf` a real
        // `media_deriver` match arm, which would now take precedence over
        // the test-only override lists passed to `dispatch` here and mask
        // the branch this test exists to exercise). `pdf`'s own
        // media-gated-but-disabled behaviour is covered directly by
        // `pdf_without_media_feature_refuses_naming_the_feature` in
        // `tests/media_parser_formats.rs`.
        let d = dispatch("faketype", &[], &["faketype"]);
        let err = d.derive(Path::new("whatever.faketype")).unwrap_err();
        let msg = format!("{err:#}");
        assert!(
            msg.contains("--features media"),
            "expected the error to name the feature to enable, got: {msg}"
        );
    }

    /// `python_parser_name` (the stand-in derivers' lookup) and the real
    /// derivers' own `parser_name()` literals are two statements of the
    /// same fact. If a rename touched one and not the other, a file would
    /// be stamped `parser = "PDFParser"` in a `media` build and something
    /// else in a slim one, for the same extension. Compare them by DRIVING
    /// `get_deriver`, not by restating either table.
    #[cfg(feature = "media")]
    #[test]
    fn stand_in_names_agree_with_the_real_derivers() {
        for ext in ["pdf", "docx", "xlsx", "html", "htm", "txt", "md", "wat"] {
            assert_eq!(
                get_deriver(ext).parser_name(),
                python_parser_name(ext),
                "parser name for {ext:?} disagrees between the real deriver and \
                 the stand-in lookup"
            );
        }
    }

    /// Every name the lookup can return must be a class that actually
    /// exists in Python — the whole point of the key is that a user can
    /// compare the two ports' rows. Pinned as literals here deliberately:
    /// this is the one place the Python-side truth is written down, and it
    /// is checked against `python/src/chunkshop/sources/parsers/__init__.py`.
    #[test]
    fn lookup_returns_pythons_class_names() {
        assert_eq!(python_parser_name("pdf"), "PDFParser");
        assert_eq!(python_parser_name("docx"), "DOCXParser");
        assert_eq!(python_parser_name("pptx"), "PPTXParser");
        assert_eq!(python_parser_name("xlsx"), "XLSXParser");
        assert_eq!(python_parser_name("html"), "HTMLParser");
        assert_eq!(python_parser_name("htm"), "HTMLParser");
        // Python's `get_parser` falls back to the shared `_TEXT` instance
        // for anything not in `DEFAULT_PARSERS`.
        assert_eq!(python_parser_name("txt"), "TextParser");
        assert_eq!(python_parser_name("wat"), "TextParser");
    }
}
