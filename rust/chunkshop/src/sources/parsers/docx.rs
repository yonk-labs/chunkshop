//! DOCX -> text via `docx-rust` 0.1.11 (crates.io name for PoiScript's
//! reader — NOT bokuweb's writer-only `docx-rs`, a common crate-name trap
//! per the scoping). Mirrors `python/src/chunkshop/sources/parsers/docx.py`:
//! every paragraph's text, newline-joined.
//!
//! Traversal shape confirmed by reading the vendored source
//! (`~/.cargo/registry/src/*/docx-rust-0.1.11/`), not just docs.rs:
//! `DocxFile::parse()` returns `Docx<'_>` with a `document: Document<'a>`
//! field (`src/docx.rs`); `Document::body: Body<'a>` (`src/document/document.rs`);
//! `Body::content: Vec<BodyContent<'a>>` (`src/document/body.rs`);
//! `BodyContent::Paragraph(Paragraph<'a>)`; `Paragraph::content:
//! Vec<ParagraphContent<'a>>` (`src/document/paragraph.rs`);
//! `ParagraphContent::Run(Run<'a>)`; `Run::content: Vec<RunContent<'a>>`
//! (`src/document/run.rs`); `RunContent::Text(Text<'a>)`; and `Text`'s
//! inner string field IS named `text` (`pub text: Cow<'a, str>` in
//! `src/document/text.rs`) — the plan's `.text` guess was correct.

use std::path::Path;

use docx_rust::document::{BodyContent, ParagraphContent, RunContent};
use docx_rust::DocxFile;

use super::{Deriver, ParseResult};

pub struct DocxDeriver;

impl Deriver for DocxDeriver {
    /// Python's class is `DOCXParser` (initialism uppercased).
    fn parser_name(&self) -> &'static str {
        "DOCXParser"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let docx_file = DocxFile::from_file(path)
            .map_err(|e| anyhow::anyhow!("failed to open DOCX {}: {e}", path.display()))?;
        let docx = docx_file
            .parse()
            .map_err(|e| anyhow::anyhow!("failed to parse DOCX {}: {e}", path.display()))?;

        let mut paragraphs = Vec::new();
        let mut tables = 0usize;
        for content in &docx.document.body.content {
            match content {
                BodyContent::Paragraph(p) => {
                    let mut text = String::new();
                    for pc in &p.content {
                        if let ParagraphContent::Run(run) = pc {
                            for rc in &run.content {
                                if let RunContent::Text(t) = rc {
                                    text.push_str(&t.text);
                                }
                            }
                        }
                    }
                    paragraphs.push(text);
                }
                BodyContent::Table(_) => tables += 1,
                _ => {}
            }
        }
        let text = paragraphs.join("\n");
        // The F5 claim here is deliberately NOT "the body has paragraphs".
        // Word gives a blank document a single empty paragraph, so paragraph
        // count would fire on a legitimately blank `.docx` — a false positive
        // of exactly the kind this whole change exists to avoid. TABLES are
        // the honest signal: this walk reads paragraph runs and nothing else,
        // so a document whose text all lives in tables is content we
        // demonstrably had and demonstrably failed to extract. Mirrors
        // Python's `len(d.tables)` check.
        if text.trim().is_empty() && tables > 0 {
            return Ok(ParseResult::unextractable(
                text,
                format!("{tables} table(s)"),
            ));
        }
        Ok(ParseResult::new(text))
    }
}
