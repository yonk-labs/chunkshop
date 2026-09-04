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

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct PdfDeriver;

impl Deriver for PdfDeriver {
    /// Python's class is `PDFParser` (initialism uppercased), not `PdfParser`.
    fn parser_name(&self) -> &'static str {
        "PDFParser"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let pages = pdf_extract::extract_text_by_pages(path)
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
