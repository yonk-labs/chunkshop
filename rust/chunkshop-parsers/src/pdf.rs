//! PDF via pdf-extract (pure Rust, on lopdf). Born-digital text only; no OCR.
use std::panic::{catch_unwind, AssertUnwindSafe};

use crate::ParseError;

pub(crate) fn parse(bytes: &[u8]) -> Result<String, ParseError> {
    // pdf-extract can panic on malformed input; a bad file must not kill the run.
    let pages = catch_unwind(AssertUnwindSafe(|| {
        pdf_extract::extract_text_from_mem_by_pages(bytes)
    }))
    .map_err(|_| ParseError::Skip("failed to parse PDF: parser panicked".into()))?
    .map_err(|e| ParseError::Skip(format!("failed to parse PDF (corrupt or encrypted): {e}")))?;
    let text = pages.join("\n");
    if text.trim().is_empty() {
        return Err(ParseError::Skip(
            "no extractable text layer (scanned/image-only?); OCR is out of scope".into(),
        ));
    }
    Ok(text)
}
