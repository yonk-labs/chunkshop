//! Extension -> Deriver dispatch. Mirrors `get_parser`/`DEFAULT_PARSERS` in
//! `python/src/chunkshop/sources/parsers/__init__.py`.
//!
//! Recognized-but-unavailable formats are a DELIBERATE gap, not an
//! oversight: PPTX has no Rust crate with the track record
//! `pdf-extract`/`calamine`/`docx-rust`/`html2text` have (scoping
//! `.superpowers/plan-d-scoping.md` §2 — `pptx-to-md`, `undoc`, and
//! `office_oxide`'s PPTX path are all young or low-traction, and a bad pick
//! risks 1-2 days of mid-implementation rework per scoping §5, risk 1).
//! PDF/XLSX/DOCX have a crate lined up (`pdf-extract`/`calamine`/
//! `docx-rust`, all `media`-gated) but their derivers are Tasks 3-5, not yet
//! landed on this branch. Every recognized binary format gets a named
//! refusal rather than falling through to `TextDeriver` — the whole point
//! of this parser layer is that a binary file must never be silently read
//! as UTF-8 text.
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
//! Carry-forward for Tasks 3, 4, 5: landing a real deriver for `pdf`/
//! `xlsx`/`docx` MOVES that extension from `UNIMPLEMENTED_FORMATS` to
//! `MEDIA_GATED_FORMATS` (never delete it outright) at the same time as
//! adding the format's `media_deriver` match arm. Deleting outright reopens
//! the binary-decoded-as-text hole in a non-`media` build: `media_deriver`
//! returns `None` when the feature is off, and if the format isn't in
//! either list any more, `get_deriver` falls all the way through to
//! `TextDeriver`.
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
const UNIMPLEMENTED_FORMATS: &[&str] = &["pdf", "xlsx", "docx", "pptx"];

/// Formats WITH a real deriver, but gated behind the `media` Cargo feature.
/// Empty today — no format has moved into it yet (Tasks 3-5 do that as
/// each deriver lands). See module docs for the move-not-delete rule.
const MEDIA_GATED_FORMATS: &[&str] = &[];

#[cfg(feature = "media")]
fn media_deriver(ext: &str) -> Option<Box<dyn Deriver>> {
    match ext {
        "html" | "htm" => Some(Box::new(super::html::HtmlDeriver)),
        _ => None,
    }
}

#[cfg(not(feature = "media"))]
fn media_deriver(_ext: &str) -> Option<Box<dyn Deriver>> {
    None
}

/// Core dispatch, parameterized over the two refusal lists so the
/// media-gated branch can be exercised by a unit test even while
/// `MEDIA_GATED_FORMATS` is empty in production (see the `tests` module
/// below) — a branch no test reaches is exactly how the pdf/xlsx/docx hole
/// slipped through the previous round of this fix.
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

/// A recognized binary format with no Rust deriver at all yet. Refuses
/// loudly instead of falling through to `TextDeriver` — see module docs.
pub struct UnimplementedFormatDeriver {
    format: &'static str,
}

impl Deriver for UnimplementedFormatDeriver {
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
        // `MEDIA_GATED_FORMATS` is empty today (see module docs — no format
        // has moved into it yet), so this exercises `dispatch` directly
        // with a test-only list. Proves the branch works correctly before
        // any real format depends on it, rather than leaving it present
        // but unreached until Task 3 lands and discovers a typo the hard
        // way.
        let d = dispatch("pdf", &[], &["pdf"]);
        let err = d.derive(Path::new("whatever.pdf")).unwrap_err();
        let msg = format!("{err:#}");
        assert!(
            msg.contains("--features media"),
            "expected the error to name the feature to enable, got: {msg}"
        );
    }
}
