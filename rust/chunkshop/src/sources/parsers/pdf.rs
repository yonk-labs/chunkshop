//! PDF -> text via `pdf-extract` 0.12. Mirrors
//! `python/src/chunkshop/sources/parsers/pdf.py`'s pypdf usage: page-by-page
//! `extract_text()`, joined with newlines. `pdf-extract::extract_text(path)`
//! returns the whole-document text directly (verified via docs.rs 0.12.0);
//! Python joins per-page strings with "\n" — the exact page-boundary
//! whitespace may differ, but `FilesSource`'s `on_empty` check only cares
//! whether the result is non-blank, which both sides agree on for any PDF
//! with extractable text.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct PdfDeriver;

impl Deriver for PdfDeriver {
    /// Python's class is `PDFParser` (initialism uppercased), not `PdfParser`.
    fn parser_name(&self) -> &'static str {
        "PDFParser"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let text = pdf_extract::extract_text(path)
            .map_err(|e| anyhow::anyhow!("failed to parse PDF {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
