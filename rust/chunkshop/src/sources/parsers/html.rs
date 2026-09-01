//! HTML -> text via html2text (html5ever-backed). Mirrors
//! `python/src/chunkshop/sources/parsers/html.py`'s BeautifulSoup
//! `get_text(separator="\n", strip=True)` call — same rough shape (block-
//! level text extraction), not guaranteed byte-identical wrapping since the
//! two libraries make different line-wrap decisions.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct HtmlDeriver;

impl Deriver for HtmlDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let bytes = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        // Wide width avoids html2text's word-wrap inserting line breaks that
        // Python's BeautifulSoup extraction never would.
        let text = html2text::from_read(bytes.as_slice(), 100_000)
            .map_err(|e| anyhow::anyhow!("parsing HTML {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
