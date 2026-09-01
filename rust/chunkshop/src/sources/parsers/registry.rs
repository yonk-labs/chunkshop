//! Extension -> Deriver dispatch. Mirrors `get_parser`/`DEFAULT_PARSERS` in
//! `python/src/chunkshop/sources/parsers/__init__.py`.
//!
//! Recognized-but-unimplemented formats are a DELIBERATE gap, not an
//! oversight: PPTX has no Rust crate with the track record
//! `pdf-extract`/`calamine`/`docx-rust`/`html2text` have (scoping
//! `.superpowers/plan-d-scoping.md` §2 — `pptx-to-md`, `undoc`, and
//! `office_oxide`'s PPTX path are all young or low-traction, and a bad pick
//! risks 1-2 days of mid-implementation rework per scoping §5, risk 1).
//! PDF/XLSX/DOCX have a crate lined up (`pdf-extract`/`calamine`/
//! `docx-rust`, all `media`-gated) but their derivers are Tasks 3-5, not yet
//! landed on this branch. Every one of the four gets a named refusal here
//! rather than falling through to `TextDeriver` — the whole point of this
//! parser layer is that a binary file must never be silently read as UTF-8
//! text. The refusal is deliberately UNCONDITIONAL (not `#[cfg(feature =
//! "media")]`): a default (non-`media`) build has no deriver for any of
//! these four formats either, so it must refuse them too, not silently
//! text-decode them.
//!
//! Carry-forward for Tasks 3, 4, 5: each task REPLACES its format's entry
//! in `UNIMPLEMENTED_FORMATS` (removing it from that list) with a real
//! `media_deriver` match arm — one line each, in the brief that creates the
//! deriver anyway. Do not add a new refusal; swap it out.
//! Follow-up for PPTX specifically: a short bakeoff task against real PPTX
//! fixtures, tracked in `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`.

use std::path::Path;

use super::text::TextDeriver;
use super::{Deriver, ParseResult, UnsupportedFormatError};

/// Extensions recognized as binary formats with no Rust deriver on this
/// branch yet. Checked after `media_deriver` (so a real deriver, once
/// landed, always wins) and before the `TextDeriver` fallback. Unconditional
/// — holds in every build, `media` feature on or off.
const UNIMPLEMENTED_FORMATS: &[&str] = &["pdf", "xlsx", "docx", "pptx"];

/// Extensions Python parses today with a real Rust crate lined up
/// (Tasks 3-5). Checked before `UNIMPLEMENTED_FORMATS`.
///
/// `pdf` / `xlsx` / `docx` arms are added by the task that creates each
/// deriver module (Tasks 3-5 respectively) — `mod.rs` only declares `pdf`,
/// `xlsx`, `docx` once their files exist, so a match arm referencing
/// `super::pdf::PdfDeriver` etc. before then would not compile. Until then
/// those three extensions fall through to `UNIMPLEMENTED_FORMATS` below.
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
    if let Some(&format) = UNIMPLEMENTED_FORMATS.iter().find(|&&f| f == ext) {
        return Box::new(UnimplementedFormatDeriver { format });
    }
    Box::new(TextDeriver)
}

/// A recognized binary format with no Rust deriver yet. Refuses loudly
/// instead of falling through to `TextDeriver` — see module docs.
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
