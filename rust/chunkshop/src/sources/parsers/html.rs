//! HTML -> text via html2text (html5ever-backed). Mirrors
//! `python/src/chunkshop/sources/parsers/html.py`'s BeautifulSoup
//! `get_text(separator="\n", strip=True)` call — same rough shape (block-
//! level text extraction), not guaranteed byte-identical wrapping since the
//! two libraries make different line-wrap decisions.

use std::path::Path;

use super::{Deriver, ParseResult};

/// Elements whose content is visual or externally-loaded — the HTML analogue
/// of a scanned PDF page. A page built only from these has content a text
/// extractor cannot reach, which is a derivation failure rather than an empty
/// document. Deliberately NOT "the body has any element at all":
/// `<html><body></body></html>` is legitimately empty and must stay quiet.
///
/// Kept in the same order as Python's `_EMBEDDED_TAGS` so the two ports name
/// the same set. Python asks BeautifulSoup's DOM; there is no DOM here, so
/// this scans the raw markup for an opening tag. The two agree on every
/// well-formed page — and this side only ever runs when extraction already
/// produced nothing, so a stray `<img` inside a comment or a string cannot
/// promote a text-bearing page into a failure.
const EMBEDDED_TAGS: [&str; 9] = [
    "img", "svg", "canvas", "video", "audio", "picture", "object", "embed", "iframe",
];

pub struct HtmlDeriver;

impl Deriver for HtmlDeriver {
    /// Python's class is `HTMLParser` (initialism uppercased).
    fn parser_name(&self) -> &'static str {
        "HTMLParser"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let bytes = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        // Wide width avoids html2text's word-wrap inserting line breaks that
        // Python's BeautifulSoup extraction never would.
        let text = html2text::from_read(bytes.as_slice(), 100_000)
            .map_err(|e| anyhow::anyhow!("parsing HTML {}: {e}", path.display()))?;
        if text.trim().is_empty() {
            if let Some(evidence) = embedded_evidence(&bytes) {
                return Ok(ParseResult::unextractable(text, evidence));
            }
        }
        Ok(ParseResult::new(text))
    }
}

/// `Some("N embedded element(s) (img, svg)")` when the markup holds at least
/// one element from `EMBEDDED_TAGS`. Counts occurrences and names the
/// distinct kinds in sorted order, matching Python's phrasing exactly.
fn embedded_evidence(bytes: &[u8]) -> Option<String> {
    let lower = String::from_utf8_lossy(bytes).to_lowercase();
    let mut total = 0usize;
    let mut kinds: Vec<&str> = Vec::new();
    for tag in EMBEDDED_TAGS {
        let needle = format!("<{tag}");
        let n = lower
            .match_indices(&needle)
            .filter(|(i, _)| {
                // `<img` must not match `<image`: require the next byte to be
                // a tag terminator or attribute separator.
                lower[i + needle.len()..]
                    .chars()
                    .next()
                    .is_none_or(|c| c.is_whitespace() || c == '>' || c == '/')
            })
            .count();
        if n > 0 {
            total += n;
            kinds.push(tag);
        }
    }
    if total == 0 {
        return None;
    }
    kinds.sort_unstable();
    Some(format!(
        "{total} embedded element(s) ({})",
        kinds.join(", ")
    ))
}
