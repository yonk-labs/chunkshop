//! Extension -> Deriver dispatch. Mirrors `get_parser`/`DEFAULT_PARSERS` in
//! `python/src/chunkshop/sources/parsers/__init__.py`.
//!
//! PPTX is a DELIBERATE gap, not an oversight: the scoping
//! (`.superpowers/plan-d-scoping.md` §2) found no Rust crate with the
//! track record `pdf-extract`/`calamine`/`docx-rust`/`html2text` have —
//! `pptx-to-md`, `undoc`, and `office_oxide`'s PPTX path are all young or
//! low-traction, and a bad pick risks 1-2 days of mid-implementation
//! rework (scoping §5, risk 1). Rather than pick blind, `.pptx` gets a
//! named refusal here. This is a decision to STOP the fallback chain, not
//! to fall through to `TextDeriver` — the whole point of this parser layer
//! is that a binary file must never be silently read as UTF-8 text.
//! Follow-up: a short bakeoff task against real PPTX fixtures, tracked in
//! `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`.

use std::path::Path;

use super::text::TextDeriver;
use super::{Deriver, ParseResult, UnsupportedFormatError};

/// Extensions Python parses today with a real Rust crate lined up
/// (Tasks 3-5). Checked before the "recognized but refused" list.
///
/// `pdf` / `xlsx` / `docx` arms are added by the task that creates each
/// deriver module (Tasks 3-5 respectively) — `mod.rs` only declares `pdf`,
/// `xlsx`, `docx` once their files exist, so a match arm referencing
/// `super::pdf::PdfDeriver` etc. before then would not compile.
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

pub fn get_deriver(ext: &str) -> Box<dyn Deriver> {
    let ext = ext.to_lowercase();
    if let Some(d) = media_deriver(&ext) {
        return d;
    }
    if ext == "pptx" {
        return Box::new(PptxRefusalDeriver);
    }
    Box::new(TextDeriver)
}

pub struct PptxRefusalDeriver;

impl Deriver for PptxRefusalDeriver {
    fn derive(&self, _path: &Path) -> anyhow::Result<ParseResult> {
        Err(UnsupportedFormatError { format: "pptx" }.into())
    }
}
