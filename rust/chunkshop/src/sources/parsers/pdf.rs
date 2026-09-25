//! PDF -> text via `pdf-extract` 0.12. Mirrors
//! `python/src/chunkshop/sources/parsers/pdf.py`'s pypdf usage: page-by-page
//! extraction, joined with newlines.
//!
//! Uses `extract_text_by_pages` rather than `extract_text`. Both were
//! available; the per-page form is now required, because closing spec defect
//! F5 needs the PAGE COUNT and not just the text. "A PDF that yielded no
//! characters" and "a PDF that HAS pages and yielded no characters" are
//! different facts, and only the second is a failure worth aborting a run
//! over. The change also brings the join to Python's exact shape
//! (`"\n".join(pages)`), which the whole-document call only approximated.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;

use super::{Deriver, ParseResult};

pub struct PdfDeriver;

impl Deriver for PdfDeriver {
    /// Python's class is `PDFParser` (initialism uppercased), not `PdfParser`.
    fn parser_name(&self) -> &'static str {
        "PDFParser"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        // GAP-004: `pdf-extract` 0.12 has roughly a hundred `unwrap()`/panic
        // sites across its parser, and a malformed PDF really can reach
        // one (verified against a one-byte-flipped fixture — see
        // `tests/media_parser_formats.rs`'s
        // `a_malformed_pdf_that_panics_pdf_extract_is_a_named_error_...`
        // test). `AssertUnwindSafe` is fine here: `path` is `&Path`
        // (shared, immutable) and nothing mutable crosses the boundary, so
        // there is no way for a caught panic to leave shared state
        // observably torn.
        let pages = catch_unwind(AssertUnwindSafe(|| pdf_extract::extract_text_by_pages(path)))
            .map_err(|payload| {
                anyhow::anyhow!(
                    "failed to parse PDF {}: parser panicked: {}",
                    path.display(),
                    panic_message(&payload)
                )
            })?
            .map_err(|e| anyhow::anyhow!("failed to parse PDF {}: {e}", path.display()))?;
        let text = pages.join("\n");
        if text.trim().is_empty() && !pages.is_empty() {
            // F5's canonical case: a scanned page with no text layer. The
            // file demonstrably holds content; we simply cannot read it.
            return Ok(ParseResult::unextractable(
                text,
                format!("{} page(s)", pages.len()),
            ));
        }
        Ok(ParseResult::new(text))
    }
}

/// Best-effort message extraction from a `catch_unwind` payload. Panics
/// carry either a `&'static str` (`panic!("literal")`) or an owned
/// `String` (`panic!("{}", x)`, `.unwrap()`'s default message, `.expect(..)`)
/// — those two cover the overwhelming majority in practice, including
/// pdf-extract's own `.unwrap()`/`.expect()` sites. Anything else falls
/// back to a fixed string rather than failing to build an error at all.
fn panic_message(payload: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "non-string panic payload".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Direct unit coverage of the boundary itself (the end-to-end
    /// `FilesSource` version lives in `tests/media_parser_formats.rs`,
    /// alongside the round-trip PDF tests).
    #[test]
    fn a_panicking_pdf_returns_a_named_error_instead_of_aborting() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("evil.pdf");
        std::fs::write(&p, include_bytes!("../../../tests/fixtures/pdf_extract_panics.pdf"))
            .unwrap();
        let err = PdfDeriver.derive(&p).unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("evil.pdf"), "{msg}");
        assert!(msg.contains("parser panicked"), "{msg}");
    }

    /// Garbage that pdf-extract rejects cleanly (no panic) must still come
    /// back as a named `Err`, never a `Result::Ok` with garbage text.
    #[test]
    fn garbage_bytes_are_a_named_error_not_a_panic() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("garbage.pdf");
        std::fs::write(&p, b"not a pdf at all, just random bytes 12345").unwrap();
        let err = PdfDeriver.derive(&p).unwrap_err();
        assert!(format!("{err:#}").contains("garbage.pdf"));
    }
}
